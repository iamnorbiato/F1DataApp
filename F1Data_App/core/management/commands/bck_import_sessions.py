# G:\Learning\F1Data\F1Data_App\core\management\commands\import_sessions.py
import requests
import json
from datetime import datetime
import os

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from psycopg2 import OperationalError

class Command(BaseCommand):
    help = 'Importa dados de sessões da API OpenF1 e os insere na tabela sessions do PostgreSQL.'

    API_URL = "https://api.openf1.org/v1/position"
    CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json')

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
                return section_data if key is None else section_data.get(key, default)
            else:
                return config if key is None else config.get(key, default)

        except json.JSONDecodeError as e:
            raise CommandError(f"Erro ao ler/parsear o arquivo de configuração JSON '{self.CONFIG_FILE}': {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado ao acessar o arquivo de configuração: {e}")

    def get_meeting_keys_to_process(self):
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando meeting_keys a processar..."))
        meeting_keys_in_meetings = set()
        meeting_keys_in_sessions = set()

        try:
            with connection.cursor() as cursor:
                self.stdout.write("Buscando todos os meeting_keys da tabela 'meetings'...")
                cursor.execute("SELECT DISTINCT meeting_key FROM meetings;")
                meeting_keys_in_meetings.update(row[0] for row in cursor.fetchall())
                self.stdout.write(f"Encontrados {len(meeting_keys_in_meetings)} meeting_keys na tabela 'meetings'.")

                self.stdout.write("Buscando meeting_keys já presentes na tabela 'sessions'...")
                cursor.execute("SELECT DISTINCT meeting_key FROM sessions;")
                meeting_keys_in_sessions.update(row[0] for row in cursor.fetchall())
                self.stdout.write(f"Encontrados {len(meeting_keys_in_sessions)} meeting_keys na tabela 'sessions'.")

            meeting_keys_to_process = sorted(list(meeting_keys_in_meetings - meeting_keys_in_sessions))
            self.stdout.write(self.style.SUCCESS(f"Identificados {len(meeting_keys_to_process)} meeting_keys que precisam de dados de sessões."))
            return meeting_keys_to_process

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados ao buscar meeting_keys: {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado ao identificar meeting_keys a processar: {e}")

    def fetch_sessions_data(self, meeting_key):
        if not meeting_key:
            raise CommandError("meeting_key deve ser fornecido para buscar dados de sessões da API.")

        url = f"{self.API_URL}?meeting_key={meeting_key}"
        self.stdout.write(self.style.MIGRATE_HEADING(f"Buscando dados de sessões para meeting_key {meeting_key} da API: {url}"))
        try:
            response = requests.get(url)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise CommandError(f"Erro ao buscar dados da API: {e}")

    def insert_session(self, cursor, session_data):
        try:
            if not isinstance(session_data, dict):
                raise ValueError(f"Dados de sessão inesperados: esperado um dicionário, mas recebeu {type(session_data)}: {session_data}")

            session_key = session_data.get('session_key')
            date_str = session_data.get('date')
            position = session_data.get('position')
            meeting_key = session_data.get('meeting_key')
            driver_number = session_data.get('driver_number')

            if any(val is None for val in [session_key, date_str, position, meeting_key, driver_number]):
                missing_fields = [k for k,v in {'session_key': session_key, 'date': date_str, 'position': position, 'meeting_key': meeting_key, 'driver_number': driver_number}.items() if v is None]
                raise ValueError(f"Dados de sessão incompletos para PK: faltam campos NOT NULL {missing_fields}. Dados API: {session_data}")

            date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))

            sql = """
            INSERT INTO sessions (
                session_key, date, "position", meeting_key, driver_number
            ) VALUES (
                %s, %s, %s, %s, %s
            ) ON CONFLICT (session_key, date, "position", meeting_key, driver_number) DO NOTHING;
            """
            values = (session_key, date_obj, position, meeting_key, driver_number)
            cursor.execute(sql, values)
            return cursor.rowcount > 0

        except Exception as e:
            data_debug = f"session_key={session_data.get('session_key') if isinstance(session_data, dict) else 'N/A'}, meeting_key={session_data.get('meeting_key') if isinstance(session_data, dict) else 'N/A'}"
            self.stdout.write(self.style.ERROR(f"Erro FATAL ao inserir/processar registro de sessão ({data_debug}): {e} - Dados API: {session_data}"))
            raise

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação de Sessions..."))

        sessions_inserted_db = 0
        sessions_skipped_db = 0
        meeting_keys_processed_count = 0 

        try:
            meeting_keys_to_process = self.get_meeting_keys_to_process()
            if not meeting_keys_to_process:
                self.stdout.write(self.style.NOTICE("Nenhum novo meeting_key encontrado para importar sessões. Encerrando."))
                return

            self.stdout.write(self.style.SUCCESS(f"Total de {len(meeting_keys_to_process)} meeting_keys elegíveis para processamento."))
            self.stdout.write(f"Iniciando loop por {len(meeting_keys_to_process)} meeting_keys...")

            with connection.cursor() as cursor:
                for i, meeting_key in enumerate(meeting_keys_to_process):
                    self.stdout.write(f"Processando meeting_key {i+1}/{len(meeting_keys_to_process)}: {meeting_key}...")
                    meeting_keys_processed_count += 1
                    
                    sessions_data_from_api = self.fetch_sessions_data(meeting_key=meeting_key)
                    if not sessions_data_from_api:
                        self.stdout.write(self.style.WARNING(f"Aviso: Nenhuma sessão encontrada na API para meeting_key {meeting_key}."))
                        continue

                    self.stdout.write(f"Encontradas {len(sessions_data_from_api)} sessões para meeting_key {meeting_key}. Inserindo...")

                    for session in sessions_data_from_api:
                        try:
                            inserted = self.insert_session(cursor, session)
                            if inserted:
                                sessions_inserted_db += 1
                            else:
                                sessions_skipped_db += 1
                        except Exception as session_insert_e:
                            self.stdout.write(self.style.ERROR(f"Erro ao processar/inserir UMA SESSÃO para meeting_key {meeting_key}: {session_insert_e}. Pulando para a próxima sessão."))

            connection.commit()
            self.stdout.write(self.style.SUCCESS("Importação de Sessions concluída com sucesso!"))

        except OperationalError as e:
            connection.rollback()
            raise CommandError(f"Erro operacional de banco de dados durante a importação: {e}")
        except Exception as e:
            connection.rollback()
            raise CommandError(f"Erro inesperado durante a importação de Sessions: {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Sessions ---"))
            self.stdout.write(self.style.SUCCESS(f"Meeting_keys processados: {meeting_keys_processed_count}"))
            self.stdout.write(self.style.SUCCESS(f"Sessões inseridas no DB: {sessions_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Sessões ignoradas (já existiam no DB): {sessions_skipped_db}"))
            self.stdout.write(self.style.MIGRATE_HEADING("-------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de sessões finalizada!"))
