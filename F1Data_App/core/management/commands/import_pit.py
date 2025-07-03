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
from core.models import Drivers, Sessions, Pit 
from dotenv import load_dotenv
from update_token import update_api_token_if_needed
import pytz # Para manipulação de fusos horários

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg') 

class Command(BaseCommand):
    help = 'Importa dados de pit stops da API OpenF1 e os insere na tabela pit do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/pit" # URL da API para pit stops
    CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json')

    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3 
    API_RETRY_DELAY_SECONDS = 5 
    BULK_SIZE = 5000 

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

    def get_race_session_driver_triplets_to_process(self):
        """
        Identifica triplas (meeting_key, session_key, driver_number) que são do tipo 'Race'
        e que ainda não têm dados de pit stops importados na tabela 'pit'.
        Retorna uma lista de tuplas (meeting_key, session_key, driver_number) a serem processadas.
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando triplas (meeting_key, session_key, driver_number) para sessões 'Race' de drivers..."))
        
        self.stdout.write(f"Buscando pares (meeting_key, session_key) de sessões 'Race'...")
        race_sessions_pairs = set(
            Sessions.objects.filter(session_type='Race').values_list('meeting_key', 'session_key')
        )
        self.stdout.write(f"Encontrados {len(race_sessions_pairs)} pares (meeting_key, session_key) para sessões 'Race'.")

        self.stdout.write(f"Buscando todas as triplas de drivers (meeting_key, session_key, driver_number) da tabela 'drivers'...")
        all_driver_triplets = set(
            Drivers.objects.all().values_list('meeting_key', 'session_key', 'driver_number')
        )
        self.stdout.write(f"Encontradas {len(all_driver_triplets)} triplas de drivers.")

        relevant_driver_triplets = set(
            (m, s, d) for m, s, d in all_driver_triplets if (m, s) in race_sessions_pairs
        )
        self.stdout.write(f"Filtradas {len(relevant_driver_triplets)} triplas de drivers para sessões 'Race'.")

        self.stdout.write("Buscando triplas já presentes na tabela 'pit'...")
        existing_pit_triplets = set(
            Pit.objects.all().values_list('meeting_key', 'session_key', 'driver_number')
        )
        self.stdout.write(f"Encontradas {len(existing_pit_triplets)} triplas já presentes na tabela 'pit'.")

        triplets_to_process = sorted(list(relevant_driver_triplets - existing_pit_triplets))
        
        self.stdout.write(self.style.SUCCESS(f"Identificadas {len(triplets_to_process)} triplas (M,S,D) de drivers de sessões 'Race' que precisam de dados de pit stops."))
        return triplets_to_process

    def fetch_pit_stops_data(self, session_key, use_token=True):
        if not session_key:
            raise CommandError("session_key deve ser fornecido para buscar dados de pit stops da API.")

        url = f"{self.API_URL}?session_key={session_key}"

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

    def build_pit_instance(self, pit_data_dict): # <--- Método ajustado
        # --- CORREÇÃO AQUI: Tratar lap_number=None para 0 ---
        lap_number = pit_data_dict.get('lap_number', 0) # <--- Usar .get() com valor padrão 0
        if pit_data_dict.get('lap_number') is None: # Verifica se o valor ORIGINAL era None
            self.warnings_count += 1 # Conta como aviso se lap_number era null
        # ----------------------------------------------------

        session_key = pit_data_dict.get('session_key')
        meeting_key = pit_data_dict.get('meeting_key')
        driver_number = pit_data_dict.get('driver_number')
        
        date_str = pit_data_dict.get('date')
        pit_duration = pit_data_dict.get('pit_duration')
        
        if any(val is None for val in [session_key, meeting_key, driver_number, lap_number]):
            missing_fields = [k for k,v in {'session_key': session_key, 'meeting_key': meeting_key, 'driver_number': driver_number, 'lap_number': lap_number}.items() if v is None]
            raise ValueError(f"Dados incompletos para Pit: faltam {missing_fields}. Dados: {pit_data_dict}")

        date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00')) if date_str else None

        pit_duration_parsed = None
        if pit_duration is not None:
            try:
                pit_duration_parsed = float(str(pit_duration).replace(',', '.').strip())
            except ValueError:
                self.stdout.write(self.style.WARNING(f"Aviso: Valor de 'pit_duration' '{pit_duration}' não é numérico. Ignorando para Sess {session_key}, Driver {driver_number}, Lap {lap_number}."))
                self.warnings_count += 1
                pit_duration_parsed = None

        return Pit(
            session_key=session_key,
            meeting_key=meeting_key,
            driver_number=driver_number,
            lap_number=lap_number,
            date=date_obj,
            pit_duration=pit_duration_parsed
        )

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        self.warnings_count = 0

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'
        
        if use_api_token_flag:
            try:
                update_api_token_if_needed()
            except Exception as e:
                raise CommandError(f"Falha ao verificar/atualizar o token da API: {e}. Não é possível prosseguir com a importação.")
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no env.cfg). Buscando dados históricos."))

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação de Pit Stops (ORM)..."))

        pit_stops_inserted_db = 0
        pit_stops_skipped_db = 0
        total_race_session_driver_triplets_eligible = 0 
        sessions_api_calls_processed_count = 0 
            
        try:
            all_race_session_driver_triplets = self.get_race_session_driver_triplets_to_process()
            total_race_session_driver_triplets_eligible = len(all_race_session_driver_triplets)
            
            if not all_race_session_driver_triplets:
                self.stdout.write(self.style.NOTICE("Nenhuma tripla (M,S,D) de sessões 'Race' encontrada para importar pit stops. Encerrando."))
                return

            self.stdout.write(self.style.SUCCESS(f"Total de {total_race_session_driver_triplets_eligible} triplas (M,S,D) de drivers de sessões 'Race' para processamento."))
            
            api_delay = self.API_DELAY_SECONDS 

            session_pairs_to_fetch_api = {}
            for m_key, s_key, d_num in all_race_session_driver_triplets:
                session_pairs_to_fetch_api.setdefault((m_key, s_key), set()).add(d_num)
            
            unique_session_pairs_to_fetch = sorted(list(session_pairs_to_fetch_api.keys()))

            for i, (meeting_key, session_key) in enumerate(unique_session_pairs_to_fetch):
                self.stdout.write(f"Processando Sessão (Mtg {meeting_key}, Sess {session_key}) para dados de pit stops ({i+1}/{len(unique_session_pairs_to_fetch)})...")
                sessions_api_calls_processed_count += 1
                current_session_pit_stops_objects = []
                
                try: 
                    pit_data_from_api = self.fetch_pit_stops_data(session_key=session_key, use_token=use_api_token_flag)
                    
                    if isinstance(pit_data_from_api, dict) and "error_status" in pit_data_from_api:
                        self.stdout.write(self.style.ERROR(f"  Erro na API para Sess {session_key}, Mtg {meeting_key}: {pit_data_from_api['error_message']}. Pulando esta sessão."))
                        self.warnings_count += 1
                        continue 

                    if not pit_data_from_api:
                        self.stdout.write(self.style.WARNING(f"  Aviso: Nenhuma entrada de pit stops encontrada na API para Sess {session_key}, Mtg {meeting_key}."))
                        self.warnings_count += 1
                        continue
                    
                    self.stdout.write(f"  Encontrados {len(pit_data_from_api)} registros para Sess {session_key}. Filtrando por driver e construindo...")

                    relevant_drivers_for_current_session_set = session_pairs_to_fetch_api.get((meeting_key, session_key), set())
                    
                    for pit_entry_dict in pit_data_from_api:
                        driver_num_from_api = pit_entry_dict.get('driver_number')
                        if driver_num_from_api in relevant_drivers_for_current_session_set: # Filtra drivers aqui
                            try:
                                pit_instance = self.build_pit_instance(pit_entry_dict)
                                if pit_instance is not None:
                                    current_session_pit_stops_objects.append(pit_instance)
                            except Exception as build_e:
                                self.stdout.write(self.style.ERROR(f"Erro ao construir instância de pit stops (Sess {session_key}, Driver {driver_num_from_api}): {build_e}. Pulando este registro."))
                                self.warnings_count += 1
                            
                        if current_session_pit_stops_objects:
                            with transaction.atomic():
                                created_instances = Pit.objects.bulk_create(current_session_pit_stops_objects, batch_size=self.BULK_SIZE, ignore_conflicts=True)
                                pit_stops_inserted_db += len(created_instances)
                                pit_stops_skipped_db += (len(current_session_pit_stops_objects) - len(created_instances))
                                self.stdout.write(self.style.SUCCESS(f"  {len(created_instances)} registros inseridos (total para esta sessão)."))
                            
                        else:
                            self.stdout.write(self.style.WARNING(f"  Aviso: Nenhuma entrada de pit stops válida encontrada para inserção para Sess {session_key}, Mtg {meeting_key} (após filtragem de driver)."))
                            self.warnings_count += 1

                        if api_delay > 0:
                            time.sleep(api_delay)

                except Exception as session_overall_e: # Captura erros gerais de processamento da SESSÃO
                    self.stdout.write(self.style.ERROR(f"Erro ao processar Sessão (Mtg {meeting_key}, Sess {session_key}): {session_overall_e}. Esta sessão não foi processada por completo. Pulando para a próxima sessão."))
                    self.warnings_count += 1
                    
            self.stdout.write(self.style.SUCCESS("Importação de Pit Stops concluída com sucesso para todas as sessões e drivers elegíveis!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Pit Stops (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Pit Stops (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Triplas (meeting_key, session_key, driver_number) de drivers de Race elegíveis: {total_race_session_driver_triplets_eligible}")) 
            self.stdout.write(self.style.SUCCESS(f"Sessões (meeting_key, session_key) com API processada: {sessions_api_calls_processed_count}")) 
            self.stdout.write(self.style.SUCCESS(f"Registros inseridos no DB: {pit_stops_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB): {pit_stops_skipped_db}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de pit stops finalizada!"))