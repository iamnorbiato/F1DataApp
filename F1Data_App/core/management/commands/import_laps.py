# G:\Learning\F1Data\F1Data_App\core\management\commands\import_laps.py

import requests
import json
import os
import time
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, IntegrityError
from django.conf import settings

from core.models import Drivers, Laps
from dotenv import load_dotenv
from update_token import update_api_token_if_needed

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')


class Command(BaseCommand):
    help = 'Importa dados de laps da API OpenF1 filtrando por meeting_key e modo de operação (insert-only ou insert+update).'

    API_URL = "https://api.openf1.org/v1/laps"
    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5
    BULK_SIZE = 5000

    def add_arguments(self, parser):
        parser.add_argument('--meeting_key', type=int, required=True, help='Meeting key para buscar dados de laps.')
        parser.add_argument('--mode', type=str, choices=['I', 'U'], default='I', help='Modo de operação: I=insert only, U=insert/update.')

    def fetch_laps_data(self, session_key, driver_number, use_token=True):
        url = f"{self.API_URL}?session_key={session_key}&driver_number={driver_number}"
        headers = {"Accept": "application/json"}

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                raise CommandError("Token da API (OPENF1_API_TOKEN) não encontrado.")
            headers["Authorization"] = f"Bearer {api_token}"

        for attempt in range(self.API_MAX_RETRIES):
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                if attempt < self.API_MAX_RETRIES - 1:
                    delay = self.API_RETRY_DELAY_SECONDS * (2 ** attempt)
                    self.stdout.write(self.style.WARNING(f"Tentativa {attempt+1} falhou, aguardando {delay}s: {e}"))
                    time.sleep(delay)
                else:
                    raise CommandError(f"Falha ao buscar dados da API após {self.API_MAX_RETRIES} tentativas: {e}")

    def build_lap_instance(self, lap_data_dict):
        def to_datetime(val):
            return datetime.fromisoformat(val.replace("Z", "+00:00")) if val else None

        return Laps(
            meeting_key=lap_data_dict.get("meeting_key"),
            session_key=lap_data_dict.get("session_key"),
            driver_number=lap_data_dict.get("driver_number"),
            lap_number=lap_data_dict.get("lap_number"),
            date_start=to_datetime(lap_data_dict.get("date_start")),
            duration_sector_1=lap_data_dict.get("duration_sector_1"),
            duration_sector_2=lap_data_dict.get("duration_sector_2"),
            duration_sector_3=lap_data_dict.get("duration_sector_3"),
            i1_speed=lap_data_dict.get("i1_speed"),
            i2_speed=lap_data_dict.get("i2_speed"),
            is_pit_out_lap=lap_data_dict.get("is_pit_out_lap"),
            lap_duration=lap_data_dict.get("lap_duration"),
            segments_sector_1=lap_data_dict.get("segments_sector_1") if isinstance(lap_data_dict.get("segments_sector_1"), list) else None,
            segments_sector_2=lap_data_dict.get("segments_sector_2") if isinstance(lap_data_dict.get("segments_sector_2"), list) else None,
            segments_sector_3=lap_data_dict.get("segments_sector_3") if isinstance(lap_data_dict.get("segments_sector_3"), list) else None,
            st_speed=lap_data_dict.get("st_speed"),
        )

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)
        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        meeting_key = options['meeting_key']
        mode = options['mode']

        self.stdout.write(self.style.MIGRATE_HEADING(f"Iniciando importação de laps para meeting_key={meeting_key} com modo={mode}..."))

        try:
            if use_api_token_flag:
                update_api_token_if_needed()

            triplets = list(
                Drivers.objects.filter(meeting_key=meeting_key)
                .values_list('session_key', 'driver_number')
                .distinct()
            )

            if not triplets:
                self.stdout.write(self.style.WARNING(f"Nenhuma tripla encontrada na tabela 'drivers' para meeting_key={meeting_key}. Encerrando."))
                return

            laps_inserted = 0
            laps_updated = 0

            for i, (session_key, driver_number) in enumerate(triplets):
                self.stdout.write(f"[{i+1}/{len(triplets)}] Buscando laps para session_key={session_key}, driver_number={driver_number}...")

                try:
                    laps_data = self.fetch_laps_data(session_key, driver_number, use_token=use_api_token_flag)
                except CommandError as e:
                    self.stdout.write(self.style.ERROR(str(e)))
                    continue

                if not laps_data:
                    self.stdout.write(self.style.NOTICE("Nenhum dado retornado. Pulando."))
                    continue

                instances = []
                for lap_dict in laps_data:
                    try:
                        instance = self.build_lap_instance(lap_dict)
                        instances.append(instance)
                    except Exception as build_e:
                        self.stdout.write(self.style.WARNING(f"Erro ao construir instância: {build_e}"))

                if not instances:
                    continue

                with transaction.atomic():
                    if mode == 'I':
                        created = Laps.objects.bulk_create(instances, batch_size=self.BULK_SIZE, ignore_conflicts=True)
                        laps_inserted += len(created)
                    elif mode == 'U':
                        for obj in instances:
                            Laps.objects.update_or_create(
                                meeting_key=obj.meeting_key,
                                session_key=obj.session_key,
                                driver_number=obj.driver_number,
                                lap_number=obj.lap_number,
                                defaults={  # todos os campos exceto os de PK
                                    "date_start": obj.date_start,
                                    "duration_sector_1": obj.duration_sector_1,
                                    "duration_sector_2": obj.duration_sector_2,
                                    "duration_sector_3": obj.duration_sector_3,
                                    "i1_speed": obj.i1_speed,
                                    "i2_speed": obj.i2_speed,
                                    "is_pit_out_lap": obj.is_pit_out_lap,
                                    "lap_duration": obj.lap_duration,
                                    "segments_sector_1": obj.segments_sector_1,
                                    "segments_sector_2": obj.segments_sector_2,
                                    "segments_sector_3": obj.segments_sector_3,
                                    "st_speed": obj.st_speed,
                                }
                            )
                        laps_updated += len(instances)

                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS(f"Importação concluída: {laps_inserted} inseridos, {laps_updated} atualizados."))
        except Exception as e:
            raise CommandError(f"Erro inesperado: {e}")
