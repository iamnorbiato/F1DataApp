# G:\Learning\F1Data\F1Data_App\core\management\commands\import_cardata.py
import requests
import json
from datetime import datetime, timezone, timedelta
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, transaction
from django.conf import settings

from core.models import Sessions, Drivers, CarData, RaceControl
from dotenv import load_dotenv
import pytz

from .token_manager import get_api_token, is_token_expired

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

# Lock para sincronizar atualização de token se necessário (seguindo padrão do import_location)
token_update_lock = threading.Lock()

class Command(BaseCommand):
    help = 'Importa dados de telemetria (car_data) da API OpenF1 para o PostgreSQL de forma otimizada.'

    API_URL = "https://api.openf1.org/v1/car_data"
#    CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json')

    API_DELAY_SECONDS = 0.2
    CHUNK_DURATION_MINUTES = 20
    BULK_SIZE = 5000

    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5

    TIME_MARGIN_MINUTES = 10
    EXTRA_QUALY_MARGIN_MINUTES = 5

    warnings_count = 0
    car_data_inserted_db = 0
    car_data_skipped_db = 0
    api_call_errors = 0
    triplets_processed_count = 0

    def add_arguments(self, parser):
        parser.add_argument('--meeting_key', type=int, help='Filtrar importacao para um meeting_key especifico')
        parser.add_argument('--session_key', type=int, help='Filtrar importacao para um session_key especifico')
        parser.add_argument('--mode', type=str, choices=['I', 'U'], default='I', help="Modo: 'I' para inserir apenas novos, 'U' para forcar update")

    def add_warning(self, message):
        self.warnings_count += 1
        self.stdout.write(self.style.WARNING(message))

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
        target_timezone = pytz.timezone(settings.TIME_ZONE)
        try:
            session_obj = Sessions.objects.get(meeting_key=meeting_key, session_key=session_key)
            session_type = session_obj.session_type.lower()
        except Sessions.DoesNotExist:
            self.add_warning(f"Erro: Sessão (Mtg {meeting_key}, Sess {session_key}) não encontrada na tabela Sessions.")
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
                    self.add_warning(f"Aviso: Não foi possível encontrar eventos de início/fim na RaceControl para a sessão {session_type}. Usando datas da tabela Sessions como fallback.")
                    start_dt = session_obj.date_start
                    end_dt = session_obj.date_end
            except Exception as e:
                self.add_warning(f"Erro ao buscar datas na RaceControl: {e}. Usando datas da tabela Sessions como fallback.")
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
            self.add_warning(f"Erro: Não foi possível determinar o intervalo de tempo para a sessão Mtg {meeting_key}, Sess {session_key}.")
            return None, None

    def get_triplets_to_process(self, meeting_key=None, session_key=None):
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando triplas (meeting_key, session_key, driver_number) a processar..."))

        drivers_qs = Drivers.objects.all()
        if meeting_key:
            drivers_qs = drivers_qs.filter(meeting_key=meeting_key)
        if session_key:
            drivers_qs = drivers_qs.filter(session_key=session_key)

        all_driver_triplets = set(drivers_qs.values_list('meeting_key', 'session_key', 'driver_number'))

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

                for current_m_key, current_s_key, current_d_num in drivers_qs.filter(meeting_key=m_key, session_key=s_key).values_list('meeting_key', 'session_key', 'driver_number'):
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

    def fetch_cardata_chunk(self, meeting_key, session_key, driver_number, date_gt, date_lt, use_token=True):
        date_gt_str = self.format_datetime_for_api_url(date_gt)
        date_lt_str = self.format_datetime_for_api_url(date_lt)
        url_params = f"meeting_key={meeting_key}&session_key={session_key}&driver_number={driver_number}"
        url = f"{self.API_URL}?{url_params}&date>{date_gt_str}&date<{date_lt_str}"
        #self.stdout.write(self.style.NOTICE(f"INFO: Chamada API CarData: {url}"))

        headers = {"Accept": "application/json"}
        if use_token:
            api_token = get_api_token(self)
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
                    self.stdout.write(self.style.WARNING(f"Aviso: Erro {status_code} da API para URL: {url}. Tentativa {attempt + 1}/{self.API_MAX_RETRIES}. Retentando em {delay} segundos..."))
                    self.warnings_count += 1
                    time.sleep(delay)
                elif status_code == 401:
                    self.stdout.write(self.style.ERROR(f"Erro 401: Não autorizado. O token da API pode estar inválido. URL: {url}"))
                    return {"error_status": status_code, "error_url": url, "error_message": "Unauthorized"}
                else:
                    return {"error_status": status_code, "error_url": url, "error_message": str(e)}
        return {"error_status": "Failed after retries", "error_url": url, "error_message": "Max retries exceeded."}

    def build_cardata_instance(self, entry):
        date_str = entry.get('date')
        if not date_str:
            raise ValueError("Data ausente na entrada da API.")

        try:
            # Converte ISO com Z para timezone-aware UTC, igual ao import_location
            date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00')).astimezone(timezone.utc)
        except ValueError:
            raise ValueError(f"Formato de data inválido: {date_str}.")

        return CarData(
            meeting_key=entry.get('meeting_key'),
            session_key=entry.get('session_key'),
            driver_number=entry.get('driver_number'),
            date=date_obj,
            speed=entry.get('speed'),
            n_gear=entry.get('n_gear'),
            drs=entry.get('drs'),
            throttle=entry.get('throttle'),
            brake=entry.get('brake'),
            rpm=entry.get('rpm')
        )

    def process_and_save_chunk(self, meeting_key, session_key, driver_number, chunk_start, chunk_end, use_token_flag, mode):
        cardata = self.fetch_cardata_chunk(
            meeting_key=meeting_key,
            session_key=session_key,
            driver_number=driver_number,
            date_gt=chunk_start,
            date_lt=chunk_end,
            use_token=use_token_flag
        )

        if isinstance(cardata, dict) and "error_status" in cardata:
            self.add_warning(f"Erro na API para chunk (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}, {chunk_start} a {chunk_end}): {cardata['error_message']}. URL: {cardata.get('error_url')}. Pulando este chunk.")
            return 0, 0, 1

        if not cardata:
            self.add_warning(f"Aviso: Nenhuma entrada de car_data encontrada na API para o chunk (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}, {chunk_start} a {chunk_end}).")
            return 0, 0, 0

        instances_to_create = []
        for entry in cardata:
            try:
                obj = self.build_cardata_instance(entry)
                instances_to_create.append(obj)
            except Exception as e:
                self.add_warning(f"Erro ao construir entrada Mtg={meeting_key}, Sess={session_key}, Driver={driver_number}: {e}")

        inserted_count = 0
        skipped_count = 0
        try:
            with transaction.atomic():
                if mode == 'U':
                    CarData.objects.filter(
                        meeting_key=meeting_key,
                        session_key=session_key,
                        driver_number=driver_number,
                        date__range=(instances_to_create[0].date, instances_to_create[-1].date)
                    ).delete()
                    CarData.objects.bulk_create(instances_to_create, batch_size=self.BULK_SIZE)
                    inserted_count = len(instances_to_create)
                else:
                    created_instances = CarData.objects.bulk_create(instances_to_create, batch_size=self.BULK_SIZE, ignore_conflicts=True)
                    inserted_count = len(created_instances)
                    skipped_count = len(instances_to_create) - len(created_instances)
        except Exception as e:
            self.add_warning(f"Erro no DB para Mtg={meeting_key} Sess={session_key} Driver={driver_number}: {e}")

        return inserted_count, skipped_count, 0

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        self.warnings_count = 0
        self.car_data_inserted_db = 0
        self.car_data_skipped_db = 0
        self.api_call_errors = 0
        self.triplets_processed_count = 0

        meeting_key_param = options.get('meeting_key')
        session_key_param = options.get('session_key')
        mode_param = options.get('mode', 'I')

        if session_key_param and not meeting_key_param:
            raise CommandError("Erro: Se 'session_key' for fornecido, 'meeting_key' também deve ser fornecido.")

        max_workers_env = self.get_config_value('MAX_WORKERS', default=4)
        try:
            max_workers = int(max_workers_env)
        except Exception:
            self.stdout.write(self.style.WARNING(f"Valor inválido para MAX_WORKERS no config, usando padrão 4"))
            max_workers = 4

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        if not use_api_token_flag:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no env.cfg). Buscando dados históricos."))

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação de car_data..."))
        self.stdout.write(f"Parâmetros: meeting_key={meeting_key_param}, session_key={session_key_param}, mode={mode_param}")

        triplets_with_dates = self.get_triplets_to_process(meeting_key_param, session_key_param)

        if not triplets_with_dates:
            self.stdout.write(self.style.NOTICE("Nenhum triplet encontrado para processamento."))
            return

        self.stdout.write(self.style.SUCCESS(f"{len(triplets_with_dates)} triplets a processar com max_workers={max_workers}..."))

        futures = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for mtg, sess, drv, start_dt, end_dt in triplets_with_dates:
                self.triplets_processed_count += 1
                session_duration = end_dt - start_dt

                if session_duration.total_seconds() <= 0:
                    self.add_warning(f"Aviso: Duração da sessão inválida para Mtg {mtg}, Sess {sess}. Ignorando.")
                    continue

                chunks = list(self.generate_time_chunks(start_dt, end_dt, self.CHUNK_DURATION_MINUTES))

                for chunk_start, chunk_end in chunks:
                    futures.append(
                        executor.submit(
                            self.process_and_save_chunk,
                            mtg,
                            sess,
                            drv,
                            chunk_start,
                            chunk_end,
                            use_api_token_flag,
                            mode_param
                        )
                    )

            for future in as_completed(futures):
                inserted, skipped, api_error = future.result()
                self.car_data_inserted_db += inserted
                self.car_data_skipped_db += skipped
                self.api_call_errors += api_error

        self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importacao de car_data ---"))
        self.stdout.write(self.style.SUCCESS(f"Triplets processados: {self.triplets_processed_count}"))
        self.stdout.write(self.style.SUCCESS(f"Registros inseridos: {self.car_data_inserted_db}"))
        self.stdout.write(self.style.NOTICE(f"Registros ignorados (ja existiam): {self.car_data_skipped_db}"))
        self.stdout.write(self.style.WARNING(f"Total de avisos: {self.warnings_count}"))
        self.stdout.write(self.style.SUCCESS("Importacao finalizada."))
