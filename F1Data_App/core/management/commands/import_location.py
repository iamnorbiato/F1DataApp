# G:\Learning\F1Data\F1Data_App\core\management\commands\import_location.py
import requests
import json
from datetime import datetime, timezone, timedelta
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings 

from core.models import Drivers, Location, Sessions, RaceControl
from dotenv import load_dotenv, set_key
import pytz

# Importe o novo módulo de gerenciamento de token
from .token_manager import get_api_token, is_token_expired

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

# Crie um lock global para sincronizar a atualização do token
token_update_lock = threading.Lock()

class Command(BaseCommand):
    help = 'Importa dados de localização (location) da API OpenF1 e os insere na tabela location do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/location"
    CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json')

    API_DELAY_SECONDS = 0.2
    CHUNK_DURATION_MINUTES = 20
    BULK_SIZE = 5000

    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5

    MAX_WORKERS = 4
    TIME_MARGIN_MINUTES = 10
    EXTRA_QUALY_MARGIN_MINUTES = 5

    warnings_count = 0
    loc_entries_inserted_db = 0
    loc_entries_skipped_db = 0

    def add_arguments(self, parser):
        parser.add_argument(
            '--meeting_key',
            type=int,
            required=False,
            help='Meeting key para filtrar quais dados importar.'
        )
        parser.add_argument(
            '--session_key',
            type=int,
            required=False,
            help='Session key para filtrar quais dados importar, em conjunto com meeting_key.'
        )
        parser.add_argument(
            '--mode',
            type=str,
            choices=['I', 'U'],
            default='I',
            help='Modo de importação: I para Insert, U para Update.'
        )

    def get_config_value(self, key=None, default=None, section=None):
        config = {}
        if not os.path.exists(self.CONFIG_FILE):
            self.stdout.write(self.style.WARNING(f"Aviso: Arquivo de configuração '{self.CONFIG_FILE}' não encontrado. Usando valor padrão para '{key}'."))
            self.warnings_count += 1
            return default
        try:
            with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)

            if section:
                section_data = config.get(section, default if key is None else {})
                if key is None:
                    return section_data
                else:
                    return section_data.get(key, default)
            else:
                if key is None:
                    return config
                else:
                    return config.get(key, default)

        except json.JSONDecodeError as e:
            raise CommandError(f"Erro ao ler/parsear o arquivo de configuração JSON '{self.CONFIG_FILE}': {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado ao acessar o arquivo de configuração: {e}")

    def get_session_time_range(self, meeting_key, session_key):
        """
        Determina o intervalo de tempo de uma sessão com base no seu tipo.
        Retorna (start_dt, end_dt) ou (None, None) se não encontrar.
        """
        target_timezone = pytz.timezone(settings.TIME_ZONE)
        
        try:
            session_obj = Sessions.objects.get(meeting_key=meeting_key, session_key=session_key)
            session_type = session_obj.session_type.lower()
        except Sessions.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Erro: Sessão (Mtg {meeting_key}, Sess {session_key}) não encontrada na tabela Sessions."))
            return None, None
            
        start_dt = None
        end_dt = None

        if session_type in ['race', 'qualifying']:
            try:
                race_start_event = RaceControl.objects.filter(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    flag='GREEN',
                    message__icontains='GREEN LIGHT'
                ).order_by('session_date').first()

                race_end_event = RaceControl.objects.filter(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    flag='CHEQUERED',
                    message__icontains='CHEQUERED FLAG'
                ).order_by('-session_date').first()

                if race_start_event and race_end_event:
                    start_dt = race_start_event.session_date
                    end_dt = race_end_event.session_date
                else:
                    self.stdout.write(self.style.WARNING(f"Aviso: Não foi possível encontrar eventos de início/fim na RaceControl para a sessão {session_type}. Usando datas da tabela Sessions como fallback."))
                    self.warnings_count += 1
                    start_dt = session_obj.date_start
                    end_dt = session_obj.date_end
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Erro ao buscar datas na RaceControl: {e}. Usando datas da tabela Sessions como fallback."))
                self.warnings_count += 1
                start_dt = session_obj.date_start
                end_dt = session_obj.date_end
        else:
            start_dt = session_obj.date_start
            end_dt = session_obj.date_end

        if start_dt and end_dt:
            start_dt_local = start_dt.astimezone(target_timezone) - timedelta(minutes=self.TIME_MARGIN_MINUTES)
            end_dt_local = end_dt.astimezone(target_timezone) + timedelta(minutes=self.TIME_MARGIN_MINUTES)
            
            if session_type == 'qualifying':
                end_dt_local += timedelta(minutes=self.EXTRA_QUALY_MARGIN_MINUTES)

            return start_dt_local, end_dt_local
        else:
            self.stdout.write(self.style.ERROR(f"Erro: Não foi possível determinar o intervalo de tempo para a sessão."))
            return None, None

    def get_triplets_to_process(self, meeting_key=None, session_key=None):
        if session_key and not meeting_key:
            raise CommandError("Erro: Se 'session_key' for fornecido, 'meeting_key' também deve ser fornecido.")
        
        self.stdout.write(self.style.MIGRATE_HEADING(f"Identificando triplas (meeting_key, session_key, driver_number) a processar..."))

        drivers_queryset = Drivers.objects.all()
        if meeting_key:
            drivers_queryset = drivers_queryset.filter(meeting_key=meeting_key)
        if session_key:
            drivers_queryset = drivers_queryset.filter(session_key=session_key)

        all_driver_triplets = set(drivers_queryset.values_list('meeting_key', 'session_key', 'driver_number'))
        
        self.stdout.write(f"Encontradas {len(all_driver_triplets)} triplas de drivers para os parâmetros de busca.")

        triplets_to_process_with_dates = []
        processed_sessions = set()
        
        for m_key, s_key, d_num in sorted(list(all_driver_triplets)):
            session_pair = (m_key, s_key)
            if session_pair not in processed_sessions:
                processed_sessions.add(session_pair)

                start_dt_final, end_dt_final = self.get_session_time_range(m_key, s_key)
                
                if not start_dt_final or not end_dt_final:
                    continue

                for current_m_key, current_s_key, current_d_num in drivers_queryset.filter(meeting_key=m_key, session_key=s_key).values_list('meeting_key', 'session_key', 'driver_number'):
                    triplets_to_process_with_dates.append((current_m_key, current_s_key, current_d_num, start_dt_final, end_dt_final))

        self.stdout.write(self.style.SUCCESS(f"Identificadas {len(triplets_to_process_with_dates)} triplas (com datas de sessão) para importação."))

        return triplets_to_process_with_dates
    

    def generate_time_chunks(self, start_dt, end_dt, chunk_interval_minutes):
        chunk_delta = timedelta(minutes=chunk_interval_minutes)
        current_dt = start_dt

        while current_dt < end_dt:
            chunk_end_dt = min(current_dt + chunk_delta, end_dt)
            yield (current_dt, chunk_end_dt)
            current_dt = chunk_end_dt

    def format_datetime_for_api_url(self, dt_obj): 
        if dt_obj.tzinfo is None or dt_obj.utcoffset() is None:
            self.warnings_count += 1
            raise ValueError(f"Data sem fuso horário (naive) passada para format_datetime_for_api_url: {dt_obj}. Esperado timezone-aware.")
        
        return dt_obj.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

    def fetch_location_data(self, meeting_key, session_key, driver_number, date_gt, date_lt, use_token=True):
        if not (meeting_key and session_key and driver_number and date_gt and date_lt):
            raise CommandError("meeting_key, session_key, driver_number, date_gt e date_lt devem ser fornecidos.")

        date_gt_str = self.format_datetime_for_api_url(date_gt)
        date_lt_str = self.format_datetime_for_api_url(date_lt)

        url_params = f"meeting_key={meeting_key}&session_key={session_key}&driver_number={driver_number}"
        url = f"{self.API_URL}?{url_params}&date>{date_gt_str}&date<{date_lt_str}" 
        
        headers = {"Accept": "application/json"}
        
        if use_token:
            api_token = get_api_token(self) # CORREÇÃO AQUI: Passa a instância completa do comando
            if not api_token:
                self.stdout.write(self.style.WARNING("Aviso: Falha ao obter um token da API. Requisição será feita sem Authorization."))
                self.warnings_count += 1
            else:
                headers["Authorization"] = f"Bearer {api_token}"
        
        for attempt in range(self.API_MAX_RETRIES):
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                status_code = getattr(response, 'status_code', 'Unknown')
                if status_code in [500, 502, 503, 504] and attempt < self.API_MAX_RETRIES - 1:
                    delay = self.API_RETRY_DELAY_SECONDS * (2 ** attempt)
                    self.stdout.write(self.style.WARNING(f"  Aviso: Erro {status_code} da API para URL: {url}. Tentativa {attempt + 1}/{self.API_MAX_RETRIES}. Retentando em {delay} segundos..."))
                    self.warnings_count += 1
                    time.sleep(delay)
                elif status_code == 401:
                    self.stdout.write(self.style.ERROR(f"Erro 401: Não autorizado. O token da API pode estar inválido. URL: {url}"))
                    return {"error_status": status_code, "error_url": url, "error_message": "Unauthorized"}
                else:
                    return {"error_status": status_code, "error_url": url, "error_message": str(e)}
        return {"error_status": "Failed after retries", "error_url": url, "error_message": "Max retries exceeded."}

    def build_location_instance(self, loc_data_entry_dict):
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
            date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00')).astimezone(timezone.utc)
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

    def process_and_save_chunk(self, meeting_key, session_key, driver_number, chunk_start, chunk_end, use_token_flag, mode):
        """
        Busca os dados de um chunk na API e os insere/atualiza no banco de dados.
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
            self.stdout.write(self.style.ERROR(f"  Erro na API para chunk (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}, {self.format_datetime_for_api_url(chunk_start)} a {self.format_datetime_for_api_url(chunk_end)}): {loc_data_from_api['error_message']}. URL: {loc_data_from_api['error_url']}. Pulando este chunk."))
            self.warnings_count += 1
            return 0, 0, 0

        if not loc_data_from_api:
            self.stdout.write(self.style.WARNING(f"  Aviso: Nenhuma entrada de location encontrada na API para o chunk (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}, {self.format_datetime_for_api_url(chunk_start)} a {self.format_datetime_for_api_url(chunk_end)})."))
            self.warnings_count += 1
            return 0, 0, 0
        
        loc_instances = []
        filtered_x0 = 0
        for loc_entry_dict in loc_data_from_api:
            try:
                loc_instance = self.build_location_instance(loc_entry_dict)
                if loc_instance is not None:
                    loc_instances.append(loc_instance)
                else:
                    filtered_x0 += 1
            except Exception as build_e:
                self.stdout.write(self.style.ERROR(f"Erro ao construir instância de location para chunk (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}): {build_e}. Pulando este registro."))
                self.warnings_count += 1

        if not loc_instances:
            return 0, 0, filtered_x0

        inserted_count = 0
        skipped_count = 0
        
        try:
            with transaction.atomic():
                if mode == 'U':
                    delete_queryset = Location.objects.filter(
                        meeting_key=meeting_key,
                        session_key=session_key,
                        driver_number=driver_number,
                        date__range=(loc_instances[0].date, loc_instances[-1].date)
                    )
                    deleted_count, _ = delete_queryset.delete()
                    created_instances = Location.objects.bulk_create(loc_instances, batch_size=self.BULK_SIZE)
                    inserted_count = len(created_instances)
                
                elif mode == 'I':
                    created_instances = Location.objects.bulk_create(loc_instances, batch_size=self.BULK_SIZE, ignore_conflicts=True)
                    inserted_count = len(created_instances)
                    skipped_count = len(loc_instances) - inserted_count
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Erro de DB ao inserir chunk (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}): {e}."))
            self.warnings_count += 1
        
        return inserted_count, skipped_count, filtered_x0

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH) 

        self.warnings_count = 0 
        self.loc_entries_inserted_db = 0
        self.loc_entries_skipped_db = 0
        loc_entries_filtered_x0 = 0
        triplets_processed_count = 0

        meeting_key = options['meeting_key']
        session_key = options['session_key']
        mode = options['mode']
        
        if session_key and not meeting_key:
            raise CommandError("Erro: Se 'session_key' for fornecido, 'meeting_key' também deve ser fornecido.")

        max_workers_env = self.get_config_value('MAX_WORKERS', default=self.MAX_WORKERS)
        try:
            self.MAX_WORKERS = int(max_workers_env)
        except Exception:
            self.stdout.write(self.style.WARNING(f"Valor inválido para MAX_WORKERS no config, usando padrão {self.MAX_WORKERS}"))

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'
        
        if not use_api_token_flag:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no env.cfg). Buscando dados históricos."))

        self.stdout.write(self.style.MIGRATE_HEADING(f"Iniciando a importação de Location (ORM)..."))
        if meeting_key:
            self.stdout.write(self.style.NOTICE(f"  Filtrando por meeting_key={meeting_key}"))
        if session_key:
            self.stdout.write(self.style.NOTICE(f"  Filtrando por session_key={session_key}"))
        if not meeting_key and not session_key:
            self.stdout.write(self.style.NOTICE(f"  Nenhum filtro de meeting_key ou session_key fornecido. Processando todas as sessões."))

        try:
            triplets_to_process_with_dates = self.get_triplets_to_process(meeting_key, session_key)
            
            if not triplets_to_process_with_dates:
                self.stdout.write(self.style.NOTICE(f"Nenhuma tripla de driver encontrada para importar location. Encerrando."))
                return

            self.stdout.write(self.style.SUCCESS(f"Total de {len(triplets_to_process_with_dates)} triplas elegíveis para processamento."))

            chunk_duration = self.CHUNK_DURATION_MINUTES

            with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
                futures = []
                processed_sessions = set()

                for i, (m_key, s_key, driver_number, session_date_start, session_date_end) in enumerate(triplets_to_process_with_dates):
                    triplets_processed_count += 1
                    session_duration = session_date_end - session_date_start
                    
                    if session_duration.total_seconds() <= 0:
                        if (m_key, s_key) not in processed_sessions:
                            self.stdout.write(self.style.WARNING(f"Aviso: Duração da sessão inválida para ({m_key}, {s_key}). Ignorando todos os drivers desta sessão."))
                            processed_sessions.add((m_key, s_key))
                        self.warnings_count += 1
                        continue
                    
                    chunks = list(self.generate_time_chunks(session_date_start, session_date_end, chunk_duration))

                    for chunk_start, chunk_end in chunks:
                        futures.append(
                            executor.submit(
                                self.process_and_save_chunk,
                                m_key,
                                s_key,
                                driver_number,
                                chunk_start,
                                chunk_end,
                                use_api_token_flag,
                                mode
                            )
                        )

                for future in as_completed(futures):
                    inserted, skipped, x0_filtered = future.result()
                    self.loc_entries_inserted_db += inserted
                    self.loc_entries_skipped_db += skipped
                    loc_entries_filtered_x0 += x0_filtered


            self.stdout.write(self.style.SUCCESS("Importação de Location concluída com sucesso!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Location (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Location (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Triplas (meeting_key, session_key, driver_number) processadas: {triplets_processed_count}"))
            self.stdout.write(self.style.SUCCESS(f"Registros inseridos no DB: {self.loc_entries_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB): {self.loc_entries_skipped_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (x=0): {loc_entries_filtered_x0}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de location finalizada!"))