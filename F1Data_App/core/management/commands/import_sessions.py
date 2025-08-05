# G:\Learning\F1Data\F1Data_App\core\management\commands\import_sessions.py
import requests
import json
from datetime import datetime, timezone, timedelta
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings

from core.models import Sessions, Meetings
from dotenv import load_dotenv

# Importe o novo módulo de gerenciamento de token
from .token_manager import get_api_token

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

class Command(BaseCommand):
    help = 'Importa e/ou atualiza dados de sessões (eventos) da API OpenF1 e os insere na tabela sessions do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/sessions"
    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5

    def add_arguments(self, parser):
        parser.add_argument('--meeting_key', type=int, help='Especifica um Meeting Key para importar/atualizar (opcional).')
        parser.add_argument('--mode', choices=['I', 'U'], default='I',
                            help='Modo de operação: I=Insert apenas (padrão), U=Update (atualiza existentes e insere novos).')

    def add_warning(self, message):
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    def get_missing_meeting_keys_for_sessions(self):
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando meeting_keys sem sessões na tabela 'sessions'..."))
        all_meeting_keys = set(Meetings.objects.values_list('meeting_key', flat=True))
        sessions_meeting_keys = set(Sessions.objects.values_list('meeting_key', flat=True))
        missing_keys = sorted(list(all_meeting_keys - sessions_meeting_keys))

        if missing_keys:
            self.stdout.write(self.style.NOTICE(f"Meeting Keys sem sessões: {missing_keys[:10]}... (total {len(missing_keys)})"))
        else:
            self.stdout.write(self.style.SUCCESS("Todos os Meeting Keys existentes em 'Meetings' já possuem sessões em 'Sessions'."))
        return missing_keys

    def get_meeting_session_pairs_from_sessions(self, meeting_key_filter=None):
        """
        Obtém todos os pares (meeting_key, session_key) da tabela 'sessions'.
        Pode filtrar por um meeting_key específico.
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Obtendo pares (meeting_key, session_key) da tabela 'sessions'..."))
        query = Sessions.objects.all()
        if meeting_key_filter:
            query = query.filter(meeting_key=meeting_key_filter)

        session_pairs = sorted(list(query.values_list('meeting_key', 'session_key')))

        self.stdout.write(f"Encontrados {len(session_pairs)} pares (meeting_key, session_key) na tabela 'sessions'{' para o meeting_key especificado' if meeting_key_filter else ''}.")
        return session_pairs


    def fetch_sessions_data(self, meeting_key=None, api_token=None):
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

        self.stdout.write(self.style.MIGRATE_HEADING(f"Buscando dados de sessões de evento da API: {url}"))
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise CommandError(f"Erro ao buscar dados de sessões de evento da API ({url}): {e}")

    def create_partition_if_not_exists(self, table_name, session_key):
        partition_name = f"{table_name}_{session_key}"

        sql_check_exists = f"""
            SELECT EXISTS (
                SELECT 1
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = '{partition_name}' AND n.nspname = 'public'
            );
        """
        sql_create_partition = f"""
            CREATE TABLE IF NOT EXISTS {partition_name} PARTITION OF {table_name}
            FOR VALUES IN ({session_key});
        """

        try:
            with connection.cursor() as cursor:
                cursor.execute(sql_check_exists)
                exists = cursor.fetchone()[0]
                if not exists:
                    self.stdout.write(self.style.NOTICE(f"Criando partição {partition_name} para a tabela {table_name}..."))
                    cursor.execute(sql_create_partition)
                    self.stdout.write(self.style.SUCCESS(f"Partição {partition_name} para {table_name} criada com sucesso."))
                    return True
                else:
                    self.stdout.write(self.style.MIGRATE_HEADING(f"Partição {partition_name} para {table_name} já existe."))
                    return False
        except OperationalError as e:
            self.stdout.write(self.style.ERROR(f"Erro operacional de banco de dados ao criar partição para {table_name} (session_key={session_key}): {e}"))
            raise CommandError(f"Falha ao criar partição: {e}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Erro inesperado ao criar partição para {table_name} (session_key={session_key}): {e}"))
            raise CommandError(f"Falha ao criar partição: {e}")


    def process_session_entry(self, session_data, mode):
        try:
            if not isinstance(session_data, dict):
                raise ValueError(f"Dados de sessão inválidos: esperado dicionário, recebido {type(session_data)}")

            meeting_key = session_data.get('meeting_key')
            session_key = session_data.get('session_key')
            session_name = session_data.get('session_name')

            if any(val is None for val in [meeting_key, session_key, session_name]):
                missing_fields = [k for k, v in {'meeting_key': meeting_key, 'session_key': session_key, 'session_name': session_name}.items() if v is None]
                return 'skipped_missing_data'

            date_start_obj = datetime.fromisoformat(session_data.get('date_start', '').replace('Z', '+00:00')) if session_data.get('date_start') else None
            date_end_obj = datetime.fromisoformat(session_data.get('date_end', '').replace('Z', '+00:00')) if session_data.get('date_end') else None

            gmt_offset_str = session_data.get('gmt_offset')

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
                'gmt_offset': gmt_offset_str,
                'year': session_data.get('year'),
            }

            if mode == 'U':
                obj, created = Sessions.objects.update_or_create(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    defaults=defaults
                )
                if created:
                    return 'inserted'
                else:
                    return 'updated'
            else:
                if Sessions.objects.filter(meeting_key=meeting_key, session_key=session_key).exists():
                    return 'skipped'
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
                f"Erro FATAL ao processar sessão (meeting_key={meeting_key}, session_key={session_key}): {e}"
            ))
            raise

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        self.warnings_count = 0
        self.all_warnings_details = []

        meeting_key_param = options.get('meeting_key')
        mode_param = options.get('mode', 'I')

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando importação/atualização de Sessões de Evento..."))
        self.stdout.write(f"Parâmetros recebidos: meeting_key={meeting_key_param}, mode={mode_param}")

        sessions_found_api = 0
        sessions_inserted = 0
        sessions_updated = 0
        sessions_skipped = 0
        sessions_skipped_missing_data = 0
        partitions_created_success = 0
        partitions_creation_failed = 0


        try:
            api_token = None
            if use_api_token_flag:
                api_token = get_api_token(self)
            
            if not api_token and use_api_token_flag:
                self.stdout.write(self.style.WARNING("Falha ao obter token da API. Prosseguindo sem autenticação."))
                self.warnings_count += 1
                use_api_token_flag = False

            meetings_to_process_api_calls = []
            if meeting_key_param:
                meetings_to_process_api_calls.append(meeting_key_param)
                self.stdout.write(f"Modo: {mode_param} para meeting_key específico: {meeting_key_param}")
            elif mode_param == 'I':
                self.stdout.write("Modo: Insert apenas. Identificando meeting_keys sem sessões existentes...")
                meetings_to_process_api_calls = self.get_missing_meeting_keys_for_sessions()
                if not meetings_to_process_api_calls:
                    self.stdout.write(self.style.NOTICE("Nenhum meeting_key sem sessões encontrado para inserção. Encerrando."))
                    return
            else:
                self.stdout.write("Modo: Update geral. Buscando todas as sessões da API para atualização/inserção...")


            all_sessions_from_api = []
            if meeting_key_param or (mode_param == 'U' and not meetings_to_process_api_calls):
                all_sessions_from_api = self.fetch_sessions_data(meeting_key=meeting_key_param, api_token=api_token)
            elif mode_param == 'I' and meetings_to_process_api_calls:
                for m_key in meetings_to_process_api_calls:
                    sessions_for_key = self.fetch_sessions_data(meeting_key=m_key, api_token=api_token)
                    all_sessions_from_api.extend(sessions_for_key)
                    if self.API_DELAY_SECONDS > 0:
                        time.sleep(self.API_DELAY_SECONDS)

            sessions_found_api = len(all_sessions_from_api)
            self.stdout.write(f"Sessões encontradas na API para processamento: {sessions_found_api}")

            if not all_sessions_from_api:
                self.stdout.write(self.style.WARNING(f"Atenção: Nenhum dado retornado da API ou nenhum meeting_key para processar. Encerrando."))
                return

            for session_entry in all_sessions_from_api:
                try:
                    with transaction.atomic():
                        result = self.process_session_entry(session_entry, mode=mode_param)
                        if result == 'inserted':
                            sessions_inserted += 1
                            session_key_for_partition = session_entry.get('session_key')
                            if session_key_for_partition:
                                try:
                                    created_loc = self.create_partition_if_not_exists('location', session_key_for_partition)
                                    created_car = self.create_partition_if_not_exists('cardata', session_key_for_partition)
                                    if created_loc or created_car:
                                        partitions_created_success += 1
                                except CommandError:
                                    partitions_creation_failed += 1
                                    self.add_warning(f"Falha ao criar partição para session_key={session_key_for_partition}.")
                                except Exception as e:
                                    partitions_creation_failed += 1
                                    self.add_warning(f"Erro inesperado na criação de partição para session_key={session_key_for_partition}: {e}")
                            else:
                                self.add_warning(f"session_key ausente para criar partição (entry: {session_entry.get('meeting_key', 'N/A')}).")
                                sessions_skipped_missing_data += 1

                        elif result == 'updated':
                            sessions_updated += 1
                        elif result == 'skipped':
                            sessions_skipped += 1
                        elif result == 'skipped_missing_data':
                            sessions_skipped_missing_data += 1
                            self.add_warning(f"Sessão ignorada devido a dados obrigatórios ausentes: {session_entry.get('meeting_key', 'N/A')}-{session_entry.get('session_key', 'N/A')}")
                except Exception as processing_e:
                    self.stdout.write(self.style.ERROR(f"Erro ao processar UM REGISTRO de sessão (meeting_key={session_entry.get('meeting_key', 'N/A')}, session_key={session_entry.get('session_key', 'N/A')}): {processing_e}. Pulando para o próximo registro."))

                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Processamento de Sessões concluído!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação/atualização de sessões (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação/atualização de sessões (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo do Processamento de Sessões ---"))
            self.stdout.write(self.style.SUCCESS(f"Sessões encontradas na API: {sessions_found_api}"))
            self.stdout.write(self.style.SUCCESS(f"Sessões novas inseridas no DB: {sessions_inserted}"))
            self.stdout.write(self.style.SUCCESS(f"Sessões existentes atualizadas no DB: {sessions_updated}"))
            self.stdout.write(self.style.NOTICE(f"Sessões ignoradas (já existiam no DB em modo 'I'): {sessions_skipped}"))
            if sessions_skipped_missing_data > 0:
                self.stdout.write(self.style.WARNING(f"Sessões ignoradas (dados obrigatórios ausentes ou session_key para partição): {sessions_skipped_missing_data}"))

            self.stdout.write(self.style.SUCCESS(f"Partições para Location/Cardata criadas com sucesso: {partitions_created_success}"))
            if partitions_creation_failed > 0:
                self.stdout.write(self.style.ERROR(f"Falha na criação de partições: {partitions_creation_failed}"))

            self.stdout.write(self.style.WARNING(f"Total de Avisos: {self.warnings_count}"))
            if self.all_warnings_details:
                for msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {msg}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Processamento de sessões finalizado (ORM)!"))