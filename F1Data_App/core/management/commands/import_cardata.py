# G:\Learning\F1Data\F1Data_App\core\management\commands\import_cardata.py
import requests
import json
from datetime import datetime
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, transaction
from django.conf import settings

from core.models import Sessions, Drivers, CarData
from dotenv import load_dotenv
from update_token import update_api_token_if_needed

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

class Command(BaseCommand):
    help = 'Importa dados de telemetria (car_data) da API OpenF1 para o PostgreSQL de forma otimizada.'

    API_URL = "https://api.openf1.org/v1/car_data"
    API_DELAY_SECONDS = 0.2
    PRIMARY_N_GEAR_FILTER = "n_gear>0"
    FALLBACK_N_GEAR_FILTERS = ["n_gear>0&n_gear<=4", "n_gear>4"]
    BULK_SIZE = 5000

    def add_arguments(self, parser):
        parser.add_argument('--meeting_key', type=int, help='Filtrar importacao para um meeting_key especifico')
        parser.add_argument('--mode', type=str, choices=['I', 'U'], default='I', help="Modo: 'I' para inserir apenas novos, 'U' para forcar update")

    def add_warning(self, message):
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    def fetch_cardata(self, meeting_key, session_key, driver_number, n_gear_filter=None, use_token=True):
        url_params = f"meeting_key={meeting_key}&session_key={session_key}&driver_number={driver_number}"
        url = f"{self.API_URL}?{url_params}&{n_gear_filter}" if n_gear_filter else f"{self.API_URL}?{url_params}"

        headers = {"Accept": "application/json"}
        if use_token:
            token = os.getenv("OPENF1_API_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            else:
                self.add_warning("Token da API nao encontrado. Requisicao sem Authorization.")
        else:
            self.add_warning("Token desativado.")

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            return {"error_status": response.status_code, "error_url": url, "error_message": str(e)}
        except requests.exceptions.RequestException as e:
            return {"error_status": "RequestError", "error_url": url, "error_message": str(e)}

    def build_cardata_instance(self, entry):
        date_str = entry.get('date')
        date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00')) if date_str else None
        return CarData(
            date=date_obj,
            session_key=entry['session_key'],
            meeting_key=entry['meeting_key'],
            driver_number=entry['driver_number'],
            speed=entry.get('speed'),
            n_gear=entry.get('n_gear'),
            drs=entry.get('drs'),
            throttle=entry.get('throttle'),
            brake=entry.get('brake'),
            rpm=entry.get('rpm')
        )

    def handle_triplet(self, meeting_key, session_key, driver_number, use_api_token_flag, mode):
        car_data = []
        primary = self.fetch_cardata(meeting_key, session_key, driver_number, self.PRIMARY_N_GEAR_FILTER, use_token=use_api_token_flag)
        api_success = False

        if isinstance(primary, list):
            car_data.extend(primary)
            api_success = True
        elif isinstance(primary, dict) and primary.get("error_status") == 422:
            for fallback in self.FALLBACK_N_GEAR_FILTERS:
                fallback_resp = self.fetch_cardata(meeting_key, session_key, driver_number, fallback, use_token=use_api_token_flag)
                if isinstance(fallback_resp, list):
                    car_data.extend(fallback_resp)
                    api_success = True
                    if self.API_DELAY_SECONDS > 0:
                        time.sleep(self.API_DELAY_SECONDS)
        elif isinstance(primary, dict):
            self.add_warning(f"Erro API Mtg={meeting_key} Sess={session_key} Driver={driver_number}: {primary.get('error_message')}")

        inserted = 0
        if api_success and car_data:
            objects = []
            for entry in car_data:
                try:
                    obj = self.build_cardata_instance(entry)
                    objects.append(obj)
                except Exception as e:
                    self.add_warning(f"Erro ao construir entrada Mtg={meeting_key} Sess={session_key} Driver={driver_number}: {e}")

            try:
                with transaction.atomic():
                    if mode == 'I':
                        CarData.objects.bulk_create(objects, batch_size=self.BULK_SIZE, ignore_conflicts=True)
                    elif mode == 'U':
                        CarData.objects.bulk_create(objects, batch_size=self.BULK_SIZE, ignore_conflicts=False)
                    inserted += len(objects)
            except Exception as e:
                self.add_warning(f"Erro bulk_create Mtg={meeting_key} Sess={session_key} Driver={driver_number}: {e}")
        return inserted

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)
        self.warnings_count = 0
        self.all_warnings_details = []

        meeting_key_filter = options.get('meeting_key')
        mode = options.get('mode', 'I')
        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'
        max_workers = int(os.getenv('MAX_WORKERS', '4'))

        if use_api_token_flag:
            try:
                update_api_token_if_needed()
            except Exception as e:
                raise CommandError(f"Falha ao verificar/atualizar token: {e}")
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False)."))

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando importacao de car_data..."))

        drivers_qs = Drivers.objects.all()
        if meeting_key_filter:
            drivers_qs = drivers_qs.filter(meeting_key=meeting_key_filter)
        triplets = list(drivers_qs.values_list('meeting_key', 'session_key', 'driver_number').distinct())

        if not triplets:
            self.stdout.write(self.style.NOTICE("Nenhum triplet encontrado para processamento."))
            return

        self.stdout.write(self.style.SUCCESS(f"{len(triplets)} triplets a processar em paralelo com max_workers={max_workers}..."))

        total_inserted = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_triplet = {
                executor.submit(self.handle_triplet, mtg, sess, drv, use_api_token_flag, mode): (mtg, sess, drv)
                for mtg, sess, drv in triplets
            }
            for future in as_completed(future_to_triplet):
                inserted = future.result()
                total_inserted += inserted

        self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importacao de car_data ---"))
        self.stdout.write(self.style.SUCCESS(f"Triplets processados: {len(triplets)}"))
        self.stdout.write(self.style.SUCCESS(f"Registros inseridos: {total_inserted}"))
        self.stdout.write(self.style.WARNING(f"Total de avisos: {self.warnings_count}"))
        if self.all_warnings_details:
            for msg in self.all_warnings_details:
                self.stdout.write(self.style.WARNING(f" - {msg}"))
        self.stdout.write(self.style.SUCCESS("Importacao finalizada."))