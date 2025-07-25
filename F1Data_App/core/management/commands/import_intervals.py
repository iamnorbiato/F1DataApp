# G:\Learning\F1Data\F1Data_App\core\management\commands\import_intervals.py

import requests
import json
import os
import time
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, IntegrityError, OperationalError
from django.conf import settings

from core.models import Drivers, Intervals, Sessions # Adicionado Sessions
from dotenv import load_dotenv
from update_token import update_api_token_if_needed

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')


class Command(BaseCommand):
    help = 'Importa dados de intervalos da API OpenF1 filtrando por meeting_key e modo de operação (insert-only ou insert+update).'

    API_URL = "https://api.openf1.org/v1/intervals"
    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5
    BULK_SIZE = 5000

    def add_arguments(self, parser):
        parser.add_argument('--meeting_key', type=int, help='Meeting key para buscar dados de intervalos. (Opcional, se omitido, processa todos os meetings)')
        parser.add_argument('--mode', type=str, choices=['I', 'U'], default='I', help='Modo de operação: I=insert only, U=insert/update.')

    def add_warning(self, message):
        if not hasattr(self, 'warnings_count'):
            self.warnings_count = 0
            self.all_warnings_details = []
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    def get_meeting_session_driver_triplets_to_fetch(self, meeting_key_filter=None, mode='I'):
        """
        Obtém todas as triplas (meeting_key, session_key, driver_number) da tabela 'Drivers'
        que possuem dados de 'intervals' na API, filtrados por meeting_key.
        Agora, considera *todos* os tipos de sessão.
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Obtendo triplas (meeting_key, session_key, driver_number) para buscar intervalos..."))
        
        # Obter todas as sessões (removido o filtro session_type='Race')
        sessions_query = Sessions.objects.all()
        if meeting_key_filter:
            sessions_query = sessions_query.filter(meeting_key=meeting_key_filter)
        
        all_sessions_pairs = set(sessions_query.values_list('meeting_key', 'session_key'))
        self.stdout.write(f"Encontrados {len(all_sessions_pairs)} pares (meeting_key, session_key) para todas as sessões{' para o meeting_key especificado' if meeting_key_filter else ''}.")

        # Obter todas as triplas de drivers para as sessões relevantes
        all_driver_triplets = set(
            Drivers.objects.filter(
                meeting_key__in=[pair[0] for pair in all_sessions_pairs],
                session_key__in=[pair[1] for pair in all_sessions_pairs]
            ).values_list('meeting_key', 'session_key', 'driver_number')
        )
        self.stdout.write(f"Encontradas {len(all_driver_triplets)} triplas de drivers relevantes para todas as sessões.")

        triplets_to_process = set()
        if mode == 'I':
            # Para 'I', precisamos subtrair o que já existe em Intervals (considerando M,S,D,Lap)
            # Como a API de Intervals retorna todos os intervalos para uma (M,S,D),
            # se já existe *qualquer* intervalo para uma (M,S,D), assumimos que já foi importado.
            existing_intervals_msd_triplets = set(
                Intervals.objects.filter(
                    meeting_key__in=[m for m,s,d in all_driver_triplets],
                    session_key__in=[s for m,s,d in all_driver_triplets],
                    driver_number__in=[d for m,s,d in all_driver_triplets]
                ).values_list('meeting_key', 'session_key', 'driver_number').distinct()
            )
            triplets_to_process = all_driver_triplets - existing_intervals_msd_triplets
            self.stdout.write(f"Modo 'I': {len(triplets_to_process)} triplas (M,S,D) serão consideradas para busca de novos intervalos (ainda não existentes).")
        else: # mode == 'U'
            triplets_to_process = all_driver_triplets
            self.stdout.write(f"Modo 'U': Todas as {len(triplets_to_process)} triplas (M,S,D) relevantes serão consideradas para atualização/inserção.")

        # Ordena para processamento consistente
        return sorted(list(triplets_to_process))

    def fetch_intervals_data(self, session_key, driver_number, use_token=True):
        url = f"{self.API_URL}?session_key={session_key}&driver_number={driver_number}"
        headers = {"Accept": "application/json"}

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                self.add_warning(f"Token da API (OPENF1_API_TOKEN) não encontrado. Requisição para Sess {session_key}, Driver {driver_number} será feita sem Authorization.")
            else:
                headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.add_warning(f"Uso do token desativado. Requisição para Sess {session_key}, Driver {driver_number} será feita sem Authorization.")

        for attempt in range(self.API_MAX_RETRIES):
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                status_code = getattr(response, 'status_code', 'Unknown')
                error_msg = f"Erro {status_code} da API para URL: {url} - {e}"
                if status_code in [500, 502, 503, 504, 401, 403] and attempt < self.API_MAX_RETRIES - 1:
                    delay = self.API_RETRY_DELAY_SECONDS * (2 ** attempt)
                    self.add_warning(f"{error_msg}. Tentativa {attempt + 1}/{self.API_MAX_RETRIES}. Retentando em {delay} segundos...")
                    time.sleep(delay)
                else:
                    self.add_warning(f"Falha na busca da API após retries para {url}: {error_msg}")
                    return {"error_status": status_code,
                            "error_url": url,
                            "error_message": str(e)}
        self.add_warning(f"Falha na busca da API para {url}: Máximo de retries excedido.")
        return {"error_status": "Failed after retries", "error_url": url, "error_message": "Max retries exceeded."}

    def process_interval_entry(self, interval_data_dict, mode):
        def to_datetime(val):
            return datetime.fromisoformat(val.replace("Z", "+00:00")) if val else None

        meeting_key = interval_data_dict.get("meeting_key")
        session_key = interval_data_dict.get("session_key")
        driver_number = interval_data_dict.get("driver_number")
        interval = interval_data_dict.get("interval")
        
        # O campo 'date' é crucial para a PK e deve ser validado
        date_str = interval_data_dict.get("date")

        if any(val is None for val in [meeting_key, session_key, driver_number, interval, date_str]):
            missing_fields = [k for k,v in {
                'meeting_key': meeting_key, 'session_key': session_key,
                'driver_number': driver_number, 'interval': interval, 'date': date_str
            }.items() if v is None]
            self.add_warning(f"Intervalo ignorado: dados obrigatórios ausentes para Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}. Faltando: {missing_fields}")
            return 'skipped_missing_data'

        date_obj = to_datetime(date_str)
        if date_obj is None:
            self.add_warning(f"Formato de data inválido para intervalo (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}): '{date_str}'.")
            return 'skipped_invalid_date'

        defaults = {
            "gap_to_leader": interval_data_dict.get("gap_to_leader"),
            "gap_to_preceding": interval_data_dict.get("gap_to_preceding"),
            "interval_type": interval_data_dict.get("interval_type"),
            "lap_number": interval_data_dict.get("lap_number"),
            "race_time": interval_data_dict.get("race_time"),
            "total_laps": interval_data_dict.get("total_laps"),
        }

        try:
            if mode == 'U':
                obj, created = Intervals.objects.update_or_create(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    driver_number=driver_number,
                    interval=interval,
                    date=date_obj, # 'date' é parte da PK
                    defaults=defaults
                )
                return 'inserted' if created else 'updated'
            else: # mode == 'I'
                if Intervals.objects.filter(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    driver_number=driver_number,
                    interval=interval,
                    date=date_obj
                ).exists():
                    return 'skipped'
                
                Intervals.objects.create(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    driver_number=driver_number,
                    interval=interval,
                    date=date_obj,
                    **defaults
                )
                return 'inserted'
        except IntegrityError:
            self.add_warning(f"IntegrityError ao processar intervalo (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}, Interval {interval}). Provavelmente duplicata. Ignorando.")
            return 'skipped'
        except Exception as e:
            self.add_warning(f"Erro FATAL ao processar intervalo (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}, Interval {interval}): {e}. Dados API: {interval_data_dict}")
            raise


    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        self.warnings_count = 0
        self.all_warnings_details = []

        meeting_key_param = options.get('meeting_key')
        mode_param = options.get('mode', 'I')

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação/atualização de Intervals (ORM)..."))
        self.stdout.write(f"Parâmetros recebidos: meeting_key={meeting_key_param if meeting_key_param else 'Nenhum'}, mode={mode_param}")

        intervals_found_api_total = 0
        intervals_inserted_db = 0
        intervals_updated_db = 0
        intervals_skipped_db = 0
        intervals_skipped_missing_data = 0
        intervals_skipped_invalid_date = 0
        api_call_errors = 0


        try:
            if use_api_token_flag:
                try:
                    self.stdout.write("Verificando e atualizando o token da API, se necessário...")
                    update_api_token_if_needed()
                    load_dotenv(dotenv_path=ENV_FILE_PATH, override=True)
                    current_api_token = os.getenv('OPENF1_API_TOKEN')
                    if not current_api_token:
                        raise CommandError("Token da API (OPENF1_API_TOKEN) não disponível após verificação/atualização. Não é possível prosseguir com importação autenticada.")
                    self.stdout.write(self.style.SUCCESS("Token da API verificado/atualizado com sucesso."))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Falha ao verificar/atualizar o token da API: {e}. Prosseguindo sem usar o token da API."))
                    use_api_token_flag = False

            if not use_api_token_flag:
                self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False ou falha na obtenção do token). Buscando dados sem autenticação."))

            triplets_to_fetch = self.get_meeting_session_driver_triplets_to_fetch(
                meeting_key_filter=meeting_key_param,
                mode=mode_param
            )

            if not triplets_to_fetch:
                self.stdout.write(self.style.NOTICE("Nenhuma tripla (M,S,D) encontrada para buscar dados de intervalos. Encerrando."))
                return

            self.stdout.write(self.style.SUCCESS(f"Total de {len(triplets_to_fetch)} triplas (M,S,D) elegíveis para busca na API."))

            for i, (m_key, s_key, d_num) in enumerate(triplets_to_fetch):
                self.stdout.write(f"Buscando e processando intervalos para tripla {i+1}/{len(triplets_to_fetch)}: Mtg {m_key}, Sess {s_key}, Driver {d_num}...")

                intervals_data_from_api = self.fetch_intervals_data(
                    session_key=s_key,
                    driver_number=d_num,
                    use_token=use_api_token_flag
                )

                if isinstance(intervals_data_from_api, dict) and "error_status" in intervals_data_from_api:
                    api_call_errors += 1
                    self.add_warning(f"Erro na API para Mtg {m_key}, Sess {s_key}, Driver {d_num}: {intervals_data_from_api['error_message']}")
                    continue

                if not intervals_data_from_api:
                    self.stdout.write(self.style.WARNING(f"Nenhum registro de intervalos encontrado na API para Mtg {m_key}, Sess {s_key}, Driver {d_num}."))
                    continue

                intervals_found_api_total += len(intervals_data_from_api)
                self.stdout.write(f"Encontrados {len(intervals_data_from_api)} registros de intervalos para Mtg {m_key}, Sess {s_key}, Driver {d_num}. Processando...")

                # Em modo 'U', a melhor estratégia para 'intervals' é deletar todos os intervalos existentes para essa tripla (M,S,D)
                # e depois inserir os novos, garantindo consistência com a API.
                if mode_param == 'U':
                    with transaction.atomic():
                        Intervals.objects.filter(
                            meeting_key=m_key,
                            session_key=s_key,
                            driver_number=d_num
                        ).delete()
                        self.stdout.write(f"Registros de intervalos existentes deletados para Mtg {m_key}, Sess {s_key}, Driver {d_num}.")
                
                for interval_entry_dict in intervals_data_from_api:
                    try:
                        result = self.process_interval_entry(interval_entry_dict, mode=mode_param)
                        if result == 'inserted':
                            intervals_inserted_db += 1
                        elif result == 'updated':
                            intervals_updated_db += 1
                        elif result == 'skipped':
                            intervals_skipped_db += 1
                        elif result == 'skipped_missing_data':
                            intervals_skipped_missing_data += 1
                        elif result == 'skipped_invalid_date':
                            intervals_skipped_invalid_date += 1
                    except Exception as interval_process_e:
                        self.add_warning(f"Erro ao processar UM REGISTRO de intervalo (Mtg {m_key}, Sess {s_key}, Driver {d_num}): {interval_process_e}. Pulando para o próximo.")


                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Processamento de Intervals concluído!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação/atualização (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação/atualização de Intervals (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo do Processamento de Intervals (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Triplas (M,S,D) processadas: {len(triplets_to_fetch) if 'triplets_to_fetch' in locals() else 0}"))
            self.stdout.write(self.style.SUCCESS(f"Registros de Intervals encontrados na API (total): {intervals_found_api_total}"))
            self.stdout.write(self.style.SUCCESS(f"Registros novos inseridos no DB: {intervals_inserted_db}"))
            self.stdout.write(self.style.SUCCESS(f"Registros existentes atualizados no DB: {intervals_updated_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB em modo 'I'): {intervals_skipped_db}"))
            if intervals_skipped_missing_data > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (dados obrigatórios ausentes): {intervals_skipped_missing_data}"))
            if intervals_skipped_invalid_date > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (formato de data inválido): {intervals_skipped_invalid_date}"))
            if api_call_errors > 0:
                self.stdout.write(self.style.ERROR(f"Erros em chamadas à API: {api_call_errors}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Processamento de intervals finalizado!"))