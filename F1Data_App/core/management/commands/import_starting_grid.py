# G:\Learning\F1Data\F1Data_App\core\management\commands\import_starting_grid.py
import requests
import json
import os
import time
from datetime import datetime, timezone, timedelta # Necessário para format_datetime_for_api_url se fosse usado

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings

# Importa os modelos necessários
from core.models import StartingGrid, Meetings # Importado Meetings para iterar por meeting_keys se necessário
from dotenv import load_dotenv
from update_token import update_api_token_if_needed
import pytz # Para manipulação de fusos horários (não usado diretamente neste, mas boa prática)

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

class Command(BaseCommand):
    help = 'Importa dados de starting grid da API OpenF1 de forma eficiente e os insere/atualiza na tabela startinggrid do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/starting_grid"

    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5
    BULK_SIZE = 5000

    def add_arguments(self, parser):
        parser.add_argument('--meeting_key', type=int, help='Especifica um Meeting Key para filtrar a importação (opcional). Se omitido, processa todos os dados retornados pela API.')
        parser.add_argument('--mode', choices=['I', 'U'], default='I',
                            help='Modo de operação: I=Insert apenas (padrão), U=Update (atualiza existentes e insere novos).')

    def add_warning(self, message):
        if not hasattr(self, 'warnings_count'):
            self.warnings_count = 0
            self.all_warnings_details = []
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    # get_config_value foi removido.

    def fetch_starting_grid_data(self, use_token=True):
        """
        Busca todos os dados de starting grid da API OpenF1 (sem filtros de meeting_key, esperando tudo).
        """
        url = self.API_URL
        
        headers = {"Accept": "application/json"}

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                self.add_warning("Token da API (OPENF1_API_TOKEN) não encontrado. Requisição será feita sem Authorization.")
            else:
                headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.add_warning("Uso do token desativado (use_token=False). Requisição será feita sem Authorization.")

        for attempt in range(self.API_MAX_RETRIES):
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                status_code = getattr(response, 'status_code', 'Unknown')
                error_msg = f"Erro {status_code} da API para URL: {url} - {e}"
                if status_code in [500, 502, 503, 504, 401, 403] and attempt < self.API_MAX_RETRIES - 1:
                    delay = self.API_RETRY_DELAY_SECONDS * (2 ** attempt)
                    self.add_warning(f"{error_msg}. Tentativa {attempt + 1}/{self.API_MAX_RETRIES}. Retentando em {delay} segundos...")
                    time.sleep(delay)
                else:
                    self.add_warning(f"Falha na busca da API após retries para {url}: {error_msg}")
                    return {"error_status": status_code,
                            "error_url": url,
                            "error_message": str(e)}
        self.add_warning(f"Falha na busca da API para {url}: Máximo de retries excedido.")
        return {"error_status": "Failed after retries", "error_url": url, "error_message": "Max retries exceeded."}

    def process_starting_grid_entry(self, sg_data_dict, mode):
        """
        Processa um único registro de starting grid, inserindo ou atualizando.
        Retorna 'inserted', 'updated', ou 'skipped_missing_data'/'skipped_invalid_value'.
        """
        meeting_key = sg_data_dict.get('meeting_key')
        session_key = sg_data_dict.get('session_key')
        position = sg_data_dict.get('position')
        driver_number = sg_data_dict.get('driver_number')
        lap_duration = sg_data_dict.get('lap_duration')

        # Validação crítica para campos NOT NULL na PK (meeting_key, session_key, driver_number)
        if any(val is None for val in [meeting_key, session_key, driver_number]):
            missing_fields = [k for k,v in {'meeting_key': meeting_key, 'session_key': session_key, 'driver_number': driver_number}.items() if v is None]
            self.add_warning(f"StartingGrid ignorado: dados obrigatórios ausentes para PK (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}). Faltando: {missing_fields}")
            return 'skipped_missing_data'

        # Tratamento de position (se for None, assume 0 se o modelo não permite null)
        if position is None:
            position = 0 # Default para 0 se a API enviar NULL e DDL for NOT NULL
            self.add_warning(f"Aviso: 'position' é null para Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}. Usando 0.")

        # Tratamento de lap_duration (numeric(8,3))
        lap_duration_parsed = None
        if lap_duration is not None:
            try:
                lap_duration_parsed = float(str(lap_duration).replace(',', '.').strip())
            except ValueError:
                self.add_warning(f"Aviso: Valor de 'lap_duration' '{lap_duration}' não é numérico para Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}. Ignorando o valor.")
                return 'skipped_invalid_value' # Um novo status para valores inválidos, mas não PK
        
        defaults = {
            'position': position,
            'lap_duration': lap_duration_parsed
        }
        
        try:
            if mode == 'U':
                obj, created = StartingGrid.objects.update_or_create(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    driver_number=driver_number,
                    defaults=defaults
                )
                return 'inserted' if created else 'updated'
            else: # mode == 'I'
                if StartingGrid.objects.filter(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    driver_number=driver_number
                ).exists():
                    return 'skipped'
                
                StartingGrid.objects.create(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    driver_number=driver_number,
                    **defaults
                )
                return 'inserted'
        except IntegrityError:
            self.add_warning(f"IntegrityError ao processar StartingGrid (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}). Provavelmente duplicata. Ignorando.")
            return 'skipped'
        except Exception as e:
            data_debug = f"Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}"
            self.add_warning(f"Erro FATAL ao processar registro de StartingGrid ({data_debug}): {e}. Dados API: {sg_data_dict}")
            raise


    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        self.warnings_count = 0
        self.all_warnings_details = []

        meeting_key_param = options.get('meeting_key')
        mode_param = options.get('mode', 'I')

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação/atualização de Starting Grid (ORM)..."))
        self.stdout.write(f"Parâmetros recebidos: meeting_key={meeting_key_param if meeting_key_param else 'Nenhum'}, mode={mode_param}")

        sg_found_api_total = 0
        sg_inserted_db = 0
        sg_updated_db = 0
        sg_skipped_db = 0 # Inclui skipped por conflito PK (mode I), erros de dados obrigatórios, etc.
        sg_skipped_invalid_value = 0 # Para casos como lap_duration não numérico.
        sg_skipped_missing_data = 0 # Para dados obrigatórios de PK ausentes
        api_call_errors = 0


        try:
            if use_api_token_flag:
                try:
                    self.stdout.write("Verificando e atualizando o token da API, se necessário...")
                    update_api_token_if_needed()
                    # CORREÇÃO CRÍTICA AQUI: Recarrega as variáveis de ambiente após possível atualização
                    load_dotenv(dotenv_path=ENV_FILE_PATH, override=True)
                    current_api_token = os.getenv('OPENF1_API_TOKEN')
                    if not current_api_token:
                        raise CommandError("Token da API (OPENF1_API_TOKEN) não disponível após verificação/atualização. Não é possível prosseguir com importação autenticada.")
                    self.stdout.write(self.style.SUCCESS("Token da API verificado/atualizado com sucesso."))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Falha ao verificar/atualizar o token da API: {e}. Prosseguindo sem usar o token da API."))
                    use_api_token_flag = False

            if not use_api_token_flag:
                self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False ou falha na obtenção do token). Buscando dados sem autenticação."))

            # A API de StartingGrid não aceita filtros por meeting_key.
            # Buscamos tudo e filtramos localmente, se meeting_key_param for fornecido.
            all_sg_from_api_raw = self.fetch_starting_grid_data(use_token=use_api_token_flag)
            
            if isinstance(all_sg_from_api_raw, dict) and "error_status" in all_sg_from_api_raw:
                api_call_errors += 1
                raise CommandError(f"Erro fatal ao buscar dados da API: {all_sg_from_api_raw['error_message']}. URL: {all_sg_from_api_raw['error_url']}")

            if not all_sg_from_api_raw:
                self.stdout.write(self.style.NOTICE("Nenhuma entrada de starting grid encontrada na API para importar. Encerrando."))
                return

            sg_found_api_total = len(all_sg_from_api_raw)

            # Filtrar os dados da API se um meeting_key específico foi fornecido
            sg_entries_to_process = []
            if meeting_key_param:
                sg_entries_to_process = [
                    entry for entry in all_sg_from_api_raw
                    if entry.get('meeting_key') == meeting_key_param
                ]
                self.stdout.write(f"Filtrando por meeting_key={meeting_key_param}. {len(sg_entries_to_process)} registros encontrados para este meeting.")
            else:
                sg_entries_to_process = all_sg_from_api_raw
                self.stdout.write(f"Processando todos os {len(sg_entries_to_process)} registros de starting grid da API (sem filtro de meeting_key).")

            if not sg_entries_to_process:
                self.stdout.write(self.style.WARNING(f"Nenhum registro de starting grid para processar após filtragem (se aplicável). Encerrando."))
                return

            # Se o modo é 'U' e um meeting_key específico foi fornecido, delete os dados existentes para aquele meeting_key
            if mode_param == 'U' and meeting_key_param:
                with transaction.atomic():
                    StartingGrid.objects.filter(
                        meeting_key=meeting_key_param
                    ).delete()
                    self.stdout.write(self.style.NOTICE(f"Registros de Starting Grid existentes deletados para Mtg {meeting_key_param} (modo U)."))
            
            # Para o modo 'I', prepare um conjunto de PKs existentes para evitar chamadas duplicadas
            existing_pks_for_mode_I = set()
            if mode_param == 'I':
                # Otimização para I: Pré-carrega as PKs existentes apenas para os meeting_keys que vamos processar
                # Isso evita muitas consultas `exists()` individuais.
                meeting_keys_in_batch = {entry.get('meeting_key') for entry in sg_entries_to_process if entry.get('meeting_key') is not None}
                for m_key in meeting_keys_in_batch:
                    # Fetch existing PKs only for relevant meeting_keys
                    existing_pks_for_mode_I.update(
                        StartingGrid.objects.filter(
                            meeting_key=m_key
                        ).values_list('meeting_key', 'session_key', 'driver_number')
                    )


            for i, sg_entry_dict in enumerate(sg_entries_to_process):
                # Opcional: mostrar progresso a cada X registros
                if (i + 1) % 100 == 0 or (i + 1) == len(sg_entries_to_process):
                    self.stdout.write(f"Processando registro {i+1}/{len(sg_entries_to_process)}...")
                
                try:
                    # Se o modo é 'I' e este registro já existe no banco, pule
                    if mode_param == 'I':
                        current_pk_tuple = (
                            sg_entry_dict.get('meeting_key'),
                            sg_entry_dict.get('session_key'),
                            sg_entry_dict.get('driver_number')
                        )
                        if current_pk_tuple in existing_pks_for_mode_I:
                            sg_skipped_db += 1
                            continue # Pula este registro, já existe e estamos em modo I

                    result = self.process_starting_grid_entry(sg_entry_dict, mode=mode_param)
                    if result == 'inserted':
                        sg_inserted_db += 1
                    elif result == 'updated':
                        sg_updated_db += 1
                    elif result == 'skipped': # De conflito de PK no update_or_create ou outros skips
                        sg_skipped_db += 1
                    elif result == 'skipped_missing_data':
                        sg_skipped_missing_data += 1
                    elif result == 'skipped_invalid_value':
                        sg_skipped_invalid_value += 1
                except Exception as process_e:
                    self.add_warning(f"Erro ao processar UM REGISTRO de Starting Grid: {process_e}. Dados: {sg_entry_dict}. Pulando.")

                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Processamento de Starting Grid concluído!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Starting Grid (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo do Processamento de Starting Grid (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Registros encontrados na API (total): {sg_found_api_total}"))
            self.stdout.write(self.style.SUCCESS(f"Registros processados localmente (após filtro de Mtg Key): {len(sg_entries_to_process) if 'sg_entries_to_process' in locals() else 0}"))
            self.stdout.write(self.style.SUCCESS(f"Registros novos inseridos no DB: {sg_inserted_db}"))
            self.stdout.write(self.style.SUCCESS(f"Registros existentes atualizados no DB: {sg_updated_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB em modo 'I' ou falhos): {sg_skipped_db}"))
            if sg_skipped_missing_data > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (dados obrigatórios ausentes): {sg_skipped_missing_data}"))
            if sg_skipped_invalid_value > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (valor inválido): {sg_skipped_invalid_value}"))
            if api_call_errors > 0:
                self.stdout.write(self.style.ERROR(f"Erros em chamadas à API: {api_call_errors}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de starting grid finalizada!"))