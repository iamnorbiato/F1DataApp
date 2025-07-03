# G:\Learning\F1Data\F1Data_App\core\management\commands\import_location.py
import requests
import json
from datetime import datetime, timezone, timedelta
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings 

from core.models import Drivers, Location, Sessions
from dotenv import load_dotenv
from update_token import update_api_token_if_needed
import pytz # Necessário para manipulação de fusos horários

# --- CORREÇÃO AQUI: Usa settings.BASE_DIR para o caminho do env.cfg ---
ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')
# -----------------------------------------------------------------------

class Command(BaseCommand):
    help = 'Importa dados de localização (location) da API OpenF1 e os insere na tabela location do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/location"
    CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json')

    API_DELAY_SECONDS = 0.2
    DEFAULT_TIME_CHUNK_HOURS = 1 
    MIN_SESSION_CHUNK_HOURS = 2 
    BULK_SIZE = 5000

    API_MAX_RETRIES = 3 # <--- ADICIONADO: Número máximo de retentativas para erros da API
    API_RETRY_DELAY_SECONDS = 5 # <--- ADICIONADO: Atraso inicial entre retentativas

    warnings_count = 0

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

    def get_triplets_to_process(self):
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando triplas (meeting_key, session_key, driver_number) a processar para location..."))
        
        all_sessions_with_dates = Sessions.objects.all().values(
            'meeting_key', 'session_key', 'date_start', 'date_end'
        )
        
        target_timezone = pytz.timezone(settings.TIME_ZONE) 
        session_dates_map = {}
        for s in all_sessions_with_dates:
            if s['date_start'] and s['date_end']:
                start_dt_local = s['date_start'].astimezone(target_timezone)
                end_dt_local = s['date_end'].astimezone(target_timezone)
                session_dates_map[(s['meeting_key'], s['session_key'])] = (start_dt_local, end_dt_local)
            else:
                self.stdout.write(self.style.WARNING(f"Aviso: Sessão (Mtg {s['meeting_key']}, Sess {s['session_key']}) sem date_start ou date_end. Ignorando."))
                self.warnings_count += 1


        self.stdout.write(f"Encontrados {len(session_dates_map)} pares (meeting_key, session_key) com datas válidas na tabela 'sessions'.")

        all_driver_triplets = set(
            Drivers.objects.all().values_list('meeting_key', 'session_key', 'driver_number')
        )
        self.stdout.write(f"Encontradas {len(all_driver_triplets)} triplas de drivers na tabela 'drivers'.")

        existing_location_triplets = set(
            Location.objects.all().values_list('meeting_key', 'session_key', 'driver_number')
        )
        self.stdout.write(f"Encontradas {len(existing_location_triplets)} triplas já presentes na tabela 'location'.")

        triplets_to_process_with_dates = []
        for m_key, s_key, d_num in sorted(list(all_driver_triplets - existing_location_triplets)):
            session_pair = (m_key, s_key)
            if session_pair in session_dates_map:
                date_start, date_end = session_dates_map[session_pair]
                triplets_to_process_with_dates.append((m_key, s_key, d_num, date_start, date_end))
            else:
                self.stdout.write(self.style.WARNING(f"Aviso: Tripla ({m_key}, {s_key}, {d_num}) em 'drivers' não tem datas válidas em 'sessions'. Ignorando."))
                self.warnings_count += 1

        self.stdout.write(self.style.SUCCESS(f"Identificadas {len(triplets_to_process_with_dates)} triplas (com datas de sessão) que precisam de dados de location."))
        return triplets_to_process_with_dates

    def generate_time_chunks(self, start_dt, end_dt, chunk_interval_hours):
        chunk_delta = timedelta(hours=chunk_interval_hours)
        current_dt = start_dt

        while current_dt < end_dt:
            chunk_end_dt = min(current_dt + chunk_delta, end_dt)
            yield (current_dt, chunk_end_dt)
            current_dt = chunk_end_dt

    def format_datetime_for_api_url(self, dt_obj): 
        """
        Formata um objeto datetime (timezone-aware e já em settings.TIME_ZONE) para a string 'YYYY-MM-DD HH:MM:SS +-HHMM' (com offset de horas e minutos).
        Isto é para atender o formato específico da API OpenF1 para o filtro de data.
        """
        if dt_obj.tzinfo is None or dt_obj.utcoffset() is None:
            self.warnings_count += 1
            raise ValueError(f"Data sem fuso horário (naive) passada para format_datetime_for_api_url: {dt_obj}. Esperado timezone-aware.")
        
        date_time_part = dt_obj.strftime('%Y-%m-%d %H:%M:%S')

        offset_timedelta = dt_obj.utcoffset() 
        offset_str = ""
        if offset_timedelta is not None:
            total_seconds = offset_timedelta.total_seconds()
            
            offset_sign = '+' if total_seconds >= 0 else '-'
            abs_hours = int(abs(total_seconds) // 3600)
            abs_minutes = int((abs(total_seconds) % 3600) // 60)
            
            # Formato esperado: "YYYY-MM-DD HH:MM:SS+-HHMM"
            formatted_offset = f"{offset_sign}{abs_hours:02}{abs_minutes:02}" 

            return f"{date_time_part}{formatted_offset}" 
        else:
            self.warnings_count += 1
            self.stdout.write(self.style.WARNING(f"Aviso: Não foi possível obter offset de fuso horário para '{dt_obj}'. Enviando data sem offset para API."))
            return date_time_part 

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
                self.warnings_count += 1
                raise CommandError("Token da API (OPENF1_API_TOKEN) não encontrado. Requisição será feita sem Authorization.")
            else:
                headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.warnings_count += 1
            pass 

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
                else:
                    return {"error_status": status_code, 
                            "error_url": url, 
                            "error_message": str(e)}
        return {"error_status": "Failed after retries", "error_url": url, "error_message": "Max retries exceeded."}


    def build_location_instance(self, loc_data_entry_dict):
        x_value = loc_data_entry_dict.get('x')
        if x_value is not None and x_value == 0:
            return None

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

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH) 

        # Zera o contador de avisos para esta execução
        self.warnings_count = 0 

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'
        
        # --- MENSAGEM DE STATUS DO TOKEN (APENAS UMA VEZ) ---
        if use_api_token_flag:
            try:
                update_api_token_if_needed()
            except Exception as e:
                raise CommandError(f"Falha ao verificar/atualizar o token da API: {e}. Não é possível prosseguir com a importação.")
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no env.cfg). Buscando dados históricos."))
        # ----------------------------------------------------

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação de Location (ORM)..."))

        loc_entries_inserted_db = 0
        loc_entries_skipped_db = 0 # Incluirá ignorados por PK
        loc_entries_filtered_x0 = 0
        triplets_processed_count = 0 
            
        try: # Este try encapsula toda a lógica principal do handle
            triplets_to_process_with_dates = self.get_triplets_to_process()
            
            if not triplets_to_process_with_dates:
                self.stdout.write(self.style.NOTICE("Nenhuma nova tripla (meeting_key, session_key, driver_number) encontrada para importar location. Encerrando."))
                return

            self.stdout.write(self.style.SUCCESS(f"Total de {len(triplets_to_process_with_dates)} triplas elegíveis para processamento."))
            # self.stdout.write(f"Iniciando loop por {len(triplets_to_process_with_dates)} triplas elegíveis e seus chunks de tempo...") # Removido

            api_delay = self.API_DELAY_SECONDS 
            chunk_interval_hours = self.DEFAULT_TIME_CHUNK_HOURS # Intervalo em horas

            for i, (meeting_key, session_key, driver_number, session_date_start, session_date_end) in enumerate(triplets_to_process_with_dates):
                current_triplet_data_objects = []
                
                try: # Este try encapsula a transação atômica para UMA TRIPLA COMPLETA
                    self.stdout.write(f"Processando tripla {i+1}/{len(triplets_to_process_with_dates)}: Mtg={meeting_key}, Sess={session_key}, Driver={driver_number} (Datas: {session_date_start.strftime('%Y-%m-%d %H:%M:%S%z')} a {session_date_end.strftime('%Y-%m-%d %H:%M:%S%z')})...") # <--- DEBUG: Adicionado %z
                    triplets_processed_count += 1
                    
                    session_duration = session_date_end - session_date_start
                    
                    if session_duration.total_seconds() / 3600 > self.MIN_SESSION_CHUNK_HOURS:
                        # self.stdout.write(self.style.NOTICE(f"  Duração da sessão ({session_duration}) > {self.MIN_SESSION_CHUNK_HOURS} horas. Dividindo em chunks de {self.DEFAULT_TIME_CHUNK_HOURS} hora(s).")) # Removido
                        chunks = list(self.generate_time_chunks(session_date_start, session_date_end, self.DEFAULT_TIME_CHUNK_HOURS))
                    else:
                        # self.stdout.write(self.style.NOTICE(f"  Duração da sessão ({session_duration}) <= {self.MIN_SESSION_CHUNK_HOURS} horas. Processando como um único chunk.")) # Removido
                        chunks = [(session_date_start, session_date_end)]

                    for chunk_start, chunk_end in chunks:
                        # self.stdout.write(f"  Buscando chunk: {chunk_start.strftime('%H:%M')} a {chunk_end.strftime('%H:%M')}...") # Removido
                        
                        loc_data_from_api = self.fetch_location_data(
                            meeting_key=meeting_key, 
                            session_key=session_key, 
                            driver_number=driver_number, 
                            date_gt=chunk_start, 
                            date_lt=chunk_end, 
                            use_token=use_api_token_flag
                        )
                        
                        if isinstance(loc_data_from_api, dict) and "error_status" in loc_data_from_api:
                            self.stdout.write(self.style.ERROR(f"  Erro na API para chunk (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}, {self.format_datetime_for_api_url(chunk_start)} a {self.format_datetime_for_api_url(chunk_end)}): {loc_data_from_api['error_message']}. URL: {loc_data_from_api['error_url']}. Pulando este chunk."))
                            self.warnings_count += 1
                            continue

                        if not loc_data_from_api:
                            self.stdout.write(self.style.WARNING(f"  Aviso: Nenhuma entrada de location encontrada na API para o chunk (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}, {self.format_datetime_for_api_url(chunk_start)} a {self.format_datetime_for_api_url(chunk_end)})."))
                            self.warnings_count += 1
                            continue
                        
                        for loc_entry_dict in loc_data_from_api:
                            try:
                                loc_instance = self.build_location_instance(loc_entry_dict)
                                if loc_instance is not None:
                                    current_triplet_data_objects.append(loc_instance)
                                else:
                                    loc_entries_filtered_x0 += 1
                            except Exception as build_e:
                                self.stdout.write(self.style.ERROR(f"Erro ao construir instância de location para chunk (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}): {build_e}. Pulando este registro."))
                                self.warnings_count += 1
                        
                        if api_delay > 0:
                            time.sleep(api_delay)
                    
                    if current_triplet_data_objects:
                        with transaction.atomic():
                            created_instances = Location.objects.bulk_create(current_triplet_data_objects, batch_size=self.BULK_SIZE, ignore_conflicts=True)
                            loc_entries_inserted_db += len(created_instances)
                            loc_entries_skipped_db += (len(current_triplet_data_objects) - len(created_instances))

                    else:
                        self.stdout.write(self.style.WARNING(f"  Aviso: Nenhuma entrada de location válida encontrada para inserção para Mtg={meeting_key}, Sess={session_key}, Driver={driver_number} (após filtragem de x=0 e API)."))
                        self.warnings_count += 1

                except Exception as triplet_overall_e:
                    self.stdout.write(self.style.ERROR(f"Erro ao processar tripla (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}): {triplet_overall_e}. Esta tripla não foi processada por completo. Pulando para a próxima tripla."))
                    self.warnings_count += 1


            self.stdout.write(self.style.SUCCESS("Importação de Location concluída com sucesso para todas as triplas elegíveis!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Location (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Location (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Triplas (meeting_key, session_key, driver_number) processadas: {triplets_processed_count}"))
            self.stdout.write(self.style.SUCCESS(f"Registros inseridos no DB: {loc_entries_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB): {loc_entries_skipped_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (x=0): {loc_entries_filtered_x0}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de location finalizada!"))