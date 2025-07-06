# G:\Learning\F1Data\F1Data_App\core\management\commands\import_cardata.py
import requests
import json
from datetime import datetime
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, IntegrityError, transaction
from django.conf import settings # Importado para settings.BASE_DIR

from core.models import Sessions, Drivers, CarData
from dotenv import load_dotenv
from update_token import update_api_token_if_needed

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

class Command(BaseCommand):
    help = 'Importa dados de telemetria (car_data) da API OpenF1 para o PostgreSQL de forma otimizada.'

    API_URL = "https://api.openf1.org/v1/car_data"
#    CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json') # Removido
    API_DELAY_SECONDS = 0.2

    PRIMARY_N_GEAR_FILTER = "n_gear>0"
    FALLBACK_N_GEAR_FILTERS = ["n_gear>0&n_gear<=4", "n_gear>4"]
    BULK_SIZE = 5000

    warnings_count = 0 # Adicionado para a classe
    all_warnings_details = [] # Adicionado para a classe

    def add_warning(self, message): # Adicionado o método para a classe
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    def get_triplets_to_process(self):
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando triplets a processar para car_data..."))

        all_session_pairs = set(Sessions.objects.all().values_list('meeting_key', 'session_key'))
        relevant_drivers_query = Drivers.objects.filter(
            meeting_key__in=[pair[0] for pair in all_session_pairs],
            session_key__in=[pair[1] for pair in all_session_pairs]
        ).values('meeting_key', 'session_key', 'driver_number')

        all_triplets_from_db = set((d['meeting_key'], d['session_key'], d['driver_number']) for d in relevant_drivers_query)
        triplets_list = list(all_triplets_from_db)

        existing_cardata_triplets = set(
            CarData.objects.filter(
                meeting_key__in=[t[0] for t in triplets_list],
                session_key__in=[t[1] for t in triplets_list],
                driver_number__in=[t[2] for t in triplets_list]
            ).values_list('meeting_key', 'session_key', 'driver_number')
        )

        return sorted(list(all_triplets_from_db - existing_cardata_triplets))

    def fetch_cardata(self, meeting_key, session_key, driver_number, n_gear_filter=None, use_token=True):
        if not (meeting_key and session_key and driver_number):
            raise CommandError("Chave inválida para car_data.")

        url_params = f"meeting_key={meeting_key}&session_key={session_key}&driver_number={driver_number}"
        url = f"{self.API_URL}?{url_params}&{n_gear_filter}" if n_gear_filter else f"{self.API_URL}?{url_params}"

        headers = {"Accept": "application/json"}
        if use_token:
            token = os.getenv("OPENF1_API_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            else:
                self.add_warning("Token da API não encontrado. Requisição será feita sem Authorization.") # Usando add_warning
        else:
            self.add_warning("Token desativado.") # Usando add_warning

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

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH) # Passando o caminho explícito

        self.warnings_count = 0 # Zera o contador de avisos
        self.all_warnings_details = [] # Zera a lista de detalhes de avisos

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true' # <--- DEFINIÇÃO DA VARIÁVEL

        if use_api_token_flag:
            try:
                update_api_token_if_needed()
            except Exception as e:
                raise CommandError(f"Falha ao verificar/atualizar token: {e}")
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no env.cfg).")) # Mensagem única

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando importação de car_data..."))
        inserted, skipped, processed = 0, 0, 0

        try:
            triplets = self.get_triplets_to_process()
            if not triplets:
                self.stdout.write(self.style.NOTICE("Nenhum triplet a processar."))
                return

            self.stdout.write(self.style.SUCCESS(f"{len(triplets)} triplets a processar."))

            for i, (meeting_key, session_key, driver_number) in enumerate(triplets):
                processed += 1
                self.stdout.write(f"[{i+1}/{len(triplets)}] Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}")

                car_data = []
                api_success = False

                primary = self.fetch_cardata(meeting_key, session_key, driver_number, self.PRIMARY_N_GEAR_FILTER, use_token=use_api_token_flag) # Passando use_token
                if isinstance(primary, list):
                    car_data.extend(primary)
                    api_success = True
                elif isinstance(primary, dict) and primary.get("error_status") == 422:
                    self.add_warning(f"422 Too Much Data. Tentando fallback para Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}") # Usando add_warning
                    for fallback in self.FALLBACK_N_GEAR_FILTERS:
                        fallback_resp = self.fetch_cardata(meeting_key, session_key, driver_number, fallback, use_token=use_api_token_flag) # Passando use_token
                        if isinstance(fallback_resp, list):
                            car_data.extend(fallback_resp)
                            api_success = True
                            if self.API_DELAY_SECONDS > 0:
                                time.sleep(self.API_DELAY_SECONDS)
                        else:
                            self.add_warning(f"Fallback '{fallback}' falhou: {fallback_resp.get('error_message')}") # Usando add_warning
                elif isinstance(primary, dict):
                    self.add_warning(f"Erro API: {primary.get('error_status')} - {primary.get('error_message')}") # Usando add_warning
                    continue

                if api_success and car_data:
                    objects = []
                    for entry in car_data:
                        try:
                            obj = self.build_cardata_instance(entry)
                            objects.append(obj)
                        except Exception as e:
                            self.add_warning(f"Erro ao construir entrada: {e}") # Usando add_warning

                    try:
                        with transaction.atomic():
                            CarData.objects.bulk_create(objects, batch_size=self.BULK_SIZE, ignore_conflicts=True)
                            inserted += len(objects)
                    except Exception as e:
                        self.add_warning(f"Erro no bulk_create: {e}") # Usando add_warning

                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

        except OperationalError as e:
            raise CommandError(f"Erro operacional no banco: {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado: {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação ---"))
            self.stdout.write(self.style.SUCCESS(f"Triplets processados: {processed}"))
            self.stdout.write(self.style.SUCCESS(f"Registros inseridos (aprox.): {inserted}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam): {skipped}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
            self.stdout.write(self.style.SUCCESS("Importação finalizada."))