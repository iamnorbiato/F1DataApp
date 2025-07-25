# G:\Learning\F1Data\F1Data_App\core\management\commands\import_pit.py
import requests
import json
from datetime import datetime, timezone, timedelta
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings

# Importa os modelos necessários
from core.models import Drivers, Sessions, Pit, Meetings # Adicionado Meetings para obter todos os meeting_keys, se necessário
from dotenv import load_dotenv
from update_token import update_api_token_if_needed
import pytz # Para manipulação de fusos horários

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

class Command(BaseCommand):
    help = 'Importa dados de pit stops da API OpenF1 e os insere na tabela pit do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/pit"

    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5
    BULK_SIZE = 5000

    # Contadores e lista de avisos serão inicializados no handle() para cada execução.

    def add_arguments(self, parser):
        parser.add_argument('--meeting_key', type=int, help='Especifica um Meeting Key para filtrar a importação (opcional).')
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

    def get_relevant_session_driver_pairs_to_fetch(self, meeting_key_filter=None, mode='I'):
        """
        Identifica pares (meeting_key, session_key) de *todas* as sessões e associa a eles os driver_numbers relevantes.
        Filtra por meeting_key e modo ('I' para apenas novos, 'U' para todos relevantes).
        Retorna um dicionário: {(m_key, s_key): {driver_number_1, driver_number_2, ...}}
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando pares (meeting_key, session_key) e drivers relevantes para 'Pit'..."))

        # Obter *todas* as sessões (removido o filtro session_type='Race')
        sessions_query = Sessions.objects.all()
        if meeting_key_filter:
            sessions_query = sessions_query.filter(meeting_key=meeting_key_filter)

        all_sessions = set(sessions_query.values_list('meeting_key', 'session_key'))
        self.stdout.write(f"Encontrados {len(all_sessions)} pares (meeting_key, session_key) para todas as sessões{' para o meeting_key especificado' if meeting_key_filter else ''}.")

        # Obter todas as triplas de drivers para as sessões relevantes
        relevant_driver_triplets = set(
            Drivers.objects.filter(
                meeting_key__in=[pair[0] for pair in all_sessions],
                session_key__in=[pair[1] for pair in all_sessions]
            ).values_list('meeting_key', 'session_key', 'driver_number')
        )
        self.stdout.write(f"Encontradas {len(relevant_driver_triplets)} triplas de drivers relevantes para todas as sessões.")

        final_pairs_drivers_map = {}

        if mode == 'I':
            # Para 'I', precisamos subtrair o que já existe em Pit (considerando M,S,D)
            existing_pit_msd_triplets = set(
                Pit.objects.filter(
                    meeting_key__in=[pair[0] for pair in all_sessions],
                    session_key__in=[pair[1] for pair in all_sessions]
                ).values_list('meeting_key', 'session_key', 'driver_number')
            )
            drivers_to_consider_for_fetch = relevant_driver_triplets - existing_pit_msd_triplets
            self.stdout.write(f"Modo 'I': {len(drivers_to_consider_for_fetch)} triplas de drivers/sessões serão consideradas para busca de novos pit stops.")
        else: # mode == 'U'
            drivers_to_consider_for_fetch = relevant_driver_triplets
            self.stdout.write(f"Modo 'U': Todas as {len(drivers_to_consider_for_fetch)} triplas de drivers/sessões relevantes serão consideradas para atualização/inserção.")

        # Organiza por (meeting_key, session_key) para chamadas de API
        for m_key, s_key, d_num in drivers_to_consider_for_fetch:
            final_pairs_drivers_map.setdefault((m_key, s_key), set()).add(d_num)
        
        self.stdout.write(self.style.SUCCESS(f"Identificados {len(final_pairs_drivers_map)} pares (M,S) únicos para buscar dados de 'Pit' na API."))
        return final_pairs_drivers_map


    def fetch_pit_stops_data(self, session_key, use_token=True):
        if not session_key:
            raise CommandError("session_key deve ser fornecido para buscar dados de pit stops da API.")

        url = f"{self.API_URL}?session_key={session_key}"

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

    def process_pit_entry(self, pit_data_dict, mode):
        try:
            lap_number = pit_data_dict.get('lap_number')
            if lap_number is None:
                lap_number = 0
                self.add_warning(f"lap_number é None para pit stop (Mtg {pit_data_dict.get('meeting_key', 'N/A')}, Sess {pit_data_dict.get('session_key', 'N/A')}, Driver {pit_data_dict.get('driver_number', 'N/A')}). Usando 0.")

            session_key = pit_data_dict.get('session_key')
            meeting_key = pit_data_dict.get('meeting_key')
            driver_number = pit_data_dict.get('driver_number')
            
            date_str = pit_data_dict.get('date')
            pit_duration = pit_data_dict.get('pit_duration')
            
            if any(val is None for val in [session_key, meeting_key, driver_number, lap_number, date_str]):
                missing_fields = [k for k,v in {'session_key': session_key, 'meeting_key': meeting_key, 'driver_number': driver_number, 'lap_number': lap_number, 'date': date_str}.items() if v is None]
                return 'skipped_missing_data'

            date_obj = None
            if date_str:
                try:
                    date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                except ValueError:
                    self.add_warning(f"Formato de data inválido '{date_str}' para pit stop (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}, Lap {lap_number}).")
                    return 'skipped_invalid_date'

            pit_duration_parsed = None
            if pit_duration is not None:
                try:
                    pit_duration_parsed = float(str(pit_duration).replace(',', '.').strip())
                except ValueError:
                    self.add_warning(f"Valor de 'pit_duration' '{pit_duration}' não é numérico. Ignorando para Sess {session_key}, Driver {driver_number}, Lap {lap_number}.")
                    pit_duration_parsed = None

            defaults = {
                'date': date_obj,
                'pit_duration': pit_duration_parsed
            }
            
            if mode == 'U':
                obj, created = Pit.objects.update_or_create(
                    session_key=session_key,
                    meeting_key=meeting_key,
                    driver_number=driver_number,
                    lap_number=lap_number,
                    date=date_obj,
                    defaults={
                        'pit_duration': pit_duration_parsed
                    }
                )
                return 'inserted' if created else 'updated'
            else:
                if Pit.objects.filter(
                    session_key=session_key,
                    meeting_key=meeting_key,
                    driver_number=driver_number,
                    lap_number=lap_number,
                    date=date_obj
                ).exists():
                    return 'skipped'
                
                Pit.objects.create(
                    session_key=session_key,
                    meeting_key=meeting_key,
                    driver_number=driver_number,
                    lap_number=lap_number,
                    date=date_obj,
                    pit_duration=pit_duration_parsed
                )
                return 'inserted'

        except IntegrityError:
            return 'skipped'
        except Exception as e:
            data_debug = f"Mtg {pit_data_dict.get('meeting_key', 'N/A')}, Sess {pit_data_dict.get('session_key', 'N/A')}, Driver {pit_data_dict.get('driver_number', 'N/A')}, Lap {pit_data_dict.get('lap_number', 'N/A')}"
            self.stdout.write(self.style.ERROR(f"Erro FATAL ao processar registro de pit stop ({data_debug}): {e} - Dados API: {pit_data_dict}"))
            raise


    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        self.warnings_count = 0
        self.all_warnings_details = []

        meeting_key_param = options.get('meeting_key')
        mode_param = options.get('mode', 'I')

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação/atualização de Pit Stops (ORM)..."))
        self.stdout.write(f"Parâmetros recebidos: meeting_key={meeting_key_param if meeting_key_param else 'Nenhum'}, mode={mode_param}")

        pit_stops_found_api_total = 0
        pit_stops_inserted_db = 0
        pit_stops_updated_db = 0
        pit_stops_skipped_db = 0
        pit_stops_skipped_missing_data = 0
        pit_stops_skipped_invalid_date = 0
        api_call_errors = 0

        sessions_api_calls_made = 0

        try:
            if use_api_token_flag:
                try:
                    self.stdout.write("Verificando e atualizando o token da API, se necessário...")
                    update_api_token_if_needed()
                    # Recarrega as variáveis de ambiente após possível atualização
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

            # Obter os pares (meeting_key, session_key) e drivers relevantes para buscar dados
            # Agora, não filtra por session_type='Race'
            relevant_session_driver_map = self.get_relevant_session_driver_pairs_to_fetch(
                meeting_key_filter=meeting_key_param,
                mode=mode_param
            )

            if not relevant_session_driver_map:
                self.stdout.write(self.style.NOTICE("Nenhum par (meeting_key, session_key) com drivers elegíveis para buscar pit stops. Encerrando."))
                return

            unique_session_pairs_to_fetch_api = sorted(list(relevant_session_driver_map.keys()))

            self.stdout.write(self.style.SUCCESS(f"Total de {len(unique_session_pairs_to_fetch_api)} pares (meeting_key, session_key) para buscar na API."))

            for i, (meeting_key, session_key) in enumerate(unique_session_pairs_to_fetch_api):
                self.stdout.write(f"Buscando e processando pit stops para par {i+1}/{len(unique_session_pairs_to_fetch_api)}: Mtg {meeting_key}, Sess {session_key}...")
                sessions_api_calls_made += 1

                pit_data_from_api = self.fetch_pit_stops_data(session_key=session_key, use_token=use_api_token_flag)

                if isinstance(pit_data_from_api, dict) and "error_status" in pit_data_from_api:
                    api_call_errors += 1
                    self.add_warning(f"Erro na API para Mtg {meeting_key}, Sess {session_key}: {pit_data_from_api['error_message']}")
                    continue

                if not pit_data_from_api:
                    self.stdout.write(self.style.WARNING(f"Nenhum registro de pit stops encontrado na API para Mtg {meeting_key}, Sess {session_key}."))
                    continue

                pit_stops_found_api_total += len(pit_data_from_api)
                self.stdout.write(f"Encontrados {len(pit_data_from_api)} registros de pit stops para Mtg {meeting_key}, Sess {session_key}. Processando...")

                relevant_drivers_for_current_session = relevant_session_driver_map.get((meeting_key, session_key), set())
                
                if mode_param == 'U':
                    with transaction.atomic():
                        Pit.objects.filter(
                            meeting_key=meeting_key,
                            session_key=session_key
                        ).delete()
                        self.stdout.write(f"Registros de pit stops existentes deletados para Mtg {meeting_key}, Sess {session_key}.")

                for pit_entry_dict in pit_data_from_api:
                    driver_num_from_api = pit_entry_dict.get('driver_number')
                    if driver_num_from_api and driver_num_from_api in relevant_drivers_for_current_session:
                        try:
                            result = self.process_pit_entry(pit_entry_dict, mode=mode_param)
                            if result == 'inserted':
                                pit_stops_inserted_db += 1
                            elif result == 'updated':
                                pit_stops_updated_db += 1
                            elif result == 'skipped':
                                pit_stops_skipped_db += 1
                            elif result == 'skipped_missing_data':
                                pit_stops_skipped_missing_data += 1
                                self.add_warning(f"Pit Stop ignorado: dados obrigatórios ausentes para Mtg {pit_entry_dict.get('meeting_key', 'N/A')}, Sess {pit_entry_dict.get('session_key', 'N/A')}, Driver {pit_entry_dict.get('driver_number', 'N/A')}, Lap {pit_entry_dict.get('lap_number', 'N/A')}.")
                            elif result == 'skipped_invalid_date':
                                pit_stops_skipped_invalid_date += 1
                        except Exception as pit_process_e:
                            self.add_warning(f"Erro ao processar UM REGISTRO de pit stop (Mtg {pit_entry_dict.get('meeting_key', 'N/A')}, Sess {pit_entry_dict.get('session_key', 'N/A')}, Driver {pit_entry_dict.get('driver_number', 'N/A')}, Lap {pit_entry_dict.get('lap_number', 'N/A')}): {pit_process_e}. Pulando para o próximo.")
                    
                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Processamento de Pit Stops concluído!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Pit Stops (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo do Processamento de Pit Stops (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Pares (M,S) únicos para busca na API: {len(unique_session_pairs_to_fetch_api) if 'unique_session_pairs_to_fetch_api' in locals() else 0}"))
            self.stdout.write(self.style.SUCCESS(f"Chamadas à API de Pit Stops realizadas: {sessions_api_calls_made}"))
            self.stdout.write(self.style.SUCCESS(f"Registros de Pit Stops encontrados na API (total): {pit_stops_found_api_total}"))
            self.stdout.write(self.style.SUCCESS(f"Registros novos inseridos no DB: {pit_stops_inserted_db}"))
            self.stdout.write(self.style.SUCCESS(f"Registros existentes atualizados no DB: {pit_stops_updated_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB em modo 'I'): {pit_stops_skipped_db}"))
            if pit_stops_skipped_missing_data > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (dados obrigatórios ausentes): {pit_stops_skipped_missing_data}"))
            if pit_stops_skipped_invalid_date > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (formato de data inválido): {pit_stops_skipped_invalid_date}"))
            if api_call_errors > 0:
                self.stdout.write(self.style.ERROR(f"Erros em chamadas à API: {api_call_errors}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Processamento de pit stops finalizado!"))