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
            url = f"{self.API_URL}?meeting_key>={min_meeting_key}"

        headers = {"Accept": "application/json"}

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                self.warnings_count += 1 # Contabiliza o aviso
                raise CommandError("Token da API (OPENF1_API_TOKEN) não encontrado nas variáveis de ambiente. Verifique seu arquivo .env.")
            headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.warnings_count += 1 # Contabiliza o aviso
            # A mensagem geral de desativação do token é controlada no handle()
            pass 

        self.stdout.write(self.style.MIGRATE_HEADING(f"Buscando dados de sessões de evento da API: {url}"))
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise CommandError(f"Erro ao buscar dados de sessões de evento da API ({url}): {e}")

    def insert_session_entry(self, session_data):
        try:
            if not isinstance(session_data, dict):
                raise ValueError(f"Dados de sessão de evento inesperados: esperado um dicionário, mas recebeu {type(session_data)}: {session_data}")

            meeting_key = session_data.get('meeting_key')
            session_key = session_data.get('session_key')
            session_name = session_data.get('session_name')

            location = session_data.get('location')
            date_start_str = session_data.get('date_start')
            date_end_str = session_data.get('date_end')
            session_type = session_data.get('session_type')
            country_key = session_data.get('country_key')
            country_code = session_data.get('country_code')
            country_name = session_data.get('country_name')
            circuit_key = session_data.get('circuit_key')
            circuit_short_name = session_data.get('circuit_short_name')
            gmt_offset_str = session_data.get('gmt_offset')
            year = session_data.get('year')

            if any(val is None for val in [meeting_key, session_key, session_name]):
                missing_fields = [k for k, v in {'meeting_key': meeting_key, 'session_key': session_key, 'session_name': session_name}.items() if v is None]
                raise ValueError(f"Dados de sessão de evento incompletos para PK: faltam campos NOT NULL {missing_fields}. Dados API: {session_data}")

            date_start_obj = datetime.fromisoformat(date_start_str.replace('Z', '+00:00')) if date_start_str else None
            date_end_obj = datetime.fromisoformat(date_end_str.replace('Z', '+00:00')) if date_end_str else None

            gmt_offset_obj = None
            if gmt_offset_str:
                try:
                    parts = [int(p) for p in gmt_offset_str.split(':')]
                    gmt_offset_obj = timedelta(hours=parts[0], minutes=parts[1], seconds=parts[2])
                except Exception as e:
                    self.add_warning(f"Aviso: Não foi possível parsear gmt_offset '{gmt_offset_str}': {e}")

            Sessions.objects.create(
                meeting_key=meeting_key,
                session_key=session_key,
                session_name=session_name,
                location=location,
                date_start=date_start_obj,
                date_end=date_end_obj,
                session_type=session_type,
                country_key=country_key,
                country_code=country_code,
                country_name=country_name,
                circuit_key=circuit_key,
                circuit_short_name=circuit_short_name,
                gmt_offset=gmt_offset_obj,
                year=year
            )
            return True
        except IntegrityError:
            return False
        except Exception as e:
            data_debug = f"Mtg {session_data.get('meeting_key', 'N/A')}, Sess {session_data.get('session_key', 'N/A')}, Name {session_data.get('session_name', 'N/A')}"
            self.stdout.write(self.style.ERROR(f"Erro FATAL ao inserir registro de sessão de evento ({data_debug}): {e} - Dados API: {session_data}"))
            raise

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        self.warnings_count = 0
        self.all_warnings_details = [] # Zera a lista de detalhes de avisos para esta execução

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true' # <--- DEFINIÇÃO DA VARIÁVEL

        if use_api_token_flag:
            try:
                update_api_token_if_needed()
            except Exception as e:
                raise CommandError(f"Falha ao verificar/atualizar o token da API: {e}. Não é possível prosseguir com a importação.")
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no env.cfg). Buscando dados históricos."))

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação de Sessões de Evento (ORM)..."))

        sessions_found_api = 0
        sessions_inserted_db = 0
        sessions_skipped_db = 0
        total_race_session_driver_triplets_eligible = 0 # Variável para o sumário final
        sessions_api_calls_processed_count = 0 # Variável para o sumário final

        try:
            last_meeting_key_in_sessions_table = self.get_last_processed_meeting_key_from_sessions()
            # Passa use_token para fetch_sessions_data
            all_sessions_from_api = self.fetch_sessions_data(min_meeting_key=last_meeting_key_in_sessions_table, use_token=use_api_token_flag)
            sessions_found_api = len(all_sessions_from_api)
            sessions_to_process = all_sessions_from_api

            self.stdout.write(self.style.SUCCESS(f"Total de {sessions_found_api} sessões de evento encontradas na API (com meeting_key >= {last_meeting_key_in_sessions_table})."))
            self.stdout.write(self.style.SUCCESS(f"Total de {len(sessions_to_process)} sessões de evento a serem processadas para inserção."))

            if not sessions_to_process:
                self.stdout.write(self.style.NOTICE("Nenhuma nova sessão de evento encontrada para importar. Encerrando."))
                return

            api_delay = self.API_DELAY_SECONDS 

            for i, session_entry in enumerate(sessions_to_process):
                try:
                    with transaction.atomic():
                        # Remover a mensagem de progresso linha a linha
                        # if (i + 1) % 10 == 0 or (i + 1) == len(sessions_to_process):
                        #     meeting_key_debug = session_entry.get('meeting_key', 'N/A')
                        #     session_key_debug = session_entry.get('session_key', 'N/A')
                        #     self.stdout.write(f"Processando sessão de evento {i+1}/{len(sessions_to_process)} (Mtg {meeting_key_debug}, Sess {session_key_debug})...")

                        inserted = self.insert_session_entry(session_entry)
                        if inserted is True:
                            sessions_inserted_db += 1
                        elif inserted is False:
                            sessions_skipped_db += 1
                except Exception as session_atomic_e:
                    self.add_warning(
                        f"Erro FATAL na transação para sessão de evento (Mtg {session_entry.get('meeting_key', 'N/A')}, Sess {session_entry.get('session_key', 'N/A')}): {session_atomic_e}. Este item não foi inserido/atualizado."
                    )

                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Importação de Sessões de Evento concluída com sucesso para todos os registros elegíveis!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Sessões de Evento (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Sessões de Evento (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Sessões encontradas na API (total): {sessions_found_api}"))
            self.stdout.write(self.style.SUCCESS(f"Sessões novas a serem inseridas: {len(sessions_to_process)}"))
            self.stdout.write(self.style.SUCCESS(f"Sessões inseridas no DB: {sessions_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Sessões ignoradas (já existiam no DB): {sessions_skipped_db}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
                self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
                self.stdout.write(self.style.SUCCESS("Importação de sessões de evento finalizada!"))