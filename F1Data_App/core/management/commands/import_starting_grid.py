# G:\Learning\F1Data\F1Data_App\core\management\commands\import_starting_grid.py
import requests
import json
import os
import time
from datetime import datetime, timezone, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings

# Importa os modelos necessários
from core.models import StartingGrid, Meetings
from dotenv import load_dotenv

# Importe o novo módulo de gerenciamento de token
from .token_manager import get_api_token

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

class Command(BaseCommand):
    help = 'Importa dados de starting grid da API OpenF1 de forma eficiente e os insere/atualiza na tabela startinggrid do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/starting_grid"

    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5
    BULK_SIZE = 5000
    warnings_count = 0

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

    def fetch_starting_grid_data(self, api_token=None):
        url = self.API_URL
        
        headers = {"Accept": "application/json"}

        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.add_warning("Uso do token desativado ou token não disponível. Requisição será feita sem Authorization.")

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
        meeting_key = sg_data_dict.get('meeting_key')
        session_key = sg_data_dict.get('session_key')
        position = sg_data_dict.get('position')
        driver_number = sg_data_dict.get('driver_number')
        lap_duration = sg_data_dict.get('lap_duration')

        if any(val is None for val in [meeting_key, session_key, driver_number]):
            missing_fields = [k for k,v in {'meeting_key': meeting_key, 'session_key': session_key, 'driver_number': driver_number}.items() if v is None]
            self.add_warning(f"StartingGrid ignorado: dados obrigatórios ausentes para PK (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}). Faltando: {missing_fields}")
            return 'skipped_missing_data'

        if position is None:
            position = 0
            self.add_warning(f"Aviso: 'position' é null para Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}. Usando 0.")

        lap_duration_parsed = None
        if lap_duration is not None:
            try:
                lap_duration_parsed = float(str(lap_duration).replace(',', '.').strip())
            except ValueError:
                self.add_warning(f"Aviso: Valor de 'lap_duration' '{lap_duration}' não é numérico para Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}. Ignorando o valor.")
                return 'skipped_invalid_value'
        
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
            else:
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
        sg_skipped_db = 0
        sg_skipped_invalid_value = 0
        sg_skipped_missing_data = 0
        api_call_errors = 0


        try:
            api_token = None
            if use_api_token_flag:
                api_token = get_api_token(self)
            
            if not api_token and use_api_token_flag:
                self.stdout.write(self.style.WARNING("Falha ao obter token da API. Prosseguindo sem autenticação."))
                self.warnings_count += 1
                use_api_token_flag = False

            all_sg_from_api_raw = self.fetch_starting_grid_data(api_token=api_token)
            
            if isinstance(all_sg_from_api_raw, dict) and "error_status" in all_sg_from_api_raw:
                api_call_errors += 1
                raise CommandError(f"Erro fatal ao buscar dados da API: {all_sg_from_api_raw['error_message']}. URL: {all_sg_from_api_raw['error_url']}")

            if not all_sg_from_api_raw:
                self.stdout.write(self.style.NOTICE("Nenhuma entrada de starting grid encontrada na API para importar. Encerrando."))
                return

            sg_found_api_total = len(all_sg_from_api_raw)

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

            if mode_param == 'U' and meeting_key_param:
                with transaction.atomic():
                    StartingGrid.objects.filter(
                        meeting_key=meeting_key_param
                    ).delete()
                    self.stdout.write(self.style.NOTICE(f"Registros de Starting Grid existentes deletados para Mtg {meeting_key_param} (modo U)."))
            
            existing_pks_for_mode_I = set()
            if mode_param == 'I':
                meeting_keys_in_batch = {entry.get('meeting_key') for entry in sg_entries_to_process if entry.get('meeting_key') is not None}
                for m_key in meeting_keys_in_batch:
                    existing_pks_for_mode_I.update(
                        StartingGrid.objects.filter(
                            meeting_key=m_key
                        ).values_list('meeting_key', 'session_key', 'driver_number')
                    )


            for i, sg_entry_dict in enumerate(sg_entries_to_process):
                if (i + 1) % 100 == 0 or (i + 1) == len(sg_entries_to_process):
                    self.stdout.write(f"Processando registro {i+1}/{len(sg_entries_to_process)}...")
                
                try:
                    if mode_param == 'I':
                        current_pk_tuple = (
                            sg_entry_dict.get('meeting_key'),
                            sg_entry_dict.get('session_key'),
                            sg_entry_dict.get('driver_number')
                        )
                        if current_pk_tuple in existing_pks_for_mode_I:
                            sg_skipped_db += 1
                            continue

                    result = self.process_starting_grid_entry(sg_entry_dict, mode=mode_param)
                    if result == 'inserted':
                        sg_inserted_db += 1
                    elif result == 'updated':
                        sg_updated_db += 1
                    elif result == 'skipped':
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