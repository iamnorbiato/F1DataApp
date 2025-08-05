# G:\Learning\F1Data\F1Data_App\core\management\commands\import_meetings.py
import requests
import json
import os
from datetime import datetime
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings

from core.models import Meetings
from dotenv import load_dotenv

# Importe o novo módulo de gerenciamento de token
from .token_manager import get_api_token

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

class Command(BaseCommand):
    help = 'Importa e/ou atualiza dados de meetings da API OpenF1 e os insere na tabela meetings do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/meetings"
    CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json')

    API_DELAY_SECONDS = 0.2
    warnings_count = 0

    def add_arguments(self, parser):
        parser.add_argument('--meeting_key', type=int, help='Especifica um Meeting Key para importação/atualização (opcional).')
        parser.add_argument('--mode', choices=['I', 'U'], default='I',
                            help='Modo de operação: I=Insert apenas (padrão), U=Update (atualiza existentes e insere novos).')

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

    def get_last_meeting_key(self):
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

    def fetch_meetings_data(self, meeting_key=None, api_token=None):
        url = self.API_URL
        if meeting_key:
            url = f"{self.API_URL}?meeting_key={meeting_key}"

        headers = {
            "Accept": "application/json"
        }
        
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado ou token não disponível. Requisição será feita sem Authorization."))
            self.warnings_count += 1

        self.stdout.write(f"Buscando dados da API: {url}")
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise CommandError(f"Erro ao buscar dados da API: {e}")

    def process_meeting_entry(self, meeting_data, mode):
        meeting_key = meeting_data.get('meeting_key')
        meeting_code = meeting_data.get('meeting_code')
        meeting_name = meeting_data.get('meeting_name')
        location = meeting_data.get('location')
        country_key = meeting_data.get('country_key')
        country_code = meeting_data.get('country_code')
        country_name = meeting_data.get('country_name')
        circuit_key = meeting_data.get('circuit_key')
        circuit_short_name = meeting_data.get('circuit_short_name')
        date_start_str = meeting_data.get('date_start')
        gmt_offset = meeting_data.get('gmt_offset')
        meeting_official_name = meeting_data.get('meeting_official_name')
        year = meeting_data.get('year')

        date_start_obj = datetime.fromisoformat(date_start_str.replace('Z', '+00:00')) if date_start_str else None

        defaults = {
            'meeting_name': meeting_name,
            'location': location,
            'country_key': country_key,
            'country_code': country_code,
            'country_name': country_name,
            'circuit_key': circuit_key,
            'circuit_short_name': circuit_short_name,
            'meeting_code': meeting_code,
            'date_start': date_start_obj,
            'gmt_offset': gmt_offset,
            'meeting_official_name': meeting_official_name,
            'year': year
        }

        try:
            if mode == 'I':
                if Meetings.objects.filter(meeting_key=meeting_key).exists():
                    return False, 'skipped'
                Meetings.objects.create(meeting_key=meeting_key, **defaults)
                return True, 'inserted'
            elif mode == 'U':
                meeting, created = Meetings.objects.update_or_create(
                    meeting_key=meeting_key,
                    defaults=defaults
                )
                return True, 'inserted' if created else 'updated'
            return False, 'invalid_mode'

        except IntegrityError:
            return False, 'skipped'
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Erro FATAL ao processar meeting {meeting_data.get('meeting_key', 'N/A')}: {e} - Dados API: {meeting_data}"))
            raise

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        meeting_key_param = options.get('meeting_key')
        mode_param = options.get('mode')
        
        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação/atualização de Meetings (ORM)..."))
        self.stdout.write(f"Parâmetros recebidos: meeting_key={meeting_key_param}, mode={mode_param}")

        meetings_found_api = 0
        meetings_inserted_db = 0
        meetings_updated_db = 0
        meetings_skipped_db = 0

        api_token = None
        if use_api_token_flag:
            api_token = get_api_token(self)
        
        if not api_token and use_api_token_flag:
            self.stdout.write(self.style.WARNING("Falha ao obter token da API. Prosseguindo sem autenticação."))
            self.warnings_count += 1
            use_api_token_flag = False

        try:
            meetings_from_api = []
            if meeting_key_param:
                self.stdout.write(f"Buscando meeting_key específico: {meeting_key_param}...")
                meetings_from_api = self.fetch_meetings_data(meeting_key=meeting_key_param, api_token=api_token)
                if not meetings_from_api:
                    self.stdout.write(self.style.WARNING(f"Nenhum meeting encontrado na API para meeting_key={meeting_key_param}."))
            else:
                self.stdout.write("Buscando todos os meetings da API...")
                meetings_from_api = self.fetch_meetings_data(api_token=api_token)

            meetings_found_api = len(meetings_from_api)
            self.stdout.write(f"Meetings encontrados na API para processamento: {meetings_found_api}")

            if not meetings_from_api and meeting_key_param:
                self.stdout.write(self.style.WARNING(f"Atenção: Nenhum dado retornado da API para o meeting_key='{meeting_key_param}'. Não há o que processar."))
                return

            for meeting_entry in meetings_from_api:
                try:
                    success, status = self.process_meeting_entry(meeting_entry, mode_param)
                    if success:
                        if status == 'inserted':
                            meetings_inserted_db += 1
                        elif status == 'updated':
                            meetings_updated_db += 1
                    else:
                        if status == 'skipped':
                            meetings_skipped_db += 1
                        else:
                            self.stdout.write(self.style.ERROR(f"Não foi possível processar o meeting {meeting_entry.get('meeting_key', 'N/A')} devido a um modo inválido ou erro interno."))
                except Exception as meeting_process_e:
                    self.stdout.write(self.style.ERROR(f"Erro ao processar/inserir/atualizar UM REGISTRO de meeting: {meeting_process_e}. Pulando para o próximo registro."))
                    self.warnings_count += 1

                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Processamento de Meetings concluído!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação/atualização (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação/atualização de meetings (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo do Processamento de Meetings (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Meetings encontrados na API: {meetings_found_api}"))
            self.stdout.write(self.style.SUCCESS(f"Meetings novos inseridos no DB: {meetings_inserted_db}"))
            self.stdout.write(self.style.SUCCESS(f"Meetings existentes atualizados no DB: {meetings_updated_db}"))
            self.stdout.write(self.style.NOTICE(f"Meetings ignorados (já existiam no DB em modo 'I'): {meetings_skipped_db}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Processamento de meetings finalizado (ORM)!"))