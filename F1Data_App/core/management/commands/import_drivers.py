# G:\Learning\F1Data\F1Data_App\core\management\commands\import_drivers.py

import requests
import json
from datetime import datetime
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction

from core.models import Drivers, Meetings, Sessions
from dotenv import load_dotenv
from update_token import update_api_token_if_needed

# --- CORREÇÃO AQUI: Usa settings.BASE_DIR para o caminho do env.cfg ---
ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')
# -----------------------------------------------------------------------

class Command(BaseCommand):
    help = 'Importa dados de pilotos (drivers) da API OpenF1 de forma eficiente e os insere na tabela drivers do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/drivers"
    CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json')
    API_DELAY_SECONDS = 0.2

    def get_config_value(self, key=None, default=None, section=None):
        config = {}
        if not os.path.exists(self.CONFIG_FILE):
            self.stdout.write(self.style.WARNING(f"Aviso: Arquivo de configuração '{self.CONFIG_FILE}' não encontrado. Usando valor padrão para '{key}'."))
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

    def get_last_processed_meeting_key_from_drivers(self):
        self.stdout.write(self.style.MIGRATE_HEADING("Verificando o último meeting_key processado na tabela 'drivers'..."))
        try:
            last_driver = Drivers.objects.order_by('-meeting_key').first()
            if last_driver:
                self.stdout.write(f"Último meeting_key encontrado na tabela 'drivers': {last_driver.meeting_key}")
                return last_driver.meeting_key
            self.stdout.write("Nenhum meeting_key encontrado no DB. Começando do zero.")
            return 0
        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados ao buscar o último meeting_key: {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado ao buscar o último meeting_key: {e}")

    def fetch_drivers_data(self, min_meeting_key=0, use_token=True):
        url = self.API_URL
        if min_meeting_key > 0:
            url = f"{self.API_URL}?meeting_key>{min_meeting_key}"

        headers = {"Accept": "application/json"}

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                self.warnings_count += 1
                raise CommandError("Token da API (OPENF1_API_TOKEN) não encontrado. Requisição será feita sem Authorization.")
            else:
                headers["Authorization"] = f"Bearer {api_token}"
        else:
            # A mensagem geral de desativação do token será controlada no handle()
            pass 

        self.stdout.write(self.style.MIGRATE_HEADING(f"Buscando dados de drivers da API: {url}"))
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise CommandError(f"Erro ao buscar dados de drivers da API ({url}): {e}")

    def insert_driver_entry(self, driver_data):
        try:
            if not isinstance(driver_data, dict):
                raise ValueError(f"Dados de driver inesperados: {type(driver_data)}: {driver_data}")

            meeting_key = driver_data.get('meeting_key')
            session_key = driver_data.get('session_key')
            driver_number = driver_data.get('driver_number')
            broadcast_name = driver_data.get('broadcast_name')
            full_name = driver_data.get('full_name')
            name_acronym = driver_data.get('name_acronym')
            team_name = driver_data.get('team_name')
            team_colour = driver_data.get('team_colour')
            first_name = driver_data.get('first_name')
            last_name = driver_data.get('last_name')
            headshot_url = driver_data.get('headshot_url')
            country_code = driver_data.get('country_code')

            if any(val is None for val in [meeting_key, session_key, driver_number]):
                missing = [k for k, v in {'meeting_key': meeting_key, 'session_key': session_key, 'driver_number': driver_number}.items() if v is None]
                raise ValueError(f"Faltam campos NOT NULL para PK: {missing}. Dados: {driver_data}")

            Drivers.objects.create(
                meeting_key=meeting_key,
                session_key=session_key,
                driver_number=driver_number,
                broadcast_name=broadcast_name,
                full_name=full_name,
                name_acronym=name_acronym,
                team_name=team_name,
                team_colour=team_colour,
                first_name=first_name,
                last_name=last_name,
                headshot_url=headshot_url,
                country_code=country_code
            )
            return True
        except IntegrityError:
            return False
        except Exception as e:
            data_debug = f"driver_number={driver_data.get('driver_number')}, meeting_key={driver_data.get('meeting_key')}"
            self.stdout.write(self.style.ERROR(f"Erro FATAL ao inserir registro de driver ({data_debug}): {e}"))
            raise

    def handle(self, *args, **options):
        load_dotenv()

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        if use_api_token_flag:
            try:
                update_api_token_if_needed()
            except Exception as e:
                raise CommandError(f"Falha ao verificar/atualizar o token da API: {e}")
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no .env)."))

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação de Drivers (ORM)..."))

        drivers_found_api = 0
        drivers_inserted_db = 0
        drivers_skipped_db = 0

        try:
            last_meeting_key = self.get_last_processed_meeting_key_from_drivers()
            all_drivers = self.fetch_drivers_data(min_meeting_key=last_meeting_key, use_token=use_api_token_flag)
            drivers_found_api = len(all_drivers)

            if not all_drivers:
                self.stdout.write(self.style.NOTICE("Nenhum novo driver encontrado. Encerrando."))
                return

            for i, driver_data in enumerate(all_drivers):
                try:
                    with transaction.atomic():
                        inserted = self.insert_driver_entry(driver_data)
                        if inserted:
                            drivers_inserted_db += 1
                        else:
                            drivers_skipped_db += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Erro ao processar driver {i+1}: {e}"))
                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Importação de Drivers finalizada com sucesso!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional no DB: {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado na importação: {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Drivers ---"))
            self.stdout.write(self.style.SUCCESS(f"Drivers encontrados na API: {drivers_found_api}"))
            self.stdout.write(self.style.SUCCESS(f"Drivers inseridos: {drivers_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Drivers ignorados (já existiam): {drivers_skipped_db}"))
            self.stdout.write(self.style.MIGRATE_HEADING("----------------------------------------"))
