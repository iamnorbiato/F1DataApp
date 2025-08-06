# G:\Learning\F1Data\F1Data_App\core\management\commands\import_position.py

import requests
import json
from datetime import datetime, timezone, timedelta
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings

from core.models import Drivers, Sessions, Position, Meetings
from dotenv import load_dotenv
from .token_manager import get_api_token

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')


class Command(BaseCommand):
    help = 'Importa dados de posição (position) da API OpenF1 e os insere na tabela positions do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/position"
    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5
    BULK_SIZE = 5000
    warnings_count = 0

    def add_arguments(self, parser):
        parser.add_argument('--meeting_key', type=int, help='Especifica um Meeting Key para importar/atualizar (opcional).')
        parser.add_argument('--session_key', type=int, help='Especifica um Session Key para importar/atualizar (opcional).')
        parser.add_argument('--mode', type=str, choices=['I', 'U'], default='I', help='Modo de operação: I=Insert, U=Update.')

    def add_warning(self, message):
        if not hasattr(self, 'warnings_count'):
            self.warnings_count = 0
            self.all_warnings_details = []
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    def get_meetings_to_discover(self):
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando Meetings na tabela 'Meetings' que não existem na tabela 'Positions'..."))
        all_meeting_keys = set(Meetings.objects.values_list('meeting_key', flat=True).distinct())
        existing_position_meeting_keys = set(Position.objects.values_list('meeting_key', flat=True).distinct())
        meetings_to_fetch = sorted(list(all_meeting_keys - existing_position_meeting_keys))
        self.stdout.write(f"Encontrados {len(meetings_to_fetch)} Meetings para os quais buscar dados de Positions.")
        return meetings_to_fetch

    def get_sessions_for_meeting(self, meeting_key):
        return list(Sessions.objects.filter(meeting_key=meeting_key).values_list('session_key', flat=True).distinct())

    def fetch_position_data(self, meeting_key=None, session_key=None, api_token=None):
        if not meeting_key and not session_key:
            raise CommandError("Pelo menos 'meeting_key' ou 'session_key' devem ser fornecidos.")
        params = {}
        if meeting_key is not None:
            params['meeting_key'] = meeting_key
        if session_key is not None:
            params['session_key'] = session_key

        query_string = "&".join([f"{k}={v}" for k, v in params.items() if v is not None])
        url = f"{self.API_URL}?{query_string}"
        self.stdout.write(f"  Chamando API Positions com URL: {url}")
        headers = {"Accept": "application/json"}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.add_warning("Uso do token desativado ou token não disponível.")

        for attempt in range(self.API_MAX_RETRIES):
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                status_code = getattr(response, 'status_code', 'Unknown')
                error_url = response.url if hasattr(response, 'url') else url
                error_msg = f"Erro {status_code} da API: {error_url} - {e}"
                if status_code in [500, 502, 503, 504, 401, 403, 422] and attempt < self.API_MAX_RETRIES - 1:
                    delay = self.API_RETRY_DELAY_SECONDS * (2 ** attempt)
                    self.add_warning(f"{error_msg}. Retentando em {delay} segundos...")
                    time.sleep(delay)
                else:
                    self.add_warning(f"Falha na API após retries: {error_msg}")
                    return {"error_status": status_code, "error_url": error_url, "error_message": str(e)}
        self.add_warning(f"Falha definitiva para {url}")
        return {"error_status": "Failed after retries", "error_url": url, "error_message": "Max retries exceeded."}

    def create_position_instance(self, position_data_dict):
        def to_datetime_aware(val):
            if not val:
                return None
            try:
                return datetime.fromisoformat(val).astimezone(timezone.utc)
            except ValueError:
                return None

        meeting_key = position_data_dict.get('meeting_key')
        session_key = position_data_dict.get('session_key')
        driver_number = position_data_dict.get('driver_number')
        date_str = position_data_dict.get('date')
        position_value = position_data_dict.get('position')

        if any(val is None for val in [date_str, driver_number, meeting_key, session_key]):
            missing = [k for k, v in {'date': date_str, 'driver_number': driver_number, 'meeting_key': meeting_key, 'session_key': session_key}.items() if v is None]
            raise ValueError(f"Dados incompletos para Position: faltam {missing}. Dados: {position_data_dict}")

        date_obj = to_datetime_aware(date_str)
        if date_obj is None:
            raise ValueError(f"Formato de data inválido: '{date_str}'.")

        return Position(
            date=date_obj,
            driver_number=driver_number,
            meeting_key=meeting_key,
            position=position_value,
            session_key=session_key
        )

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        self.warnings_count = 0
        self.all_warnings_details = []

        meeting_key_param = options.get('meeting_key')
        session_key_param = options.get('session_key')
        mode_param = options.get('mode')

        if session_key_param and not meeting_key_param:
            raise CommandError("Erro: Se 'session_key' for fornecido, 'meeting_key' também deve ser.")
        
        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação/atualização de Positions (ORM)..."))
        self.stdout.write(f"Parâmetros recebidos: meeting_key={meeting_key_param or 'Nenhum'}, session_key={session_key_param or 'Nenhum'}, mode={mode_param or 'Automático'}")

        positions_found_api_total = 0
        positions_inserted_db = 0
        positions_updated_db = 0
        positions_skipped_db = 0
        positions_skipped_missing_data = 0
        positions_skipped_invalid_date = 0
        api_call_errors = 0

        api_calls_info = []

        actual_mode = mode_param
        if not actual_mode:
            actual_mode = 'U' if meeting_key_param or session_key_param else 'I'

        self.stdout.write(self.style.NOTICE(f"Modo de operação FINAL: '{actual_mode}'."))

        if not meeting_key_param and not session_key_param:
            self.stdout.write(self.style.NOTICE("Modo AUTOMÁTICO: Descoberta de novos Meetings."))
            meetings_to_discover = self.get_meetings_to_discover()
            if not meetings_to_discover:
                self.stdout.write(self.style.NOTICE("Nenhum Meeting novo para processar."))
                return
            for m_key in meetings_to_discover:
                session_keys = self.get_sessions_for_meeting(m_key)
                if not session_keys:
                    self.add_warning(f"Meeting {m_key} não possui sessões. Pulando.")
                    continue
                for s_key in session_keys:
                    api_calls_info.append({'meeting_key': m_key, 'session_key': s_key})
        else:
            self.stdout.write(self.style.NOTICE("Modo DIRECIONADO: Importação com filtros."))
            call_params = {}
            if meeting_key_param:
                call_params['meeting_key'] = meeting_key_param
            if session_key_param:
                call_params['session_key'] = session_key_param
            api_calls_info.append(call_params)

        self.stdout.write(self.style.SUCCESS(f"Total de {len(api_calls_info)} chamadas de API a serem feitas."))

        api_token = get_api_token(self) if use_api_token_flag else None
        if not api_token and use_api_token_flag:
            self.add_warning("Falha ao obter token. Prosseguindo sem autenticação.")

        try:
            for i, call_params in enumerate(api_calls_info):
                m_key = call_params.get('meeting_key')
                s_key = call_params.get('session_key')
                self.stdout.write(f"Processando chamada {i+1}/{len(api_calls_info)}: Mtg {m_key or 'N/A'}, Sess {s_key or 'N/A'}...")
                api_data = self.fetch_position_data(meeting_key=m_key, session_key=s_key, api_token=api_token)

                if isinstance(api_data, dict) and "error_status" in api_data:
                    api_call_errors += 1
                    self.add_warning(f"Erro na API para Mtg {m_key or 'N/A'}, Sess {s_key or 'N/A'}: {api_data['error_message']}")
                    continue

                if not api_data:
                    self.stdout.write(self.style.WARNING(f"Nenhum registro retornado para Mtg {m_key or 'N/A'}, Sess {s_key or 'N/A'}."))
                    continue

                positions_found_api_total += len(api_data)

                if actual_mode == 'U':
                    with transaction.atomic():
                        delete_kwargs = {k: v for k, v in call_params.items() if v is not None}
                        Position.objects.filter(**delete_kwargs).delete()
                        self.stdout.write(f"Registros antigos deletados para {delete_kwargs}.")

                if api_data:
                    with transaction.atomic():
                        instances = [self.create_position_instance(entry) for entry in api_data]
                        if actual_mode == 'I':
                            created = Position.objects.bulk_create(instances, batch_size=self.BULK_SIZE, ignore_conflicts=True)
                            positions_inserted_db += len(created)
                            positions_skipped_db += len(instances) - len(created)
                        else: # Modo 'U'
                            Position.objects.bulk_create(instances, batch_size=self.BULK_SIZE, ignore_conflicts=False)
                            positions_inserted_db += len(instances)
                else:
                    self.stdout.write(self.style.WARNING("Nenhum registro válido para inserir."))

                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

        except OperationalError as e:
            raise CommandError(f"Erro de banco: {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado: {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo ---"))
            self.stdout.write(self.style.SUCCESS(f"Chamadas API: {len(api_calls_info)}"))
            self.stdout.write(self.style.SUCCESS(f"Encontrados: {positions_found_api_total}"))
            self.stdout.write(self.style.SUCCESS(f"Inseridos: {positions_inserted_db}"))
            self.stdout.write(self.style.SUCCESS(f"Atualizados: {positions_updated_db}"))
            self.stdout.write(self.style.NOTICE(f"Ignorados (conflitos): {positions_skipped_db}"))
            if positions_skipped_missing_data:
                self.stdout.write(self.style.WARNING(f"Ignorados (dados ausentes): {positions_skipped_missing_data}"))
            if positions_skipped_invalid_date:
                self.stdout.write(self.style.WARNING(f"Ignorados (data inválida): {positions_skipped_invalid_date}"))
            if api_call_errors:
                self.stdout.write(self.style.ERROR(f"Erros de API: {api_call_errors}"))
            self.stdout.write(self.style.WARNING(f"Avisos totais: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos:"))
                for msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {msg}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de positions finalizada!"))