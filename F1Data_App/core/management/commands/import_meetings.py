# G:\Learning\F1Data\F1Data_App\core\management\commands\import_meetings.py
import requests
import json
import os
from datetime import datetime
import time # Para o sleep da API

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings

from core.models import Meetings
from dotenv import load_dotenv 
from update_token import update_api_token_if_needed # Importa a função de atualização de token

# --- CORREÇÃO AQUI: Usa settings.BASE_DIR para o caminho do env.cfg ---
ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')
# -----------------------------------------------------------------------

class Command(BaseCommand):
    help = 'Importa dados de meetings da API OpenF1 e os insere na tabela meetings do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/meetings"
    CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json')

    API_DELAY_SECONDS = 0.2 # Delay de 0.2 segundos entre as chamadas da API (ajuste conforme necessário)

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

    def get_last_meeting_key(self):
        """
        Obtém o último meeting_key da tabela meetings no banco de dados local.
        """
        self.stdout.write("Verificando o último meeting_key no banco de dados local (ORM)...")
        try:
            last_meeting = Meetings.objects.order_by('-meeting_key').first()
            if last_meeting:
                self.stdout.write(f"Último meeting_key no DB (ORM): {last_meeting.meeting_key}")
                return last_meeting.meeting_key
            self.stdout.write("Nenhum meeting_key encontrado no DB. Começando do zero.")
            return 0
        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados ao buscar o último meeting_key: {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado ao buscar o último meeting_key: {e}")

    def fetch_meetings_data(self, last_meeting_key, use_token=True): # <--- ADICIONADO: use_token
        """
        Busca dados de meetings da API OpenF1, filtrando por meeting_key > last_meeting_key.
        use_token: Se True, usa o token de autorização. Se False, não.
        """
        url = f"{self.API_URL}?meeting_key>{last_meeting_key}"

        headers = {
            "Accept": "application/json"
        }

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

        self.stdout.write(f"Buscando dados da API: {url}")
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise CommandError(f"Erro ao buscar dados da API: {e}")

    def insert_meeting_entry(self, meeting_data):
        """
        Insere um único registro de meeting no banco de dados usando ORM.
        Usa ON CONFLICT (meeting_key) DO NOTHING para evitar duplicatas.
        """
        try:
            meeting_key = meeting_data.get('meeting_key')
            meeting_name = meeting_data.get('meeting_name')
            location = meeting_data.get('location')
            country_key = meeting_data.get('country_key')
            country_code = meeting_data.get('country_code')
            country_name = meeting_data.get('country_name')
            circuit_key = meeting_data.get('circuit_key')
            circuit_short_name = meeting_data.get('circuit_short_name')
            date_start_str = meeting_data.get('date_start')
#            date_end_str = meeting_data.get('date_end')
            gmt_offset = meeting_data.get('gmt_offset')
            meeting_official_name = meeting_data.get('meeting_official_name')
            year = meeting_data.get('year')

            date_start_obj = datetime.fromisoformat(date_start_str.replace('Z', '+00:00')) if date_start_str else None
#           date_end_obj = datetime.fromisoformat(date_end_str.replace('Z', '+00:00')) if date_end_str else None

            Meetings.objects.create(
                meeting_key=meeting_key,
                meeting_name=meeting_name,
                location=location,
                country_key=country_key,
                country_code=country_code,
                country_name=country_name,
                circuit_key=circuit_key,
                circuit_short_name=circuit_short_name,
                date_start=date_start_obj,
#                date_end=date_end_obj,
                gmt_offset=gmt_offset,
                meeting_official_name=meeting_official_name,
                year=year
            )
            return True
        except IntegrityError:
            return False
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Erro FATAL ao inserir/processar registro de meeting {meeting_data.get('meeting_key', 'N/A')}: {e} - Dados API: {meeting_data}"))
            raise

    def handle(self, *args, **options):
        load_dotenv() # Carrega as variáveis do .env

        # >>>>> ADICIONADO: Lógica para usar/não usar o token <<<<<
        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true' # Lê a flag do .env

        if use_api_token_flag:
            try:
                update_api_token_if_needed() # Verifica/renova o token
            except Exception as e:
                raise CommandError(f"Falha ao verificar/atualizar o token da API: {e}. Não é possível prosseguir com a importação.")
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no .env). Buscando dados históricos."))

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação de Meetings (ORM)..."))

        meetings_found_api = 0
        meetings_inserted_db = 0
        meetings_skipped_db = 0

        try:
            last_meeting_key = self.get_last_meeting_key()
            # Passa a flag use_api_token_flag para fetch_meetings_data
            meetings_from_api = self.fetch_meetings_data(last_meeting_key, use_token=use_api_token_flag) 

            self.stdout.write(f"Meetings encontrados na API: {len(meetings_from_api)}")

            for meeting_entry in meetings_from_api:
                try:
                    inserted = self.insert_meeting_entry(meeting_entry)
                    if inserted:
                        meetings_inserted_db += 1
                    else:
                        meetings_skipped_db += 1
                except Exception as meeting_insert_e:
                    self.stdout.write(self.style.ERROR(f"Erro ao processar/inserir UM REGISTRO de meeting: {meeting_insert_e}. Pulando para o próximo registro."))

            if self.API_DELAY_SECONDS > 0:
                time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Importação de Meetings concluída com sucesso!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de meetings (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Meetings (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Meetings encontrados na API: {len(meetings_from_api) if 'meetings_from_api' in locals() else 0}"))
            self.stdout.write(self.style.SUCCESS(f"Meetings novos a serem inseridos: {meetings_inserted_db}"))
            self.stdout.write(self.style.SUCCESS(f"Meetings inseridos no DB: {meetings_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Meetings ignorados (já existiam no DB): {meetings_skipped_db}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de meetings finalizada (ORM)!"))