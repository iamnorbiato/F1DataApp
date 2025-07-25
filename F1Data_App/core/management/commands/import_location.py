# G:\Learning\F1Data\F1Data_App\core\management\commands\import_location.py
import requests
import json
from datetime import datetime, timezone, timedelta
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings

from core.models import Drivers, Location, Sessions, Meetings # Incluí Meetings para pegar todos os meeting_keys
from dotenv import load_dotenv
from update_token import update_api_token_if_needed
import pytz

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

class Command(BaseCommand):
    help = 'Importa dados de localização (location) da API OpenF1 e os insere na tabela location do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/location"

    API_DELAY_SECONDS = 0.2
    DEFAULT_TIME_CHUNK_HOURS = 1
    MIN_SESSION_CHUNK_HOURS = 2
    BULK_SIZE = 5000

    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5

    MAX_WORKERS = 4  # padrão, pode ser sobrescrito pelo env.cfg

    # warnings_count será inicializado no handle() para esta execução.
    # all_warnings_details será inicializado no handle() para esta execução.

    def add_arguments(self, parser):
        parser.add_argument(
            '--meeting_key',
            type=int,
            help='Meeting key para filtrar quais dados importar (opcional). Se omitido, processa todos os meetings.'
        )
        parser.add_argument(
            '--mode',
            type=str,
            choices=['I', 'U'],
            default='I',
            help='Modo de importação: I para Insert (apenas novos), U para Update (atualiza existentes e insere novos).'
        )

    def add_warning(self, message):
        # Inicializa se não estiverem inicializados (para segurança, embora handle os inicialize)
        if not hasattr(self, 'warnings_count'):
            self.warnings_count = 0
            self.all_warnings_details = []
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    # get_config_value foi removido, pois não é mais usado.

    def get_triplets_to_process(self, meeting_key, mode):
        self.stdout.write(self.style.MIGRATE_HEADING(f"Identificando triplas (meeting_key, session_key, driver_number) para meeting_key={meeting_key} (modo={mode}) a processar para location..."))

        # Obter sessions para o meeting_key específico
        sessions_filtered = Sessions.objects.filter(meeting_key=meeting_key).values('meeting_key', 'session_key', 'date_start', 'date_end')

        target_timezone = pytz.timezone(settings.TIME_ZONE)
        session_dates_map = {}
        for s in sessions_filtered:
            if s['date_start'] and s['date_end']:
                start_dt_local = s['date_start'].astimezone(target_timezone)
                end_dt_local = s['date_end'].astimezone(target_timezone)
                session_dates_map[(s['meeting_key'], s['session_key'])] = (start_dt_local, end_dt_local)
            else:
                self.add_warning(f"Sessão (Mtg {s['meeting_key']}, Sess {s['session_key']}) sem date_start ou date_end. Ignorando.")

        self.stdout.write(f"Encontrados {len(session_dates_map)} pares (meeting_key, session_key) com datas válidas para meeting_key={meeting_key}.")

        # Buscar drivers somente para meeting_key passado
        all_driver_triplets = set(
            Drivers.objects.filter(meeting_key=meeting_key).values_list('meeting_key', 'session_key', 'driver_number')
        )
        self.stdout.write(f"Encontradas {len(all_driver_triplets)} triplas de drivers para meeting_key={meeting_key}.")

        triplets_to_process_with_dates = []

        if mode == 'I': # Insert apenas: Apenas triplas que não existem em Location
            existing_location_triplets = set(
                Location.objects.filter(meeting_key=meeting_key).values_list('meeting_key', 'session_key', 'driver_number')
            )
            self.stdout.write(f"Encontradas {len(existing_location_triplets)} triplas já presentes na tabela 'location' para meeting_key={meeting_key}.")
            
            drivers_to_consider = all_driver_triplets - existing_location_triplets
            self.stdout.write(f"Identificadas {len(drivers_to_consider)} novas triplas de drivers para inserção.")
        else: # Update (mode == 'U'): Todas as triplas de drivers
            drivers_to_consider = all_driver_triplets
            self.stdout.write(f"Todas as {len(drivers_to_consider)} triplas de drivers serão consideradas para atualização/inserção.")

        for m_key, s_key, d_num in sorted(list(drivers_to_consider)):
            session_pair = (m_key, s_key)
            if session_pair in session_dates_map:
                date_start, date_end = session_dates_map[session_pair]
                triplets_to_process_with_dates.append((m_key, s_key, d_num, date_start, date_end))
            else:
                self.add_warning(f"Tripla ({m_key}, {s_key}, {d_num}) em 'drivers' não tem datas válidas em 'sessions'. Ignorando.")

        self.stdout.write(self.style.SUCCESS(f"Identificadas {len(triplets_to_process_with_dates)} triplas (com datas de sessão) para processamento neste modo."))

        return triplets_to_process_with_dates

    def generate_time_chunks(self, start_dt, end_dt, chunk_interval_hours):
        chunk_delta = timedelta(hours=chunk_interval_hours)
        current_dt = start_dt

        while current_dt < end_dt:
            chunk_end_dt = min(current_dt + chunk_delta, end_dt)
            yield (current_dt, chunk_end_dt)
            current_dt = chunk_end_dt

    def format_datetime_for_api_url(self, dt_obj):
        if dt_obj.tzinfo is None or dt_obj.utcoffset() is None:
            self.add_warning(f"Data sem fuso horário (naive) passada para format_datetime_for_api_url: {dt_obj}. Esperado timezone-aware.")
            return dt_obj.strftime('%Y-%m-%d %H:%M:%S') # Retorna sem offset se naive para evitar erro

        date_time_part = dt_obj.strftime('%Y-%m-%d %H:%M:%S')
        offset_timedelta = dt_obj.utcoffset()
        
        # Formata o offset no formato ±HHMM
        total_seconds = int(offset_timedelta.total_seconds())
        offset_sign = '+' if total_seconds >= 0 else '-'
        abs_hours = abs(total_seconds) // 3600
        abs_minutes = (abs(total_seconds) % 3600) // 60
        
        formatted_offset = f"{offset_sign}{abs_hours:02}{abs_minutes:02}"
        return f"{date_time_part}{formatted_offset}"

    def fetch_location_data(self, meeting_key, session_key, driver_number, date_gt, date_lt, use_token=True):
        if not (meeting_key and session_key and driver_number and date_gt and date_lt):
            raise CommandError("meeting_key, session_key, driver_number, date_gt e date_lt devem ser fornecidos.")

        date_gt_str = self.format_datetime_for_api_url(date_gt)
        date_lt_str = self.format_datetime_for_api_url(date_lt)

        url_params = f"meeting_key={meeting_key}&session_key={session_key}&driver_number={driver_number}"
        url = f"{self.API_URL}?{url_params}&date>{date_gt_str}&date<{date_lt_str}"

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
                if status_code in [500, 502, 503, 504] and attempt < self.API_MAX_RETRIES - 1:
                    delay = self.API_RETRY_DELAY_SECONDS * (2 ** attempt)
                    self.stdout.write(self.style.WARNING(f"  Aviso: {error_msg}. Tentativa {attempt + 1}/{self.API_MAX_RETRIES}. Retentando em {delay} segundos..."))
                    time.sleep(delay)
                else:
                    self.add_warning(f"Falha na busca da API após retries para {url}: {error_msg}")
                    return {"error_status": status_code,
                            "error_url": url,
                            "error_message": str(e)}
        self.add_warning(f"Falha na busca da API para {url}: Máximo de retries excedido.")
        return {"error_status": "Failed after retries", "error_url": url, "error_message": "Max retries exceeded."}

    def build_location_instance(self, loc_data_entry_dict):
        # Filtra x=0 antes de construir a instância
        x_value = loc_data_entry_dict.get('x')
        if x_value is not None and x_value == 0:
            return None # Retorna None para indicar que esta instância deve ser ignorada

        meeting_key = loc_data_entry_dict.get('meeting_key')
        session_key = loc_data_entry_dict.get('session_key')
        driver_number = loc_data_entry_dict.get('driver_number')
        date_str = loc_data_entry_dict.get('date')
        x = loc_data_entry_dict.get('x')
        y = loc_data_entry_dict.get('y')
        z = loc_data_entry_dict.get('z')

        if any(val is None for val in [meeting_key, session_key, driver_number, date_str]):
            missing_fields = [k for k,v in {'meeting_key': meeting_key, 'session_key': session_key, 'driver_number': driver_number, 'date': date_str}.items() if v is None]
            raise ValueError(f"Dados incompletos para Location: faltam {missing_fields}. Dados: {loc_data_entry_dict}")

        try:
            date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError(f"Formato de data inválido: {date_str}")

        return Location(
            meeting_key=meeting_key,
            session_key=session_key,
            driver_number=driver_number,
            date=date_obj,
            x=x,
            y=y,
            z=z
        )

    def process_triplet_chunk(self, meeting_key, session_key, driver_number, chunk_start, chunk_end, use_token_flag):
        """
        Processa um chunk de tempo para um triplet (meeting_key, session_key, driver_number)
        Busca dados na API e constrói as instâncias Location.
        Retorna lista de instâncias Location válidas.
        """
        loc_data_from_api = self.fetch_location_data(
            meeting_key=meeting_key,
            session_key=session_key,
            driver_number=driver_number,
            date_gt=chunk_start,
            date_lt=chunk_end,
            use_token=use_token_flag
        )

        if isinstance(loc_data_from_api, dict) and "error_status" in loc_data_from_api:
            self.add_warning(f"Erro na API para chunk (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}, {self.format_datetime_for_api_url(chunk_start)} a {self.format_datetime_for_api_url(chunk_end)}): {loc_data_from_api['error_message']}. URL: {loc_data_from_api['error_url']}. Pulando este chunk.")
            return [], 1 # Retorna 1 para contar erro de API

        if not loc_data_from_api:
            self.add_warning(f"Nenhuma entrada de location encontrada na API para o chunk (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}, {self.format_datetime_for_api_url(chunk_start)} a {self.format_datetime_for_api_url(chunk_end)}).")
            return [], 0 # Retorna 0 para erro de API, mas avisa

        loc_instances = []
        filtered_x0_count = 0
        for loc_entry_dict in loc_data_from_api:
            try:
                loc_instance = self.build_location_instance(loc_entry_dict)
                if loc_instance is not None:
                    loc_instances.append(loc_instance)
                else:
                    filtered_x0_count += 1
            except Exception as build_e:
                self.add_warning(f"Erro ao construir instância de location para chunk (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}): {build_e}. Pulando este registro.")
        return loc_instances, 0, filtered_x0_count # Retorna 0 para erro de API, e o count de x=0

    def handle(self, *args, **options):
        # AQUI: load_dotenv inicial para carregar configs do ambiente inicial
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        self.warnings_count = 0
        self.all_warnings_details = []

        meeting_key_param = options.get('meeting_key')
        mode_param = options.get('mode', 'I') # 'I' é o default se não fornecido

        max_workers_env = os.getenv('MAX_WORKERS', str(self.MAX_WORKERS))
        try:
            self.MAX_WORKERS = int(max_workers_env)
        except ValueError:
            self.add_warning(f"Valor inválido para MAX_WORKERS no env.cfg, usando padrão {self.MAX_WORKERS}")

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação/atualização de Location (ORM)..."))
        self.stdout.write(f"Parâmetros recebidos: meeting_key={meeting_key_param if meeting_key_param else 'Nenhum'}, mode={mode_param}")

        loc_entries_inserted_db = 0
        loc_entries_updated_db = 0 # No bulk_create com delete prévio, tudo é "inserido"
        loc_entries_skipped_db = 0
        loc_entries_filtered_x0 = 0
        triplets_processed_count = 0
        api_call_errors_total = 0


        try:
            if use_api_token_flag:
                try:
                    self.stdout.write("Verificando e atualizando o token da API, se necessário...")
                    update_api_token_if_needed()
                    # >>> CORREÇÃO CRÍTICA AQUI: Recarrega as variáveis de ambiente após possível atualização <<<
                    # Isso garante que os.getenv() abaixo leia o TOKEN NOVO do arquivo env.cfg
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

            # Determinar quais meeting_keys processar
            meetings_to_process = []
            if meeting_key_param:
                meetings_to_process.append(meeting_key_param)
            else:
                self.stdout.write(self.style.NOTICE("Nenhum meeting_key especificado. Processando todos os meetings existentes em 'Meetings'."))
                meetings_to_process = list(Meetings.objects.values_list('meeting_key', flat=True).distinct().order_by('meeting_key'))
                if not meetings_to_process:
                    self.stdout.write(self.style.WARNING("Nenhum meeting_key encontrado na tabela 'Meetings'. Encerrando."))
                    return

            total_meetings_to_process = len(meetings_to_process)
            for m_idx, current_meeting_key in enumerate(meetings_to_process):
                self.stdout.write(self.style.MIGRATE_HEADING(f"\nIniciando processamento para Meeting Key: {current_meeting_key} ({m_idx + 1}/{total_meetings_to_process})"))

                triplets_to_process_with_dates = self.get_triplets_to_process(current_meeting_key, mode_param)

                if not triplets_to_process_with_dates:
                    self.stdout.write(self.style.NOTICE(f"Nenhuma tripla para importar location para meeting_key={current_meeting_key}. Pulando."))
                    continue

                self.stdout.write(self.style.SUCCESS(f"Total de {len(triplets_to_process_with_dates)} triplas elegíveis para busca na API para Mtg {current_meeting_key}."))

                chunk_interval_hours = self.DEFAULT_TIME_CHUNK_HOURS

                all_loc_instances_for_meeting = []
                with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
                    futures = []
                    for i, (m_key, s_key, d_num, session_date_start, session_date_end) in enumerate(triplets_to_process_with_dates):
                        triplets_processed_count += 1

                        session_duration = session_date_end - session_date_start
                        if session_duration.total_seconds() / 3600 > self.MIN_SESSION_CHUNK_HOURS:
                            chunks = list(self.generate_time_chunks(session_date_start, session_date_end, chunk_interval_hours))
                        else:
                            chunks = [(session_date_start, session_date_end)]

                        for chunk_start, chunk_end in chunks:
                            futures.append(
                                executor.submit(
                                    self.process_triplet_chunk,
                                    m_key,
                                    s_key,
                                    d_num,
                                    chunk_start,
                                    chunk_end,
                                    use_api_token_flag
                                )
                            )

                    for future in as_completed(futures):
                        loc_instances, api_error_count_chunk, filtered_x0_count_chunk = future.result()
                        all_loc_instances_for_meeting.extend(loc_instances)
                        api_call_errors_total += api_error_count_chunk
                        loc_entries_filtered_x0 += filtered_x0_count_chunk
                        
                if all_loc_instances_for_meeting:
                    if mode_param == 'U':
                        # Para o modo 'U', deleta todos os registros de location para o meeting_key atual antes de inserir
                        self.stdout.write(self.style.NOTICE(f"Deletando registros existentes de location para meeting_key={current_meeting_key} (modo U)..."))
                        Location.objects.filter(meeting_key=current_meeting_key).delete()
                        loc_entries_inserted_db += len(all_loc_instances_for_meeting) # Todos serão inseridos como novos
                    
                    with transaction.atomic():
                        # Para modo 'I', bulk_create com ignore_conflicts=True
                        # Para modo 'U', ignore_conflicts=False (já deletamos)
                        created_instances = Location.objects.bulk_create(
                            all_loc_instances_for_meeting, 
                            batch_size=self.BULK_SIZE, 
                            ignore_conflicts=(mode_param == 'I')
                        )
                        
                        if mode_param == 'I':
                            loc_entries_inserted_db += len(created_instances)
                            loc_entries_skipped_db += (len(all_loc_instances_for_meeting) - len(created_instances))
                        else: # mode_param == 'U'
                             # Se deletamos antes, todos os que estão aqui são novos (inseridos)
                             # loc_entries_inserted_db já contabilizou o total.
                            pass

                else:
                    self.stdout.write(self.style.WARNING(f"Nenhuma entrada válida de location para inserção para meeting_key={current_meeting_key}."))

                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Processamento de Location concluído!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Location (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Location (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Total de Meetings processados: {total_meetings_to_process if 'total_meetings_to_process' in locals() else 0}"))
            self.stdout.write(self.style.SUCCESS(f"Triplas (meeting_key, session_key, driver_number) processadas: {triplets_processed_count}"))
            self.stdout.write(self.style.SUCCESS(f"Registros novos inseridos no DB: {loc_entries_inserted_db}"))
            if mode_param == 'I': # Only relevant for 'I' mode
                self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB): {loc_entries_skipped_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (x=0): {loc_entries_filtered_x0}"))
            if api_call_errors_total > 0:
                self.stdout.write(self.style.ERROR(f"Erros em chamadas à API: {api_call_errors_total}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de location finalizada!"))