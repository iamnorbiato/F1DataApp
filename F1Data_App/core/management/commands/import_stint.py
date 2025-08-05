# G:\Learning\F1Data\F1Data_App\core\management\commands\import_stint.py
import requests
import json
from datetime import datetime, timezone, timedelta
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings

# Importa os modelos necessários
from core.models import Meetings, Stint, Sessions # Adicionado Sessions para consistência, embora não seja usado diretamente para filtrar stints
from dotenv import load_dotenv
from update_token import update_api_token_if_needed
import pytz # Para manipulação de fusos horários (caso necessário para formatação de data)

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

class Command(BaseCommand):
    help = 'Importa dados de stints da API OpenF1 e os insere na tabela stint do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/stints"

    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5
    BULK_SIZE = 5000

    def add_arguments(self, parser):
        parser.add_argument('--meeting_key', type=int, help='Especifica um Meeting Key para importar/atualizar (opcional).')
        parser.add_argument('--mode', choices=['I', 'U'], default='I',
                            help='Modo de operação: I=Insert apenas (padrão), U=Update (atualiza existentes e insere novos).')

    def add_warning(self, message):
        # Inicializa se não estiverem inicializados (para segurança, embora handle os inicialize)
        if not hasattr(self, 'warnings_count'):
            self.warnings_count = 0
            self.all_warnings_details = []
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    # get_config_value foi removido.

    def get_meeting_keys_to_fetch(self, meeting_key_filter=None, mode='I'):
        """
        Obtém os meeting_keys da tabela 'meetings' para buscar dados de stints.
        Filtra por meeting_key_filter se fornecido, e considera o modo para determinar
        se busca todos os meetings ou apenas os que ainda não têm dados de stint.
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Obtendo meeting_keys para buscar stints..."))

        all_meetings_keys_in_db = set(Meetings.objects.values_list('meeting_key', flat=True))
        
        # Filtra pelo meeting_key_filter se presente
        if meeting_key_filter:
            if meeting_key_filter in all_meetings_keys_in_db:
                meeting_keys_to_process = {meeting_key_filter}
                self.stdout.write(f"Meeting Key específico {meeting_key_filter} selecionado para processamento.")
            else:
                self.add_warning(f"Meeting Key {meeting_key_filter} não encontrado na tabela 'meetings'. Nenhuma ação será realizada.")
                return []
        else:
            meeting_keys_to_process = all_meetings_keys_in_db
            self.stdout.write(f"Nenhum meeting_key especificado. Todos os {len(all_meetings_keys_in_db)} meeting_keys da tabela 'meetings' serão considerados.")

        if mode == 'I':
            existing_stint_meeting_keys = set(Stint.objects.values_list('meeting_key', flat=True).distinct())
            meeting_keys_to_fetch_api = meeting_keys_to_process - existing_stint_meeting_keys
            self.stdout.write(f"Modo 'I': {len(meeting_keys_to_fetch_api)} meeting_keys serão considerados para busca de novos stints (ainda não existentes).")
        else: # mode == 'U'
            meeting_keys_to_fetch_api = meeting_keys_to_process
            self.stdout.write(f"Modo 'U': Todos os {len(meeting_keys_to_fetch_api)} meeting_keys relevantes serão considerados para atualização/inserção.")

        # Ordena para processamento consistente
        return sorted(list(meeting_keys_to_fetch_api))


    def fetch_stints_data(self, meeting_key, use_token=True):
        if not meeting_key:
            self.add_warning("meeting_key deve ser fornecido para buscar dados de stints da API.")
            return {"error_status": "InvalidParams", "error_message": "Missing meeting_key"}

        url = f"{self.API_URL}?meeting_key={meeting_key}"

        headers = {"Accept": "application/json"}

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                self.add_warning("Token da API (OPENF1_API_TOKEN) não encontrado. Requisição será feita sem Authorization.")
            else:
                headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.add_warning("Uso do token desativado. Requisição será feita sem Authorization.")

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

    def process_stint_entry(self, stint_data_dict, mode):
        """
        Processa um único registro de stint, inserindo ou atualizando.
        Retorna 'inserted', 'updated', ou 'skipped_missing_data'.
        """
        meeting_key = stint_data_dict.get('meeting_key')
        session_key = stint_data_dict.get('session_key')
        stint_number = stint_data_dict.get('stint_number')
        driver_number = stint_data_dict.get('driver_number')
        
        lap_start = stint_data_dict.get('lap_start')
        lap_end = stint_data_dict.get('lap_end')
        compound = stint_data_dict.get('compound')
        tyre_age_at_start = stint_data_dict.get('tyre_age_at_start')

        # Validação crítica para campos NOT NULL na PK
        if any(val is None for val in [meeting_key, session_key, stint_number, driver_number]):
            missing_fields = [k for k,v in {'meeting_key': meeting_key, 'session_key': session_key, 'stint_number': stint_number, 'driver_number': driver_number}.items() if v is None]
            self.add_warning(f"Stint ignorado: dados obrigatórios ausentes para PK (Mtg {meeting_key}, Sess {session_key}, Stint {stint_number}, Driver {driver_number}). Faltando: {missing_fields}")
            return 'skipped_missing_data'

        defaults = {
            'lap_start': lap_start,
            'lap_end': lap_end,
            'compound': compound,
            'tyre_age_at_start': tyre_age_at_start
        }

        try:
            if mode == 'U':
                obj, created = Stint.objects.update_or_create(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    stint_number=stint_number,
                    driver_number=driver_number,
                    defaults=defaults
                )
                return 'inserted' if created else 'updated'
            else: # mode == 'I'
                if Stint.objects.filter(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    stint_number=stint_number,
                    driver_number=driver_number
                ).exists():
                    return 'skipped'
                
                Stint.objects.create(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    stint_number=stint_number,
                    driver_number=driver_number,
                    **defaults
                )
                return 'inserted'
        except IntegrityError:
            self.add_warning(f"IntegrityError ao processar stint (Mtg {meeting_key}, Sess {session_key}, Stint {stint_number}, Driver {driver_number}). Provavelmente duplicata. Ignorando.")
            return 'skipped'
        except Exception as e:
            self.add_warning(f"Erro FATAL ao processar stint (Mtg {meeting_key}, Sess {session_key}, Stint {stint_number}, Driver {driver_number}): {e}. Dados API: {stint_data_dict}")
            raise

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        self.warnings_count = 0
        self.all_warnings_details = []

        meeting_key_param = options.get('meeting_key')
        mode_param = options.get('mode', 'I')

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação/atualização de Stints (ORM)..."))
        self.stdout.write(f"Parâmetros recebidos: meeting_key={meeting_key_param if meeting_key_param else 'Nenhum'}, mode={mode_param}")

        stints_found_api_total = 0
        stints_inserted_db = 0
        stints_updated_db = 0
        stints_skipped_db = 0
        stints_skipped_missing_data = 0
        api_call_errors = 0


        try:
            if use_api_token_flag:
                try:
                    self.stdout.write("Verificando e atualizando o token da API, se necessário...")
                    update_api_token_if_needed()
                    # >>> CORREÇÃO CRÍTICA AQUI: Recarrega as variáveis de ambiente após possível atualização <<<
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

            # Obter os meeting_keys para buscar
            meeting_keys_to_fetch = self.get_meeting_keys_to_fetch(
                meeting_key_filter=meeting_key_param,
                mode=mode_param
            )

            if not meeting_keys_to_fetch:
                self.stdout.write(self.style.NOTICE("Nenhum meeting_key encontrado para buscar dados de stints. Encerrando."))
                return

            self.stdout.write(self.style.SUCCESS(f"Total de {len(meeting_keys_to_fetch)} meeting_keys elegíveis para busca na API."))

            for i, current_meeting_key in enumerate(meeting_keys_to_fetch):
                self.stdout.write(f"Buscando e processando stints para Meeting {i+1}/{len(meeting_keys_to_fetch)}: Mtg {current_meeting_key}...")

                stints_data_from_api = self.fetch_stints_data(
                    meeting_key=current_meeting_key,
                    use_token=use_api_token_flag
                )

                if isinstance(stints_data_from_api, dict) and "error_status" in stints_data_from_api:
                    api_call_errors += 1
                    self.add_warning(f"Erro na API para Mtg {current_meeting_key}: {stints_data_from_api['error_message']}")
                    continue

                if not stints_data_from_api:
                    self.stdout.write(self.style.WARNING(f"Nenhum registro de stints encontrado na API para Mtg {current_meeting_key}."))
                    continue

                stints_found_api_total += len(stints_data_from_api)
                self.stdout.write(f"Encontrados {len(stints_data_from_api)} registros de stints para Mtg {current_meeting_key}. Processando...")

                # Em modo 'U', a melhor estratégia para 'stints' é deletar todos os stints existentes para esse meeting_key
                # e depois inserir os novos, garantindo consistência com a API.
                if mode_param == 'U':
                    with transaction.atomic():
                        Stint.objects.filter(
                            meeting_key=current_meeting_key
                        ).delete()
                        self.stdout.write(f"Registros de stints existentes deletados para Mtg {current_meeting_key}.")
                
                for stint_entry_dict in stints_data_from_api:
                    try:
                        result = self.process_stint_entry(stint_entry_dict, mode=mode_param)
                        if result == 'inserted':
                            stints_inserted_db += 1
                        elif result == 'updated':
                            stints_updated_db += 1
                        elif result == 'skipped':
                            stints_skipped_db += 1
                        elif result == 'skipped_missing_data':
                            stints_skipped_missing_data += 1
                    except Exception as stint_process_e:
                        self.add_warning(f"Erro ao processar UM REGISTRO de stint (Mtg {current_meeting_key}, Sess {stint_entry_dict.get('session_key', 'N/A')}, Stint {stint_entry_dict.get('stint_number', 'N/A')}, Driver {stint_entry_dict.get('driver_number', 'N/A')}): {stint_process_e}. Pulando para o próximo.")


                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Processamento de Stints concluído!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação/atualização (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação/atualização de Stints (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo do Processamento de Stints (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Meetings processados: {len(meeting_keys_to_fetch) if 'meeting_keys_to_fetch' in locals() else 0}"))
            self.stdout.write(self.style.SUCCESS(f"Registros de Stints encontrados na API (total): {stints_found_api_total}"))
            self.stdout.write(self.style.SUCCESS(f"Registros novos inseridos no DB: {stints_inserted_db}"))
            self.stdout.write(self.style.SUCCESS(f"Registros existentes atualizados no DB: {stints_updated_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB em modo 'I'): {stints_skipped_db}"))
            if stints_skipped_missing_data > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (dados obrigatórios ausentes): {stints_skipped_missing_data}"))
            if api_call_errors > 0:
                self.stdout.write(self.style.ERROR(f"Erros em chamadas à API: {api_call_errors}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de stints finalizada!"))