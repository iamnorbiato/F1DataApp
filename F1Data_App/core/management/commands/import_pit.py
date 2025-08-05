# G:\Learning\F1Data\F1Data_App\core\management\commands\import_pit.py
import requests
import json
from datetime import datetime, timezone, timedelta
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings

# Importa os modelos necessários
from core.models import Drivers, Sessions, Pit, Meetings 
from dotenv import load_dotenv
from update_token import update_api_token_if_needed
import pytz 

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

class Command(BaseCommand):
    help = 'Importa dados de pit stops da API OpenF1 e os insere na tabela pit do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/pit"

    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5
    BULK_SIZE = 5000

    def add_arguments(self, parser):
        parser.add_argument('--meeting_key', type=int, help='Especifica um Meeting Key para filtrar a importação (opcional).')
        parser.add_argument('--session_key', type=int, help='Especifica um Session Key para filtrar a importação (opcional).') # Adicionado session_key
        parser.add_argument('--mode', choices=['I', 'U'], default=None, # Alterado para None para controle automático no handle
                            help='Modo de operação: I=Insert apenas, U=Update (atualiza existentes e insere novos).')

    def add_warning(self, message):
        if not hasattr(self, 'warnings_count'):
            self.warnings_count = 0
            self.all_warnings_details = []
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    # Esta função será modificada para se alinhar com a nova estratégia
    def get_session_pairs_to_fetch(self, meeting_key_param=None, session_key_param=None, mode='I'):
        """
        Identifica os pares (meeting_key, session_key) a serem processados.
        Modo 'I' (descoberta): Pega meetings sem pits.
        Modo 'U' (direcionado): Pega meetings/sessions especificados.
        Retorna uma lista de tuplas (meeting_key, session_key) únicas.
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando pares (meeting_key, session_key) para buscar 'Pit'..."))

        session_pairs = []

        if meeting_key_param is None and session_key_param is None:
            # Modo de descoberta (mode='I')
            all_meeting_keys = set(Meetings.objects.values_list('meeting_key', flat=True).distinct())
            existing_pit_meeting_keys = set(Pit.objects.values_list('meeting_key', flat=True).distinct())
            meetings_to_discover = sorted(list(all_meeting_keys - existing_pit_meeting_keys))

            if not meetings_to_discover:
                self.stdout.write(self.style.NOTICE("Nenhum Meeting novo encontrado para buscar dados de Pit. Encerrando."))
                return []

            for m_key in meetings_to_discover:
                s_keys_for_meeting = Sessions.objects.filter(meeting_key=m_key).values_list('session_key', flat=True).distinct()
                for s_key in s_keys_for_meeting:
                    session_pairs.append((m_key, s_key))
            
            self.stdout.write(f"Modo de Descoberta: Encontrados {len(session_pairs)} pares (M,S) para buscar Pit Stops.")

        else:
            # Modo direcionado (mode='U' por padrão)
            if session_key_param is not None:
                # Se session_key é especificado, pega apenas esse par
                if meeting_key_param:
                    # Se meeting_key também foi passado, valida se o par existe
                    if Sessions.objects.filter(meeting_key=meeting_key_param, session_key=session_key_param).exists():
                         session_pairs.append((meeting_key_param, session_key_param))
                    else:
                        self.add_warning(f"Aviso: Par (Mtg {meeting_key_param}, Sess {session_key_param}) não encontrado na tabela Sessions. Nenhuma busca será feita.")
                else:
                    # Se apenas session_key, tenta encontrar o meeting_key associado
                    session_obj = Sessions.objects.filter(session_key=session_key_param).first()
                    if session_obj:
                        session_pairs.append((session_obj.meeting_key, session_key_param))
                    else:
                        self.add_warning(f"Aviso: Session Key {session_key_param} não encontrado na tabela Sessions. Nenhuma busca será feita.")
            elif meeting_key_param is not None:
                # Se apenas meeting_key é especificado, pega todas as sessões desse meeting
                s_keys_for_meeting = Sessions.objects.filter(meeting_key=meeting_key_param).values_list('session_key', flat=True).distinct()
                for s_key in s_keys_for_meeting:
                    session_pairs.append((meeting_key_param, s_key))
                self.stdout.write(f"Modo Direcionado: Encontrados {len(session_pairs)} pares (M,S) para o Meeting {meeting_key_param}.")
            
            if not session_pairs:
                self.stdout.write(self.style.WARNING("Nenhum par (M,S) válido para o modo direcionado com os parâmetros fornecidos. Encerrando."))
        
        return sorted(list(set(session_pairs))) # Retorna uma lista de tuplas únicas e ordenadas


    # fetch_pit_stops_data agora aceita meeting_key e session_key e não exige driver_number
    def fetch_pit_stops_data(self, meeting_key=None, session_key=None, use_token=True):
        """
        Busca dados de pit stops da API OpenF1 com base nos parâmetros fornecidos.
        Aceita meeting_key e/ou session_key.
        """
        params = {}
        if meeting_key is not None:
            params['meeting_key'] = meeting_key
        if session_key is not None:
            params['session_key'] = session_key
        
        if not params:
            raise CommandError("Pelo menos 'meeting_key' ou 'session_key' devem ser fornecidos para fetch_pit_stops_data.")

        url = f"{self.API_URL}?" + "&".join([f"{k}={v}" for k,v in params.items()])
        self.stdout.write(f"  Chamando API Pit com URL: {url}") # Loga a URL exata que será chamada

        headers = {"Accept": "application/json"}

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                self.add_warning("Token da API (OPENF1_API_TOKEN) não encontrado. Requisição será feita sem Authorization.")
            else:
                headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.add_warning("Uso do token desativado (use_token=False). Requisição será feita sem Authorization.")

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

    def process_pit_entry(self, pit_data_dict, mode):
        # REMOVIDO: def to_datetime(val) não é mais necessária aqui
        
        meeting_key = pit_data_dict.get("meeting_key")
        session_key = pit_data_dict.get("session_key")
        driver_number = pit_data_dict.get("driver_number")
        lap_number = pit_data_dict.get('lap_number')
        date_str = pit_data_dict.get('date') # O dado da API é usado diretamente como string

        if lap_number is None:
            lap_number = 0

        # === VALIDAÇÃO DE CAMPOS OBRIGATÓRIOS PARA PK ===
        # Valida apenas os campos que NUNCA devem ser None para uma PK de Pit Stop
        # Assumindo que meeting_key, session_key, driver_number, lap_number e date SÃO SEMPRE NECESSÁRIOS
        if any(val is None for val in [meeting_key, session_key, driver_number, lap_number, date_str]):
            missing_fields = [k for k,v in {'session_key': session_key, 'meeting_key': meeting_key, 'driver_number': driver_number, 'lap_number': lap_number, 'date': date_str}.items() if v is None]
            self.add_warning(f"Pit Stop ignorado: dados obrigatórios da PK ausentes. Faltando: {missing_fields}. Dados API: {pit_data_dict}")
            return 'skipped_missing_data'

        # REMOVIDO: Validação de formato de data, já que a string é usada crua
        # if date_str:
        #    try: ...
        #    except ValueError: ...

        pit_duration = pit_data_dict.get('pit_duration') # Pode ser string, float ou None da API
        pit_duration_parsed = None
        if pit_duration is not None:
            try:
                # Tenta converter para float, substituindo vírgulas por pontos se necessário
                pit_duration_parsed = float(str(pit_duration).replace(',', '.').strip())
            except ValueError:
                self.add_warning(f"Valor de 'pit_duration' '{pit_duration}' não é numérico. Ignorando para Sess {session_key}, Driver {driver_number}, Lap {lap_number}. Usando None.")
                pit_duration_parsed = None

        defaults = {
            'pit_duration': pit_duration_parsed
        }
        
        try:
            if mode == 'U':
                obj, created = Pit.objects.update_or_create(
                    session_key=session_key,
                    meeting_key=meeting_key,
                    driver_number=driver_number,
                    lap_number=lap_number,
                    date=date_str, # Usando a string bruta
                    defaults=defaults
                )
                return 'inserted' if created else 'updated'
            else: # mode == 'I'
                # Para Insert-only, verifica se já existe antes de criar para evitar IntegrityError
                if Pit.objects.filter(
                    session_key=session_key,
                    meeting_key=meeting_key,
                    driver_number=driver_number,
                    lap_number=lap_number,
                    date=date_str # Usando a string bruta
                ).exists():
                    return 'skipped'
                
                Pit.objects.create(
                    session_key=session_key,
                    meeting_key=meeting_key,
                    driver_number=driver_number,
                    lap_number=lap_number,
                    date=date_str, # Usando a string bruta
                    pit_duration=pit_duration_parsed
                )
                return 'inserted'

        except IntegrityError as ie: # Captura IntegrityError para logs mais claros
            self.add_warning(f"IntegrityError ao processar pit stop (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}, Lap {lap_number}, Date {date_str}). Erro: {ie}. Provavelmente duplicata. Ignorando.")
            return 'skipped'
        except Exception as e:
            data_debug = f"Mtg {pit_data_dict.get('meeting_key', 'N/A')}, Sess {pit_data_dict.get('session_key', 'N/A')}, Driver {pit_data_dict.get('driver_number', 'N/A')}, Lap {pit_data_dict.get('lap_number', 'N/A')}"
            self.stdout.write(self.style.ERROR(f"Erro FATAL ao processar registro de pit stop ({data_debug}): {e} - Dados API: {pit_data_dict}"))
            raise


    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        self.warnings_count = 0
        self.all_warnings_details = []

        meeting_key_param = options.get('meeting_key')
        session_key_param = options.get('session_key') # NOVO: Captura o session_key
        mode_param = options.get('mode') # Não define default aqui

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação/atualização de Pit Stops (ORM)..."))
        self.stdout.write(f"Parâmetros recebidos: meeting_key={meeting_key_param if meeting_key_param else 'Nenhum'}, session_key={session_key_param if session_key_param else 'Nenhum'}, mode={mode_param if mode_param else 'Automático'}")

        pit_stops_found_api_total = 0
        pit_stops_inserted_db = 0
        pit_stops_updated_db = 0
        pit_stops_skipped_db = 0
        pit_stops_skipped_missing_data = 0
        pit_stops_skipped_invalid_date = 0
        api_call_errors = 0

        sessions_api_calls_made = 0 # Contagem de chamadas API, não de sessões processadas

        # === Lógica de Determinação do Modo e API Calls ===
        api_calls_info = [] # Lista de dicionários de parâmetros para fetch_pit_stops_data
        
        actual_mode = 'I' # Default inicial é Insert Only
        if meeting_key_param is not None or session_key_param is not None:
            # Se algum parâmetro de filtro é passado, o modo padrão é 'U' (Upsert)
            actual_mode = 'U'

        # O parâmetro --mode na linha de comando sempre sobrescreve o modo automático
        if mode_param is not None:
            actual_mode = mode_param
        
        self.stdout.write(self.style.NOTICE(f"Modo de operação FINAL: '{actual_mode}'."))


        if meeting_key_param is None and session_key_param is None:
            # MODO 1: Descoberta de Novos Meetings para Pit Stops
            self.stdout.write(self.style.NOTICE("Modo AUTOMÁTICO: Descoberta de Novos Meetings (sem parâmetros de filtro)."))
            
            meetings_to_discover = self.get_session_pairs_to_fetch(mode=actual_mode) # Esta função já filtra para o modo I

            if not meetings_to_discover:
                self.stdout.write(self.style.NOTICE("Nenhum Meeting novo/elegível encontrado para buscar dados de Pit. Encerrando."))
                return
            
            # Para o modo de descoberta, a API é chamada por (M,S)
            for m_key, s_key in meetings_to_discover:
                api_calls_info.append({'meeting_key': m_key, 'session_key': s_key})

        else:
            # MODO 2: Importação Direcionada
            self.stdout.write(self.style.NOTICE("Modo AUTOMÁTICO: Importação Direcionada (com parâmetros de filtro)."))

            # Construir os parâmetros para a ÚNICA chamada API, conforme instruído
            call_params = {}
            if meeting_key_param is not None:
                call_params['meeting_key'] = meeting_key_param
            if session_key_param is not None:
                call_params['session_key'] = session_key_param
            
            if not call_params: # Deveria ser impossível aqui se meeting_key_param ou session_key_param não são None
                self.stdout.write(self.style.WARNING("Nenhum parâmetro válido para o modo direcionado. Encerrando."))
                return

            api_calls_info.append(call_params) # APENAS UMA CHAMADA API (ou poucas, se a API for flexível)
            
            if not api_calls_info:
                self.stdout.write(self.style.WARNING("Nenhum parâmetro válido (meeting_key ou session_key) fornecido para o modo direcionado. Encerrando."))
                return


        self.stdout.write(self.style.SUCCESS(f"Total de {len(api_calls_info)} chamadas de API de Pit Stops a serem feitas."))

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
            
            # Não precisamos mais do set deleted_msd_for_update_mode aqui,
            # pois a lógica de deleção no modo U foi simplificada para deletar tudo
            # da sessão/meeting da chamada API.

            for i, call_params in enumerate(api_calls_info):
                current_m_key_for_log = call_params.get('meeting_key', 'N/A')
                current_s_key_for_log = call_params.get('session_key', 'N/A')

                self.stdout.write(f"Buscando e processando pit stops para chamada {i+1}/{len(api_calls_info)}: Mtg {current_m_key_for_log}, Sess {current_s_key_for_log}...")
                sessions_api_calls_made += 1

                pit_data_from_api = self.fetch_pit_stops_data(
                    meeting_key=call_params.get('meeting_key'),
                    session_key=call_params.get('session_key'),
                    use_token=use_api_token_flag
                )

                if isinstance(pit_data_from_api, dict) and "error_status" in pit_data_from_api:
                    api_call_errors += 1
                    self.add_warning(f"Erro na API para Mtg {current_m_key_for_log}, Sess {current_s_key_for_log}: {pit_data_from_api['error_message']}")
                    continue

                if not pit_data_from_api:
                    self.stdout.write(self.style.WARNING(f"Nenhum registro de pit stops encontrado na API para Mtg {current_m_key_for_log}, Sess {current_s_key_for_log}."))
                    continue

                pit_stops_found_api_total += len(pit_data_from_api)
                self.stdout.write(f"Encontrados {len(pit_data_from_api)} registros de pit stops para Mtg {current_m_key_for_log}, Sess {current_s_key_for_log}. Processando...")

                # No modo 'U', a melhor estratégia para 'pit' é deletar todos os pit stops existentes
                # para o MEETING/SESSION que foi buscado na API e depois inserir os novos.
                if actual_mode == 'U':
                    with transaction.atomic():
                        # Lógica de deleção baseada nos parâmetros da chamada API
                        delete_filter_kwargs = {}
                        if call_params.get('meeting_key') is not None:
                            delete_filter_kwargs['meeting_key'] = call_params['meeting_key']
                        if call_params.get('session_key') is not None:
                            delete_filter_kwargs['session_key'] = call_params['session_key']
                        
                        if delete_filter_kwargs: # Só deleta se houver algum filtro
                            Pit.objects.filter(**delete_filter_kwargs).delete()
                            self.stdout.write(f"Registros de pit stops existentes deletados para {delete_filter_kwargs}.")
                        else:
                            self.add_warning("Aviso: Modo U ativado, mas nenhum filtro (meeting_key ou session_key) para deletar. Não deletando registros.")

                for pit_entry_dict in pit_data_from_api:
                    # Filtra os dados recebidos da API pelo driver_number e outros campos,
                    # se o modo 'I' exigir que a tripla (M,S,D) já tenha sido "descoberta".
                    # No modo direcionado, processamos tudo que a API envia.
                    
                    # No caso de Pit, a PK é (M, S, D, Lap, Date). 
                    # A API pode retornar pits para drivers que não estão na nossa tabela 'Drivers'.
                    # Ou em modo I, pode retornar pits para drivers que já existem.
                    # A lógica de process_pit_entry e exists() vai lidar com isso.
                    try:
                        result = self.process_pit_entry(pit_entry_dict, mode=actual_mode)
                        if result == 'inserted':
                            pit_stops_inserted_db += 1
                        elif result == 'updated':
                            pit_stops_updated_db += 1
                        elif result == 'skipped':
                            pit_stops_skipped_db += 1
                        elif result == 'skipped_missing_data':
                            pit_stops_skipped_missing_data += 1
                            self.add_warning(f"Pit Stop ignorado: dados obrigatórios da PK ausentes. Dados API: {pit_entry_dict}")
                        elif result == 'skipped_invalid_date':
                            pit_stops_skipped_invalid_date += 1
                    except Exception as pit_process_e:
                        self.add_warning(f"Erro ao processar UM REGISTRO de pit stop: {pit_process_e}. Dados API: {pit_entry_dict}. Pulando para o próximo.")
                    
                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Processamento de Pit Stops concluído!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação/atualização (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Pit Stops (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo do Processamento de Pit Stops (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Chamadas à API de Pit Stops realizadas: {sessions_api_calls_made}"))
            self.stdout.write(self.style.SUCCESS(f"Registros de Pit Stops encontrados na API (total): {pit_stops_found_api_total}"))
            self.stdout.write(self.style.SUCCESS(f"Registros novos inseridos no DB: {pit_stops_inserted_db}"))
            self.stdout.write(self.style.SUCCESS(f"Registros existentes atualizados no DB: {pit_stops_updated_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB em modo 'I'): {pit_stops_skipped_db}"))
            if pit_stops_skipped_missing_data > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (dados obrigatórios da PK ausentes): {pit_stops_skipped_missing_data}"))
            if pit_stops_skipped_invalid_date > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (formato de data inválido): {pit_stops_skipped_invalid_date}"))
            if api_call_errors > 0:
                self.stdout.write(self.style.ERROR(f"Erros em chamadas à API: {api_call_errors}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Processamento de pit stops finalizado!"))