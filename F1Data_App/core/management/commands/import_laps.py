# G:\Learning\F1Data\F1Data_App\core\management\commands\import_laps.py
import requests
import json
from datetime import datetime, timezone, timedelta
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings # Importado para settings.BASE_DIR

# Importa os modelos necessários
from core.models import Drivers, Sessions, Laps 
from dotenv import load_dotenv
from update_token import update_api_token_if_needed
import pytz # Para manipulação de fusos horários

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg') 

class Command(BaseCommand):
    help = 'Importa dados de voltas (laps) da API OpenF1 e os insere na tabela laps do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/laps" # URL da API para laps
    CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json')

    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3 
    API_RETRY_DELAY_SECONDS = 5 
    BULK_SIZE = 5000 # <--- ADICIONADO: Tamanho do lote para bulk_create

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

    def get_session_driver_triplets_to_process(self): # Mesma lógica que para teamradio e weather
        """
        Identifica quais triplas (meeting_key, session_key, driver_number) que existem
        e ainda não têm dados de laps importados na tabela 'laps'.
        Retorna uma lista de tuplas (meeting_key, session_key, driver_number) a serem processadas.
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando triplas (meeting_key, session_key, driver_number) a processar para laps..."))
        
        # Obter todos os pares (meeting_key, session_key) da tabela 'sessions'
        self.stdout.write("Buscando todos os pares (meeting_key, session_key) da tabela 'sessions'...")
        all_session_pairs = set(
            Sessions.objects.all().values_list('meeting_key', 'session_key')
        )
        self.stdout.write(f"Encontrados {len(all_session_pairs)} pares (meeting_key, session_key) na tabela 'sessions'.")

        # Obter todas as triplas (meeting_key, session_key, driver_number) da tabela 'drivers'
        self.stdout.write(f"Buscando todas as triplas de drivers (meeting_key, session_key, driver_number) da tabela 'drivers'...")
        all_driver_triplets = set(
            Drivers.objects.all().values_list('meeting_key', 'session_key', 'driver_number')
        )
        self.stdout.write(f"Encontradas {len(all_driver_triplets)} triplas de drivers.")

        relevant_driver_triplets = set(
            (m, s, d) for m, s, d in all_driver_triplets if (m, s) in all_session_pairs
        )
        self.stdout.write(f"Filtradas {len(relevant_driver_triplets)} triplas de drivers para sessões válidas.")

        existing_laps_triplets = set(
            Laps.objects.all().values_list('meeting_key', 'session_key', 'driver_number')
        )
        self.stdout.write(f"Buscando triplas já presentes na tabela 'laps'...")
        self.stdout.write(f"Encontradas {len(existing_laps_triplets)} triplas já presentes na tabela 'laps'.")

        triplets_to_process = sorted(list(relevant_driver_triplets - existing_laps_triplets))
        
        self.stdout.write(self.style.SUCCESS(f"Identificadas {len(triplets_to_process)} triplas (M,S,D) que precisam de dados de laps."))
        return triplets_to_process

    def fetch_laps_data(self, session_key, driver_number, use_token=True): # <--- API filtra por session_key E driver_number
        """
        Busca dados de voltas da API OpenF1 para um session_key e driver_number específicos.
        """
        if not session_key or not driver_number:
            raise CommandError("session_key e driver_number devem ser fornecidos para buscar dados de laps da API.")

        url = f"{self.API_URL}?session_key={session_key}&driver_number={driver_number}" # Constrói a URL com ambos os filtros

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

    def build_lap_instance(self, lap_data_dict):
        """
        Constrói uma instância de modelo Laps a partir de um dicionário de dados.
        """
        meeting_key = lap_data_dict.get('meeting_key')
        session_key = lap_data_dict.get('session_key')
        driver_number = lap_data_dict.get('driver_number')
        lap_number = lap_data_dict.get('lap_number')
        
        date_start_str = lap_data_dict.get('date_start')
        duration_sector_1 = lap_data_dict.get('duration_sector_1')
        duration_sector_2 = lap_data_dict.get('duration_sector_2')
        duration_sector_3 = lap_data_dict.get('duration_sector_3')
        i1_speed = lap_data_dict.get('i1_speed')
        i2_speed = lap_data_dict.get('i2_speed')
        is_pit_out_lap = lap_data_dict.get('is_pit_out_lap')
        lap_duration = lap_data_dict.get('lap_duration')
        segments_sector_1 = lap_data_dict.get('segments_sector_1')
        segments_sector_2 = lap_data_dict.get('segments_sector_2')
        segments_sector_3 = lap_data_dict.get('segments_sector_3')
        st_speed = lap_data_dict.get('st_speed')

        # Validação crítica para campos NOT NULL na PK
        if any(val is None for val in [meeting_key, session_key, driver_number, lap_number]):
            missing_fields = [k for k,v in {'meeting_key': meeting_key, 'session_key': session_key, 'driver_number': driver_number, 'lap_number': lap_number}.items() if v is None]
            raise ValueError(f"Dados incompletos para Laps: faltam {missing_fields}. Dados: {lap_data_dict}")

        date_start_obj = None
        if date_start_str:
            try:
                date_start_obj = datetime.fromisoformat(date_start_str.replace('Z', '+00:00'))
            except ValueError:
                self.stdout.write(self.style.WARNING(f"Aviso: Formato de data inválido '{date_start_str}' para Lap (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}, Lap {lap_number})."))
                self.warnings_count += 1
                # Deixa como None se não puder parsear

        # Assegura que segments_sector_X são listas (para ArrayField)
        segments_sector_1 = segments_sector_1 if isinstance(segments_sector_1, list) else None
        segments_sector_2 = segments_sector_2 if isinstance(segments_sector_2, list) else None
        segments_sector_3 = segments_sector_3 if isinstance(segments_sector_3, list) else None

        return Laps(
            meeting_key=meeting_key,
            session_key=session_key,
            driver_number=driver_number,
            lap_number=lap_number,
            date_start=date_start_obj,
            duration_sector_1=duration_sector_1,
            duration_sector_2=duration_sector_2,
            duration_sector_3=duration_sector_3,
            i1_speed=i1_speed,
            i2_speed=i2_speed,
            is_pit_out_lap=is_pit_out_lap,
            lap_duration=lap_duration,
            segments_sector_1=segments_sector_1,
            segments_sector_2=segments_sector_2,
            segments_sector_3=segments_sector_3,
            st_speed=st_speed
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

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação de Laps (ORM)..."))

        laps_inserted_db = 0
        laps_skipped_db = 0
        triplets_processed_count = 0 
            
        try:
            triplets_to_process = self.get_session_driver_triplets_to_process()
            
            if not triplets_to_process:
                self.stdout.write(self.style.NOTICE("Nenhuma nova tripla (meeting_key, session_key, driver_number) encontrada para importar laps. Encerrando."))
                return

            self.stdout.write(self.style.SUCCESS(f"Total de {len(triplets_to_process)} triplas elegíveis para processamento."))
            
            api_delay = self.API_DELAY_SECONDS 

            for i, (meeting_key, session_key, driver_number) in enumerate(triplets_to_process):
                current_laps_objects = []
                
                try: # Este try encapsula a transação atômica para UMA TRIPLA COMPLETA
                    self.stdout.write(f"Processando tripla {i+1}/{len(triplets_to_process)}: Mtg={meeting_key}, Sess={session_key}, Driver={driver_number}...")
                    triplets_processed_count += 1
                    
                    laps_data_from_api = self.fetch_laps_data(
                        session_key=session_key, 
                        driver_number=driver_number, 
                        use_token=use_api_token_flag
                    )
                    
                    if isinstance(laps_data_from_api, dict) and "error_status" in laps_data_from_api:
                        self.stdout.write(self.style.ERROR(f"  Erro na API para tripla (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}): {laps_data_from_api['error_message']}. Pulando esta tripla."))
                        self.warnings_count += 1
                        continue

                    if not laps_data_from_api:
                        self.stdout.write(self.style.WARNING(f"  Aviso: Nenhuma entrada de laps encontrada na API para a tripla (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number})."))
                        self.warnings_count += 1
                        continue
                    
                    for lap_entry_dict in laps_data_from_api:
                        try:
                            lap_instance = self.build_lap_instance(lap_entry_dict)
                            if lap_instance is not None:
                                current_laps_objects.append(lap_instance)
                        except Exception as build_e:
                            self.stdout.write(self.style.ERROR(f"Erro ao construir instância de laps para tripla (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}): {build_e}. Pulando este registro."))
                            self.warnings_count += 1
                    
                    if current_laps_objects:
                        with transaction.atomic():
                            created_instances = Laps.objects.bulk_create(current_laps_objects, batch_size=self.BULK_SIZE, ignore_conflicts=True)
                            laps_inserted_db += len(created_instances)
                            laps_skipped_db += (len(current_laps_objects) - len(created_instances))

                    else:
                        self.stdout.write(self.style.WARNING(f"  Aviso: Nenhuma entrada de laps válida encontrada para inserção para Mtg={meeting_key}, Sess={session_key}, Driver={driver_number} (após construção de instância)."))
                        self.warnings_count += 1

                    if api_delay > 0:
                        time.sleep(api_delay) # Delay APÓS cada chamada de API (por tripla)

                except Exception as triplet_overall_e:
                    self.stdout.write(self.style.ERROR(f"Erro ao processar tripla (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}): {triplet_overall_e}. Esta tripla não foi processada por completo. Pulando para a próxima tripla."))
                    self.warnings_count += 1


            self.stdout.write(self.style.SUCCESS("Importação de Laps concluída com sucesso para todas as triplas elegíveis!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Laps (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Laps (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Triplas (meeting_key, session_key, driver_number) processadas: {triplets_processed_count}"))
            self.stdout.write(self.style.SUCCESS(f"Registros inseridos no DB: {laps_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB): {laps_skipped_db}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de laps finalizada!"))