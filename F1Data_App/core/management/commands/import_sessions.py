# G:\Learning\F1Data\F1Data_App\core\management\commands\import_sessions.py
import requests
import json
from datetime import datetime, timezone, timedelta
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings 

from core.models import Sessions 
from dotenv import load_dotenv 
from update_token import update_api_token_if_needed 
import pytz 

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg') 

class Command(BaseCommand):
    help = 'Importa dados de sessões (eventos) da API OpenF1 e os insere na tabela sessions do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/sessions"
    API_DELAY_SECONDS = 0.2

    def add_arguments(self, parser):
        parser.add_argument('--meeting_key', type=int, help='Meeting Key específico para importar')
        parser.add_argument('--mode', choices=['I', 'U'], default='I', help='Modo de operação: I = Insert apenas, U = Insert e Update')

    def get_last_processed_meeting_key_from_sessions(self):
        self.stdout.write(self.style.MIGRATE_HEADING("Verificando o último meeting_key processado na tabela 'sessions'..."))
        last_key = 0
        try:
            last_session_entry = Sessions.objects.order_by('-meeting_key').first()
            if last_session_entry:
                last_key = last_session_entry.meeting_key

            self.stdout.write(self.style.SUCCESS(f"Último meeting_key encontrado na tabela 'sessions': {last_key}"))
            return last_key
        except Exception as e:
            raise CommandError(f"Erro ao buscar o último meeting_key na tabela 'sessions' via ORM: {e}")

    def fetch_sessions_data(self, min_meeting_key=0, use_token=True):
        url = self.API_URL
        if min_meeting_key > 0:
            url = f"{self.API_URL}?meeting_key={min_meeting_key}"

        headers = {"Accept": "application/json"}

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                self.warnings_count += 1
                raise CommandError("Token da API (OPENF1_API_TOKEN) não encontrado nas variáveis de ambiente. Verifique seu arquivo .env.")
            headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.warnings_count += 1

        self.stdout.write(self.style.MIGRATE_HEADING(f"Buscando dados de sessões de evento da API: {url}"))
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise CommandError(f"Erro ao buscar dados de sessões de evento da API ({url}): {e}")

    def insert_session_entry(self, session_data, mode='I'):
        try:
            if not isinstance(session_data, dict):
                raise ValueError(f"Dados de sessão inválidos: esperado dicionário, recebido {type(session_data)}")

            meeting_key = session_data.get('meeting_key')
            session_key = session_data.get('session_key')
            session_name = session_data.get('session_name')

            if any(val is None for val in [meeting_key, session_key, session_name]):
                missing_fields = [k for k, v in {'meeting_key': meeting_key, 'session_key': session_key, 'session_name': session_name}.items() if v is None]
                raise ValueError(f"Campos obrigatórios ausentes: {missing_fields}")

            date_start_obj = datetime.fromisoformat(session_data.get('date_start', '').replace('Z', '+00:00')) if session_data.get('date_start') else None
            date_end_obj = datetime.fromisoformat(session_data.get('date_end', '').replace('Z', '+00:00')) if session_data.get('date_end') else None

            gmt_offset_str = session_data.get('gmt_offset')
            gmt_offset_obj = None
            if gmt_offset_str:
                try:
                    parts = [int(p) for p in gmt_offset_str.split(':')]
                    gmt_offset_obj = timedelta(hours=parts[0], minutes=parts[1], seconds=parts[2])
                except Exception as e:
                    self.add_warning(f"Aviso: Falha ao converter gmt_offset '{gmt_offset_str}': {e}")

            defaults = {
                'session_name': session_name,
                'location': session_data.get('location'),
                'date_start': date_start_obj,
                'date_end': date_end_obj,
                'session_type': session_data.get('session_type'),
                'country_key': session_data.get('country_key'),
                'country_code': session_data.get('country_code'),
                'country_name': session_data.get('country_name'),
                'circuit_key': session_data.get('circuit_key'),
                'circuit_short_name': session_data.get('circuit_short_name'),
                'gmt_offset': gmt_offset_obj,
                'year': session_data.get('year'),
            }

            if mode == 'U':
                obj, created = Sessions.objects.update_or_create(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    defaults=defaults
                )
                return 'inserted' if created else 'updated'
            else:  # mode == 'I'
                Sessions.objects.create(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    **defaults
                )
                return 'inserted'

        except IntegrityError:
            return 'skipped'
        except Exception as e:
            self.stdout.write(self.style.ERROR(
                f"Erro ao processar sessão (meeting_key={meeting_key}, session_key={session_key}): {e}"
            ))
            raise

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        self.warnings_count = 0
        self.all_warnings_details = []

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        if use_api_token_flag:
            try:
                update_api_token_if_needed()
            except Exception as e:
                raise CommandError(f"Erro ao atualizar token da API: {e}")
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no env.cfg)."))

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando importação de Sessões de Evento..."))

        sessions_found_api = 0
        sessions_inserted = 0
        sessions_updated = 0
        sessions_skipped = 0

        meeting_key_filter = options.get('meeting_key')
        mode = options.get('mode', 'I')

        try:
            if meeting_key_filter:
                self.stdout.write(self.style.MIGRATE_HEADING(f"Importando meeting_key específico: {meeting_key_filter}"))
                min_meeting_key = meeting_key_filter
            else:
                min_meeting_key = self.get_last_processed_meeting_key_from_sessions()

            all_sessions_from_api = self.fetch_sessions_data(min_meeting_key=min_meeting_key, use_token=use_api_token_flag)
            sessions_found_api = len(all_sessions_from_api)

            if not all_sessions_from_api:
                self.stdout.write(self.style.NOTICE("Nenhuma nova sessão de evento encontrada. Encerrando."))
                return

            for session_entry in all_sessions_from_api:
                try:
                    with transaction.atomic():
                        result = self.insert_session_entry(session_entry, mode=mode)
                        if result == 'inserted':
                            sessions_inserted += 1
                        elif result == 'updated':
                            sessions_updated += 1
                        elif result == 'skipped':
                            sessions_skipped += 1
                except Exception:
                    continue

                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

        except Exception as e:
            raise CommandError(f"Erro geral na importação de sessões: {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação ---"))
            self.stdout.write(self.style.SUCCESS(f"Sessões encontradas na API: {sessions_found_api}"))
            self.stdout.write(self.style.SUCCESS(f"Sessões inseridas: {sessions_inserted}"))
            self.stdout.write(self.style.SUCCESS(f"Sessões atualizadas: {sessions_updated}"))
            self.stdout.write(self.style.NOTICE(f"Sessões ignoradas (já existentes): {sessions_skipped}"))
            self.stdout.write(self.style.WARNING(f"Total de avisos: {self.warnings_count}"))
            if self.all_warnings_details:
                for msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {msg}"))

    def add_warning(self, msg):
        self.all_warnings_details.append(msg)
        self.warnings_count += 1
