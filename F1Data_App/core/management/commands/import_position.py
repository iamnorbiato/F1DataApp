# G:\Learning\F1Data\F1Data_App\core\management\commands\import_position.py
import requests
import json
from datetime import datetime, timezone, timedelta
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings 

# Importa os modelos necessários
from core.models import Drivers, Sessions, Position, Meetings # Precisamos de Meetings e Drivers para obter os pares
from dotenv import load_dotenv
from update_token import update_api_token_if_needed
import pytz # Para manipulação de fusos horários

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg') 

class Command(BaseCommand):
    help = 'Importa dados de posição (position) da API OpenF1 e os insere na tabela positions do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/position" # URL da API para positions
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

    def get_triplets_to_process(self):
        """
        Identifica quais triplas (meeting_key, session_key, driver_number)
        ainda não têm dados de posição importados na tabela 'positions'.
        Retorna uma lista de tuplas (meeting_key, session_key, driver_number) a serem processadas.
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando triplas (meeting_key, session_key, driver_number) a processar para positions..."))

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

        # Filtrar as triplas de drivers para incluir apenas aquelas que são de sessões existentes
        relevant_driver_triplets = set(
            (m, s, d) for m, s, d in all_driver_triplets if (m, s) in all_session_pairs
        )
        self.stdout.write(f"Filtradas {len(relevant_driver_triplets)} triplas de drivers para sessões válidas.")

        # Obter as triplas (meeting_key, session_key, driver_number) já presentes na tabela 'positions'
        self.stdout.write("Buscando triplas já presentes na tabela 'positions'...")
        existing_position_triplets = set(
            Position.objects.all().values_list('meeting_key', 'session_key', 'driver_number')
        )
        self.stdout.write(f"Encontradas {len(existing_position_triplets)} triplas já presentes na tabela 'positions'.")

        # Calcular a diferença: triplas em 'drivers' (via sessions) mas não em 'positions'
        triplets_to_process = sorted(list(relevant_driver_triplets - existing_position_triplets))

        self.stdout.write(self.style.SUCCESS(f"Identificadas {len(triplets_to_process)} triplas (M,S,D) que precisam de dados de positions."))
        return triplets_to_process

    def fetch_position_data(self, meeting_key, session_key, driver_number, use_token=True):
        """
        Busca dados de posição da API OpenF1 para uma tripla (meeting_key, session_key, driver_number) específica.
        """
        if not (meeting_key and session_key and driver_number):
            raise CommandError("meeting_key, session_key e driver_number devem ser fornecidos para buscar dados de position da API.")

        url = f"{self.API_URL}?meeting_key={meeting_key}&session_key={session_key}&driver_number={driver_number}" # Constrói a URL com os três filtros

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

    def build_position_instance(self, position_data_dict):
        """
        Constrói uma instância de modelo Position a partir de um dicionário de dados.
        """
        meeting_key = position_data_dict.get('meeting_key')
        session_key = position_data_dict.get('session_key')
        driver_number = position_data_dict.get('driver_number')
        date_str = position_data_dict.get('date')
        position_value = position_data_dict.get('position') # Nome do campo 'position' na API

        # Validação crítica para campos NOT NULL na PK (date, driver_number, meeting_key, session_key)
        if any(val is None for val in [date_str, driver_number, meeting_key, session_key]):
            missing_fields = [k for k,v in {'date': date_str, 'driver_number': driver_number, 'meeting_key': meeting_key, 'session_key': session_key}.items() if v is None]
            raise ValueError(f"Dados incompletos para Position: faltam {missing_fields}. Dados: {position_data_dict}")

        date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00')) if date_str else None

        return Position(
            date=date_obj,
            driver_number=driver_number,
            meeting_key=meeting_key,
            position=position_value, # Mapeia para o campo 'position' do modelo
            session_key=session_key
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

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação de Positions (ORM)..."))

        positions_inserted_db = 0
        positions_skipped_db = 0
        triplets_processed_count = 0 

        try:
            triplets_to_process = self.get_triplets_to_process()

            if not triplets_to_process:
                self.stdout.write(self.style.NOTICE("Nenhuma nova tripla (meeting_key, session_key, driver_number) encontrada para importar positions. Encerrando."))
                return

            self.stdout.write(self.style.SUCCESS(f"Total de {len(triplets_to_process)} triplas elegíveis para processamento."))

            api_delay = self.API_DELAY_SECONDS 

            for i, (meeting_key, session_key, driver_number) in enumerate(triplets_to_process):
                current_position_objects = []

                try:
                    self.stdout.write(f"Processando tripla {i+1}/{len(triplets_to_process)}: Mtg={meeting_key}, Sess={session_key}, Driver={driver_number}...")
                    triplets_processed_count += 1

                    position_data_from_api = self.fetch_position_data(
                        meeting_key=meeting_key, 
                        session_key=session_key, 
                        driver_number=driver_number, 
                        use_token=use_api_token_flag
                    )

                    if isinstance(position_data_from_api, dict) and "error_status" in position_data_from_api:
                        self.stdout.write(self.style.ERROR(f"  Erro na API para tripla (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}): {position_data_from_api['error_message']}. Pulando esta tripla."))
                        self.warnings_count += 1
                        continue

                    if not position_data_from_api:
                        self.stdout.write(self.style.WARNING(f"  Aviso: Nenhuma entrada de position encontrada na API para a tripla (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number})."))
                        self.warnings_count += 1
                        continue

                    for position_entry_dict in position_data_from_api:
                        try:
                            position_instance = self.build_position_instance(position_entry_dict)
                            if position_instance is not None:
                                current_position_objects.append(position_instance)
                        except Exception as build_e:
                            self.stdout.write(self.style.ERROR(f"Erro ao construir instância de position para tripla (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}): {build_e}. Pulando este registro."))
                            self.warnings_count += 1

                    if current_position_objects:
                        with transaction.atomic():
                            created_instances = Position.objects.bulk_create(current_position_objects, batch_size=self.BULK_SIZE, ignore_conflicts=True)
                            positions_inserted_db += len(created_instances)
                            positions_skipped_db += (len(current_position_objects) - len(created_instances))

                    else:
                        self.stdout.write(self.style.WARNING(f"  Aviso: Nenhuma entrada de position válida encontrada para inserção para Mtg={meeting_key}, Sess={session_key}, Driver={driver_number} (após construção de instância)."))
                        self.warnings_count += 1

                    if api_delay > 0:
                        time.sleep(api_delay)

                except Exception as triplet_overall_e:
                    self.stdout.write(self.style.ERROR(f"Erro ao processar tripla (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}): {triplet_overall_e}. Esta tripla não foi processada por completo. Pulando para a próxima tripla."))
                    self.warnings_count += 1


            self.stdout.write(self.style.SUCCESS("Importação de Positions concluída com sucesso para todas as triplas elegíveis!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Positions (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Positions (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Triplas (meeting_key, session_key, driver_number) processadas: {triplets_processed_count}"))
            self.stdout.write(self.style.SUCCESS(f"Registros inseridos no DB: {positions_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB): {positions_skipped_db}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de positions finalizada!"))