# G:\Learning\F1Data\F1Data_App\core\management\commands\import_drivers.py
import requests
import os
import time
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, IntegrityError
from django.conf import settings
from dotenv import load_dotenv
from update_token import update_api_token_if_needed

from core.models import Drivers

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

class Command(BaseCommand):
    help = 'Importa dados de drivers da API OpenF1 com base em meeting_key.'

    API_URL = "https://api.openf1.org/v1/drivers"
    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5

    def add_arguments(self, parser):
        parser.add_argument('--meeting_key', type=int, required=True, help='Meeting Key a ser importado')
        parser.add_argument('--mode', choices=['I', 'U'], default='I', help='Modo: I=Insert apenas, U=Insert e Update')

    def add_warning(self, message):
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    def fetch_drivers_data(self, meeting_key, use_token=True):
        url = f"{self.API_URL}?meeting_key={meeting_key}"
        headers = {"Accept": "application/json"}

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                raise CommandError("Token da API não encontrado.")
            headers["Authorization"] = f"Bearer {api_token}"

        for attempt in range(self.API_MAX_RETRIES):
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                status_code = getattr(response, 'status_code', 'Unknown')
                if status_code in [500, 502, 503, 504] and attempt < self.API_MAX_RETRIES - 1:
                    delay = self.API_RETRY_DELAY_SECONDS * (2 ** attempt)
                    self.stdout.write(self.style.WARNING(f"Erro {status_code} da API. Retentando em {delay} segundos..."))
                    time.sleep(delay)
                else:
                    raise CommandError(f"Erro ao acessar API: {e}")
        raise CommandError("Falha após múltiplas tentativas na API.")

    def insert_driver_entry(self, driver_data, mode='I'):
        try:
            if not isinstance(driver_data, dict):
                raise ValueError("Dados de driver inválidos.")

            keys = ['meeting_key', 'session_key', 'driver_number']
            if any(driver_data.get(k) is None for k in keys):
                raise ValueError(f"Faltam campos obrigatórios: {keys}")

            lookup = {
                'meeting_key': driver_data['meeting_key'],
                'session_key': driver_data['session_key'],
                'driver_number': driver_data['driver_number'],
            }

            defaults = {
                'broadcast_name': driver_data.get('broadcast_name'),
                'full_name': driver_data.get('full_name'),
                'name_acronym': driver_data.get('name_acronym'),
                'team_name': driver_data.get('team_name'),
                'team_colour': driver_data.get('team_colour'),
                'first_name': driver_data.get('first_name'),
                'last_name': driver_data.get('last_name'),
                'headshot_url': driver_data.get('headshot_url'),
                'country_code': driver_data.get('country_code'),
            }

            if mode == 'U':
                obj, created = Drivers.objects.update_or_create(defaults=defaults, **lookup)
                return 'inserted' if created else 'updated'
            else:
                Drivers.objects.create(**lookup, **defaults)
                return 'inserted'

        except IntegrityError:
            return 'skipped'
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Erro ao processar driver ({lookup}): {e}"))
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
                raise CommandError(f"Erro ao atualizar token: {e}")
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no env.cfg)."))

        meeting_key = options.get('meeting_key')
        mode = options.get('mode', 'I')

        self.stdout.write(self.style.MIGRATE_HEADING(f"Iniciando importação de Drivers para meeting_key={meeting_key} (modo={mode})..."))

        try:
            all_drivers = self.fetch_drivers_data(meeting_key=meeting_key, use_token=use_api_token_flag)
        except Exception as e:
            raise CommandError(f"Erro ao buscar dados da API: {e}")

        if not all_drivers:
            self.stdout.write(self.style.NOTICE("Nenhum driver retornado pela API."))
            return

        drivers_found = len(all_drivers)
        drivers_inserted = 0
        drivers_updated = 0
        drivers_skipped = 0

        for driver_data in all_drivers:
            try:
                with transaction.atomic():
                    result = self.insert_driver_entry(driver_data, mode=mode)
                    if result == 'inserted':
                        drivers_inserted += 1
                    elif result == 'updated':
                        drivers_updated += 1
                    elif result == 'skipped':
                        drivers_skipped += 1
            except Exception:
                continue
            time.sleep(self.API_DELAY_SECONDS)

        self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Drivers ---"))
        self.stdout.write(self.style.SUCCESS(f"Drivers encontrados: {drivers_found}"))
        self.stdout.write(self.style.SUCCESS(f"Drivers inseridos: {drivers_inserted}"))
        self.stdout.write(self.style.SUCCESS(f"Drivers atualizados: {drivers_updated}"))
        self.stdout.write(self.style.NOTICE(f"Drivers ignorados: {drivers_skipped}"))
        self.stdout.write(self.style.WARNING(f"Avisos: {self.warnings_count}"))
        if self.all_warnings_details:
            for msg in self.all_warnings_details:
                self.stdout.write(self.style.WARNING(f" - {msg}"))
