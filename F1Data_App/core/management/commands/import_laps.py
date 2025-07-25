# G:\Learning\F1Data\F1Data_App\core\management\commands\import_laps.py

import requests
import json
import os
import time
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, IntegrityError
from django.conf import settings

from core.models import Drivers, Laps, Sessions # Adicionado Sessions para obter pares M,S,D se necessário
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
        parser.add_argument('--meeting_key', type=int, help='Meeting key para buscar dados de laps. (Opcional, se omitido, processa todos os meetings)')
        parser.add_argument('--mode', type=str, choices=['I', 'U'], default='I', help='Modo de operação: I=insert only, U=insert/update.')

    def add_warning(self, message):
        # Inicializa se não estiverem inicializados (para segurança, embora handle os inicialize)
        if not hasattr(self, 'warnings_count'):
            self.warnings_count = 0
            self.all_warnings_details = []
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    def get_meeting_session_driver_triplets_to_fetch(self, meeting_key_filter=None):
        """
        Obtém todos os pares (session_key, driver_number) da tabela 'Drivers'
        que possuem dados de 'laps' na API, filtrados por meeting_key.
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Obtendo triplas (meeting_key, session_key, driver_number) para buscar laps..."))
        
        query = Drivers.objects.all()
        if meeting_key_filter:
            query = query.filter(meeting_key=meeting_key_filter)
        
        # Obtenha triplas distintas (meeting_key, session_key, driver_number)
        triplets = sorted(list(
            query.values_list('meeting_key', 'session_key', 'driver_number').distinct()
        ))

        self.stdout.write(f"Encontradas {len(triplets)} triplas (M,S,D) elegíveis para busca na API.")
        return triplets

    def fetch_laps_data(self, meeting_key, session_key, driver_number, use_token=True):
        """
        Busca dados de laps da API OpenF1 para um par (session_key, driver_number) específico.
        """
        if not all([meeting_key, session_key, driver_number]):
            self.add_warning(f"Dados incompletos para fetch_laps_data: Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}. Pulando chamada.")
            return {"error_status": "InvalidParams", "error_message": "Missing meeting_key, session_key, or driver_number"}

        url = f"{self.API_URL}?session_key={session_key}&driver_number={driver_number}"
        headers = {"Accept": "application/json"}

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                self.add_warning(f"Token da API (OPENF1_API_TOKEN) não encontrado. Requisição para Mtg {meeting_key}, Sess {session_key}, Driver {driver_number} será feita sem Authorization.")
            else:
                headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.add_warning(f"Uso do token desativado. Requisição para Mtg {meeting_key}, Sess {session_key}, Driver {driver_number} será feita sem Authorization.")

        for attempt in range(self.API_MAX_RETRIES):
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                status_code = getattr(response, 'status_code', 'Unknown')
                error_msg = f"Erro {status_code} da API para URL: {url} - {e}"
                if status_code in [500, 502, 503, 504, 401, 403] and attempt < self.API_MAX_RETRIES - 1: # Incluir 401/403 para retentativa
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

    def process_lap_entry(self, lap_data_dict, mode):
        """
        Processa um único registro de volta, inserindo ou atualizando.
        Retorna 'inserted', 'updated', ou 'skipped_missing_data'/'skipped_invalid_date'.
        """
        def to_datetime(val):
            return datetime.fromisoformat(val.replace("Z", "+00:00")) if val else None

        meeting_key = lap_data_dict.get("meeting_key")
        session_key = lap_data_dict.get("session_key")
        driver_number = lap_data_dict.get("driver_number")
        lap_number = lap_data_dict.get("lap_number")
        date_start_str = lap_data_dict.get("date_start")

        # Validação de campos obrigatórios para PK
        if any(val is None for val in [meeting_key, session_key, driver_number, lap_number, date_start_str]):
            missing_fields = [k for k,v in {
                'meeting_key': meeting_key, 'session_key': session_key,
                'driver_number': driver_number, 'lap_number': lap_number,
                'date_start': date_start_str
            }.items() if v is None]
            return 'skipped_missing_data'

        date_start_obj = to_datetime(date_start_str)
        if date_start_obj is None: # Se to_datetime falhou silenciosamente ou a string era None/vazia
            self.add_warning(f"Formato de data inválido para lap {lap_number} (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}): '{date_start_str}'.")
            return 'skipped_invalid_date'

        defaults = {
            "duration_sector_1": lap_data_dict.get("duration_sector_1"),
            "duration_sector_2": lap_data_dict.get("duration_sector_2"),
            "duration_sector_3": lap_data_dict.get("duration_sector_3"),
            "i1_speed": lap_data_dict.get("i1_speed"),
            "i2_speed": lap_data_dict.get("i2_speed"),
            "is_pit_out_lap": lap_data_dict.get("is_pit_out_lap"),
            "lap_duration": lap_data_dict.get("lap_duration"),
            "segments_sector_1": lap_data_dict.get("segments_sector_1") if isinstance(lap_data_dict.get("segments_sector_1"), list) else None,
            "segments_sector_2": lap_data_dict.get("segments_sector_2") if isinstance(lap_data_dict.get("segments_sector_2"), list) else None,
            "segments_sector_3": lap_data_dict.get("segments_sector_3") if isinstance(lap_data_dict.get("segments_sector_3"), list) else None,
            "st_speed": lap_data_dict.get("st_speed"),
        }

        try:
            if mode == 'U':
                obj, created = Laps.objects.update_or_create(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    driver_number=driver_number,
                    lap_number=lap_number,
                    date_start=date_start_obj, # Parte da PK, mas também pode ser default se a API permitir
                    defaults=defaults
                )
                return 'inserted' if created else 'updated'
            else: # mode == 'I'
                # Para Insert-only, verifica se já existe antes de criar para evitar IntegrityError
                if Laps.objects.filter(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    driver_number=driver_number,
                    lap_number=lap_number,
                    date_start=date_start_obj # Inclui date_start na PK check
                ).exists():
                    return 'skipped'
                
                Laps.objects.create(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    driver_number=driver_number,
                    lap_number=lap_number,
                    date_start=date_start_obj,
                    **defaults
                )
                return 'inserted'
        except IntegrityError:
            return 'skipped' # Em caso de rara condição de corrida
        except Exception as e:
            self.add_warning(f"Erro FATAL ao processar lap (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}, Lap {lap_number}): {e}. Dados API: {lap_data_dict}")
            raise # Re-levanta para tratamento superior


    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        self.warnings_count = 0
        self.all_warnings_details = []

        meeting_key_param = options.get('meeting_key')
        mode_param = options.get('mode', 'I')

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação/atualização de Laps (ORM)..."))
        self.stdout.write(f"Parâmetros recebidos: meeting_key={meeting_key_param if meeting_key_param else 'Nenhum'}, mode={mode_param}")

        laps_found_api_total = 0
        laps_inserted_db = 0
        laps_updated_db = 0
        laps_skipped_db = 0
        laps_skipped_missing_data = 0
        laps_skipped_invalid_date = 0
        api_call_errors = 0


        try:
            if use_api_token_flag:
                try:
                    self.stdout.write("Verificando e atualizando o token da API, se necessário...")
                    update_api_token_if_needed()
                    # >>> CORREÇÃO CRÍTICA AQUI: Recarrega as variáveis de ambiente após possível atualização <<<
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

            # Determinar quais triplas (M,S,D) buscar
            triplets_to_fetch = []
            if meeting_key_param:
                # Se meeting_key é fornecido, busca triplas apenas para ele
                self.stdout.write(f"Buscando triplas para meeting_key específico: {meeting_key_param}...")
                triplets_to_fetch = self.get_meeting_session_driver_triplets_to_fetch(meeting_key_filter=meeting_key_param)
            else:
                # Se meeting_key NÃO é fornecido, busca todas as triplas de todos os meetings
                self.stdout.write("Nenhum meeting_key especificado. Buscando todas as triplas de todos os meetings para 'Laps'...")
                triplets_to_fetch = self.get_meeting_session_driver_triplets_to_fetch(meeting_key_filter=None)

            if not triplets_to_fetch:
                self.stdout.write(self.style.NOTICE("Nenhuma tripla (M,S,D) encontrada para buscar dados de voltas. Encerrando."))
                return

            self.stdout.write(self.style.SUCCESS(f"Total de {len(triplets_to_fetch)} triplas (M,S,D) elegíveis para busca na API."))

            for i, (m_key, s_key, d_num) in enumerate(triplets_to_fetch):
                self.stdout.write(f"Buscando e processando laps para tripla {i+1}/{len(triplets_to_fetch)}: Mtg {m_key}, Sess {s_key}, Driver {d_num}...")

                laps_data_from_api = self.fetch_laps_data(
                    meeting_key=m_key,
                    session_key=s_key,
                    driver_number=d_num,
                    use_token=use_api_token_flag
                )

                if isinstance(laps_data_from_api, dict) and "error_status" in laps_data_from_api:
                    api_call_errors += 1
                    self.add_warning(f"Erro na API para Mtg {m_key}, Sess {s_key}, Driver {d_num}: {laps_data_from_api['error_message']}")
                    continue

                if not laps_data_from_api:
                    self.stdout.write(self.style.WARNING(f"Nenhum registro de laps encontrado na API para Mtg {m_key}, Sess {s_key}, Driver {d_num}."))
                    continue

                laps_found_api_total += len(laps_data_from_api)
                self.stdout.write(f"Encontrados {len(laps_data_from_api)} registros de laps para Mtg {m_key}, Sess {s_key}, Driver {d_num}. Processando...")

                # Preparar para bulk operations
                instances_to_create = []
                instances_to_update = [] # Será usado se a estratégia for update_or_create individualmente.

                # Em modo 'U', a melhor estratégia para 'laps' é deletar todos os laps existentes para essa tripla (M,S,D)
                # e depois inserir os novos, garantindo consistência com a API.
                if mode_param == 'U':
                    with transaction.atomic():
                        Laps.objects.filter(
                            meeting_key=m_key,
                            session_key=s_key,
                            driver_number=d_num
                        ).delete()
                        self.stdout.write(f"Registros de laps existentes deletados para Mtg {m_key}, Sess {s_key}, Driver {d_num}.")
                
                for lap_entry_dict in laps_data_from_api:
                    try:
                        result = self.process_lap_entry(lap_entry_dict, mode=mode_param)
                        if result == 'inserted':
                            laps_inserted_db += 1
                        elif result == 'updated':
                            laps_updated_db += 1
                        elif result == 'skipped': # Modo I, registro já existe
                            laps_skipped_db += 1
                        elif result == 'skipped_missing_data':
                            laps_skipped_missing_data += 1
                            self.add_warning(f"Lap ignorado: dados obrigatórios ausentes para Mtg {lap_entry_dict.get('meeting_key', 'N/A')}, Sess {lap_entry_dict.get('session_key', 'N/A')}, Driver {lap_entry_dict.get('driver_number', 'N/A')}, Lap {lap_entry_dict.get('lap_number', 'N/A')}.")
                        elif result == 'skipped_invalid_date':
                            laps_skipped_invalid_date += 1
                    except Exception as lap_process_e:
                        self.add_warning(f"Erro ao processar UM REGISTRO de lap (Mtg {m_key}, Sess {s_key}, Driver {d_num}): {lap_process_e}. Pulando para o próximo.")


                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Processamento de Laps concluído!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação/atualização (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação/atualização de Laps (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo do Processamento de Laps (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Triplas (M,S,D) processadas: {len(triplets_to_fetch) if 'triplets_to_fetch' in locals() else 0}"))
            self.stdout.write(self.style.SUCCESS(f"Registros de Laps encontrados na API (total): {laps_found_api_total}"))
            self.stdout.write(self.style.SUCCESS(f"Registros novos inseridos no DB: {laps_inserted_db}"))
            self.stdout.write(self.style.SUCCESS(f"Registros existentes atualizados no DB: {laps_updated_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB em modo 'I'): {laps_skipped_db}"))
            if laps_skipped_missing_data > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (dados obrigatórios ausentes): {laps_skipped_missing_data}"))
            if laps_skipped_invalid_date > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (formato de data inválido): {laps_skipped_invalid_date}"))
            if api_call_errors > 0:
                self.stdout.write(self.style.ERROR(f"Erros em chamadas à API: {api_call_errors}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Processamento de laps finalizado!"))