# G:\Learning\F1Data\F1Data_App\core\management\commands\import_laps.py

import requests
import json
import os
import time
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, IntegrityError
from django.conf import settings

from core.models import Drivers, Laps, Sessions, Meetings # Incluí Meetings
from dotenv import load_dotenv
from update_token import update_api_token_if_needed

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')


class Command(BaseCommand):
    help = 'Importa dados de laps da API OpenF1 com modos de descoberta ou direcionados.'

    API_URL = "https://api.openf1.org/v1/laps"
    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5
    BULK_SIZE = 5000

    def add_arguments(self, parser):
        parser.add_argument(
            '--meeting_key',
            type=int,
            help='Meeting key para filtrar quais dados de laps importar. Usado em modo direcionado.'
        )
        parser.add_argument(
            '--session_key', # NOVO ARGUMENTO
            type=int,
            help='Session key para filtrar quais dados de laps importar. Usado em modo direcionado (prevalece sobre meeting_key se ambos passados).'
        )
        parser.add_argument(
            '--mode',
            type=str,
            choices=['I', 'U'],
            default=None, # Alterado para None para permitir definição automática no handle
            help='Modo de operação: I=insert only, U=insert/update. Padrão: I para descoberta, U para direcionado.'
        )

    def add_warning(self, message):
        if not hasattr(self, 'warnings_count'):
            self.warnings_count = 0
            self.all_warnings_details = []
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    # Esta função será usada apenas no modo "descoberta" para encontrar Meetings ausentes
    def get_meetings_to_discover(self):
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando Meetings na tabela 'Meetings' que não existem na tabela 'Laps'..."))
        
        all_meeting_keys = set(Meetings.objects.values_list('meeting_key', flat=True).distinct())
        
        # Obter todos os meeting_keys já presentes na tabela Laps
        existing_laps_meeting_keys = set(Laps.objects.values_list('meeting_key', flat=True).distinct())

        meetings_to_fetch = sorted(list(all_meeting_keys - existing_laps_meeting_keys))
        
        self.stdout.write(f"Encontrados {len(meetings_to_fetch)} Meetings para os quais buscar dados de Laps.")
        return meetings_to_fetch

    def get_sessions_for_meeting(self, meeting_key):
        """Retorna todos os session_keys para um dado meeting_key."""
        return list(Sessions.objects.filter(meeting_key=meeting_key).values_list('session_key', flat=True).distinct())


    # fetch_laps_data agora aceita meeting_key e session_key e não exige driver_number
    def fetch_laps_data(self, meeting_key=None, session_key=None, use_token=True):
        """
        Busca dados de laps da API OpenF1 com base nos parâmetros fornecidos.
        Aceita meeting_key e/ou session_key.
        """
        params = {}
        if meeting_key is not None:
            params['meeting_key'] = meeting_key
        if session_key is not None:
            params['session_key'] = session_key
        
        if not params:
            raise CommandError("Pelo menos 'meeting_key' ou 'session_key' devem ser fornecidos para fetch_laps_data.")

        url = f"{self.API_URL}?" + "&".join([f"{k}={v}" for k,v in params.items()])
        headers = {"Accept": "application/json"}

        # Loga a URL exata que será chamada
        self.stdout.write(f"  Chamando API Laps com URL: {url}")

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                self.add_warning("Token da API (OPENF1_API_TOKEN) não encontrado. Requisição será feita sem Authorization.")
            else:
                headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.add_warning("Uso do token desativado. Requisição será feita sem Authorization.")

        for attempt in range(self.API_MAX_RETRIES):
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                status_code = getattr(response, 'status_code', 'Unknown')
                error_url = response.url if hasattr(response, 'url') else url
                error_msg = f"Erro {status_code} da API para URL: {error_url} - {e}"
                if status_code in [500, 502, 503, 504, 401, 403, 422] and attempt < self.API_MAX_RETRIES - 1:
                    delay = self.API_RETRY_DELAY_SECONDS * (2 ** attempt)
                    self.add_warning(f"  {error_msg}. Tentativa {attempt + 1}/{self.API_MAX_RETRIES}. Retentando em {delay} segundos...")
                    time.sleep(delay)
                else:
                    self.add_warning(f"Falha na busca da API após retries para {error_url}: {error_msg}")
                    return {"error_status": status_code,
                            "error_url": error_url,
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
        if any(val is None for val in [meeting_key, session_key, driver_number, lap_number]): # , date_start_str
            missing_fields = [k for k,v in {
                'meeting_key': meeting_key,
                'session_key': session_key,
                'driver_number': driver_number,
                'lap_number': lap_number
#                'date_start': date_start_str
            }.items() if v is None]
            self.add_warning(f"Lap ignorado: dados obrigatórios da PK ausentes. Faltando: {missing_fields}. Dados API: {lap_data_dict}")
            return 'skipped_missing_data'

        date_start_obj = date_start_str
#        date_start_obj = to_datetime(date_start_str)
#        if date_start_obj is None: 
#            self.add_warning(f"Formato de data inválido para lap {lap_number} (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}): '{date_start_str}'. Dados API: {lap_data_dict}")
#            return 'skipped_invalid_date'

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
            # Note: Para `mode == 'U'`, update_or_create é chamado INDIVIDUALMENTE para cada registro da API.
            # Se a API retornar muitos registros, isso pode ser menos performático que bulk_update_or_create.
            # Porém, sem um bulk_update_or_create nativo do ORM e com a complexidade da PK,
            # update_or_create individual é a abordagem mais direta e segura.
            if mode == 'U':
                obj, created = Laps.objects.update_or_create(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    driver_number=driver_number,
                    lap_number=lap_number,
                    date_start=date_start_obj,
                    defaults=defaults
                )
                return 'inserted' if created else 'updated'
            else: # mode == 'I'
                # Para Insert-only, verifica se já existe antes de criar para evitar IntegrityError.
                # Esta verificação é crucial para o modo I.
                if Laps.objects.filter(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    driver_number=driver_number,
                    lap_number=lap_number,
                    date_start=date_start_obj
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
        except IntegrityError as ie:
            self.add_warning(f"IntegrityError ao processar lap (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}, Lap {lap_number}). Erro: {ie}. Provavelmente duplicata. Ignorando.")
            return 'skipped'
        except Exception as e:
            self.add_warning(f"Erro FATAL ao processar lap (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}, Lap {lap_number}): {e}. Dados API: {lap_data_dict}")
            raise


    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        self.warnings_count = 0
        self.all_warnings_details = []

        meeting_key_param = options.get('meeting_key')
        session_key_param = options.get('session_key') # NOVO: Captura o session_key
        mode_param = options.get('mode') # Não define default aqui

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação/atualização de Laps (ORM)..."))
        self.stdout.write(f"Parâmetros recebidos: meeting_key={meeting_key_param if meeting_key_param else 'Nenhum'}, session_key={session_key_param if session_key_param else 'Nenhum'}, mode={mode_param if mode_param else 'Automático'}")

        laps_found_api_total = 0
        laps_inserted_db = 0
        laps_updated_db = 0
        laps_skipped_db = 0
        laps_skipped_missing_data = 0
        laps_skipped_invalid_date = 0
        api_call_errors = 0

        # === Lógica de Determinação do Modo e API Calls ===
        api_calls_to_make = [] # Lista de dicionários de parâmetros para fetch_laps_data

        if meeting_key_param is None and session_key_param is None:
            # Modo 1: Descoberta de Novos Meetings
            actual_mode = 'I' if mode_param is None else mode_param
            self.stdout.write(self.style.NOTICE(f"Modo de operação AUTOMÁTICO: Descoberta de Novos Meetings (mode='{actual_mode}')."))
            meetings_to_discover = self.get_meetings_to_discover()

            if not meetings_to_discover:
                self.stdout.write(self.style.NOTICE("Nenhum Meeting novo encontrado para buscar dados de Laps. Encerrando."))
                return

            for m_key in meetings_to_discover:
                session_keys_for_meeting = self.get_sessions_for_meeting(m_key)
                if not session_keys_for_meeting:
                    self.add_warning(f"Aviso: Meeting {m_key} não tem sessões. Pulando.")
                    continue
                for s_key in session_keys_for_meeting:
                    api_calls_to_make.append({'meeting_key': m_key, 'session_key': s_key})
        else:
            # Modo 2: Importação Direcionada
            actual_mode = 'U' if mode_param is None else mode_param
            self.stdout.write(self.style.NOTICE(f"Modo de operação AUTOMÁTICO: Importação Direcionada (mode='{actual_mode}')."))

            if session_key_param is not None:
                # Se session_key é passado, é a chamada mais específica
                api_calls_to_make.append({'meeting_key': meeting_key_param, 'session_key': session_key_param})
            elif meeting_key_param is not None:
                # Se apenas meeting_key é passado, a API deve lidar com isso (presumido pela instrução)
                # O usuário pediu para chamar a API só com os parâmetros passados.
                # Assumimos que OpenF1 /laps?meeting_key=X funciona como um filtro amplo.
                api_calls_to_make.append({'meeting_key': meeting_key_param})
            
            if not api_calls_to_make:
                self.stdout.write(self.style.WARNING("Nenhum parâmetro válido (meeting_key ou session_key) fornecido para o modo direcionado. Encerrando."))
                return

        # === Processamento das Chamadas API ===
        self.stdout.write(self.style.SUCCESS(f"Total de {len(api_calls_to_make)} chamadas de API de Laps a serem feitas."))

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
            
            # Use um set para controlar os MSDs que já tiveram seus laps deletados no modo U
            deleted_msd_for_update_mode = set() 

            for i, call_params in enumerate(api_calls_to_make):
                current_m_key = call_params.get('meeting_key', 'N/A')
                current_s_key = call_params.get('session_key', 'N/A')
                self.stdout.write(f"Processando chamada {i+1}/{len(api_calls_to_make)}: Mtg {current_m_key}, Sess {current_s_key}...")

                laps_data_from_api = self.fetch_laps_data(
                    meeting_key=current_m_key,
                    session_key=current_s_key,
                    use_token=use_api_token_flag
                )

                if isinstance(laps_data_from_api, dict) and "error_status" in laps_data_from_api:
                    api_call_errors += 1
                    self.add_warning(f"Erro na API para Mtg {current_m_key}, Sess {current_s_key}: {laps_data_from_api['error_message']}")
                    continue

                if not laps_data_from_api:
                    self.stdout.write(self.style.WARNING(f"Nenhum registro de laps encontrado na API para Mtg {current_m_key}, Sess {current_s_key}."))
                    continue

                laps_found_api_total += len(laps_data_from_api)
                self.stdout.write(f"Encontrados {len(laps_data_from_api)} registros de laps para Mtg {current_m_key}, Sess {current_s_key}. Processando...")

                # Para o modo 'U', deletar todos os laps existentes para as triplas (M,S,D) contidas na resposta da API
                # antes de inseri-los. Isso é feito por tripla M,S,D para ser mais granular e seguro.
                if actual_mode == 'U' and laps_data_from_api:
                    unique_msd_in_api_response = set()
                    for lap_entry_dict in laps_data_from_api:
                        m_k = lap_entry_dict.get("meeting_key")
                        s_k = lap_entry_dict.get("session_key")
                        d_n = lap_entry_dict.get("driver_number")
                        if all([m_k, s_k, d_n]):
                            unique_msd_in_api_response.add((m_k, s_k, d_n))
                    
                    with transaction.atomic():
                        for msd_tuple in unique_msd_in_api_response:
                            if msd_tuple not in deleted_msd_for_update_mode: # Evita deletar múltiplas vezes para a mesma tripla
                                Laps.objects.filter(
                                    meeting_key=msd_tuple[0],
                                    session_key=msd_tuple[1],
                                    driver_number=msd_tuple[2]
                                ).delete()
                                deleted_msd_for_update_mode.add(msd_tuple)
                        self.stdout.write(f"Registros de laps existentes deletados para {len(unique_msd_in_api_response)} triplas (M,S,D) da resposta da API.")

                # Processar e inserir/atualizar os registros recebidos
                for lap_entry_dict in laps_data_from_api:
                    try:
                        result = self.process_lap_entry(lap_entry_dict, mode=actual_mode)
                        if result == 'inserted':
                            laps_inserted_db += 1
                        elif result == 'updated':
                            laps_updated_db += 1
                        elif result == 'skipped': # Modo I, registro já existe
                            laps_skipped_db += 1
                        elif result == 'skipped_missing_data':
                            laps_skipped_missing_data += 1
                            self.add_warning(f"Lap ignorado: dados obrigatórios da PK ausentes. Dados API: {lap_entry_dict}")
                        elif result == 'skipped_invalid_date':
                            laps_skipped_invalid_date += 1
                    except Exception as lap_process_e:
                        self.add_warning(f"Erro ao processar UM REGISTRO de lap: {lap_process_e}. Dados API: {lap_entry_dict}. Pulando para o próximo.")
                
                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação/atualização (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação/atualização de Laps (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo do Processamento de Laps (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Chamadas de API feitas: {len(api_calls_to_make)}"))
            self.stdout.write(self.style.SUCCESS(f"Registros de Laps encontrados na API (total): {laps_found_api_total}"))
            self.stdout.write(self.style.SUCCESS(f"Registros novos inseridos no DB: {laps_inserted_db}"))
            self.stdout.write(self.style.SUCCESS(f"Registros existentes atualizados no DB: {laps_updated_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB em modo 'I'): {laps_skipped_db}"))
            if laps_skipped_missing_data > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (dados obrigatórios da PK ausentes): {laps_skipped_missing_data}"))
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