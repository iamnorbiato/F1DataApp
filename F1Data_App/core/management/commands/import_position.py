# G:\Learning\F1Data\F1Data_App\core\management\commands\import_position.py
import requests
import json
from datetime import datetime, timezone, timedelta
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings

# Importa os modelos necessários
from core.models import Drivers, Sessions, Position, Meetings
from dotenv import load_dotenv
from update_token import update_api_token_if_needed
import pytz 

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

class Command(BaseCommand):
    help = 'Importa dados de posição (position) da API OpenF1 e os insere na tabela positions do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/position"

    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5
    BULK_SIZE = 5000

    def add_arguments(self, parser):
        parser.add_argument(
            '--meeting_key',
            type=int,
            help='Especifica um Meeting Key para importar/atualizar (opcional). Usado em modo direcionado.'
        )
        parser.add_argument(
            '--session_key', # NOVO ARGUMENTO
            type=int,
            help='Especifica um Session Key para importar/atualizar (opcional). Usado em modo direcionado (prevalece sobre meeting_key se ambos passados).'
        )
        parser.add_argument(
            '--mode',
            type=str,
            choices=['I', 'U'],
            default=None, # Alterado para None para permitir definição automática no handle
            help='Modo de operação: I=Insert apenas, U=Update (atualiza existentes e insere novos). Padrão: I para descoberta, U para direcionado.'
        )

    def add_warning(self, message):
        if not hasattr(self, 'warnings_count'):
            self.warnings_count = 0
            self.all_warnings_details = []
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    # --- Funções Auxiliares de Descoberta ---
    def get_meetings_to_discover(self):
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando Meetings na tabela 'Meetings' que não existem na tabela 'Positions'..."))
        
        all_meeting_keys = set(Meetings.objects.values_list('meeting_key', flat=True).distinct())
        existing_position_meeting_keys = set(Position.objects.values_list('meeting_key', flat=True).distinct())

        meetings_to_fetch = sorted(list(all_meeting_keys - existing_position_meeting_keys))
        
        self.stdout.write(f"Encontrados {len(meetings_to_fetch)} Meetings para os quais buscar dados de Positions.")
        return meetings_to_fetch

    def get_sessions_for_meeting(self, meeting_key):
        """Retorna todos os session_keys para um dado meeting_key."""
        return list(Sessions.objects.filter(meeting_key=meeting_key).values_list('session_key', flat=True).distinct())

    # --- Função de Fetch da API (flexível) ---
    def fetch_position_data(self, meeting_key=None, session_key=None, use_token=True):
        """
        Busca dados de posição da API OpenF1 com base nos parâmetros fornecidos.
        Aceita meeting_key e/ou session_key. Driver_number não é usado diretamente na URL aqui.
        """
        params = {}
        if meeting_key is not None:
            params['meeting_key'] = meeting_key
        if session_key is not None:
            params['session_key'] = session_key
        
        if not params:
            raise CommandError("Pelo menos 'meeting_key' ou 'session_key' devem ser fornecidos para fetch_position_data.")

        # Constrói a URL de forma dinâmica com os parâmetros
        url = f"{self.API_URL}?" + "&".join([f"{k}={v}" for k,v in params.items()])
        self.stdout.write(f"  Chamando API Positions com URL: {url}") # Loga a URL exata que será chamada

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

    def create_position_instance(self, position_data_dict):
        """
        Constrói uma *nova* instância de modelo Position a partir de um dicionário de dados.
        Este método NÃO lida com INSERT/UPDATE, apenas com a construção do objeto.
        """
        def to_datetime(val):
            # Mantém a conversão para datetime para Positions, pois é importante para ordenação e PK.
            # Caso o campo 'date' do modelo Position seja CharField, esta função precisaria ser removida
            # e 'date_str' usado diretamente, similar ao import_pit.py.
            return datetime.fromisoformat(val.replace("Z", "+00:00")) if val else None

        meeting_key = position_data_dict.get('meeting_key')
        session_key = position_data_dict.get('session_key')
        driver_number = position_data_dict.get('driver_number')
        date_str = position_data_dict.get('date')
        position_value = position_data_dict.get('position')

        if any(val is None for val in [date_str, driver_number, meeting_key, session_key]):
            missing_fields = [k for k,v in {'date': date_str, 'driver_number': driver_number, 'meeting_key': meeting_key, 'session_key': session_key}.items() if v is None]
            raise ValueError(f"Dados incompletos para Position: faltam {missing_fields}. Dados: {position_data_dict}")

        date_obj = to_datetime(date_str)
        if date_obj is None:
            raise ValueError(f"Formato de data inválido para Posição (Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}): '{date_str}'.")

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
        session_key_param = options.get('session_key') # NOVO: Captura o session_key
        mode_param = options.get('mode') # Não define default aqui

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação/atualização de Positions (ORM)..."))
        self.stdout.write(f"Parâmetros recebidos: meeting_key={meeting_key_param if meeting_key_param else 'Nenhum'}, session_key={session_key_param if session_key_param else 'Nenhum'}, mode={mode_param if mode_param else 'Automático'}")

        positions_found_api_total = 0
        positions_inserted_db = 0
        positions_updated_db = 0 
        positions_skipped_db = 0 
        positions_skipped_missing_data = 0
        positions_skipped_invalid_date = 0
        api_call_errors = 0
        
        # === Lógica de Determinação do Modo e API Calls ===
        api_calls_info = [] # Lista de dicionários de parâmetros para fetch_position_data
        
        actual_mode = 'I' # Default inicial é Insert Only
        if meeting_key_param is not None or session_key_param is not None:
            # Se algum parâmetro de filtro é passado, o modo padrão é 'U' (Upsert)
            actual_mode = 'U'

        # O parâmetro --mode na linha de comando sempre sobrescreve o modo automático
        if mode_param is not None:
            actual_mode = mode_param
        
        self.stdout.write(self.style.NOTICE(f"Modo de operação FINAL: '{actual_mode}'."))


        if meeting_key_param is None and session_key_param is None:
            # MODO 1: Descoberta de Novos Meetings para Positions
            self.stdout.write(self.style.NOTICE("Modo AUTOMÁTICO: Descoberta de Novos Meetings (sem parâmetros de filtro)."))
            
            meetings_to_discover = self.get_meetings_to_discover()

            if not meetings_to_discover:
                self.stdout.write(self.style.NOTICE("Nenhum Meeting novo/elegível encontrado para buscar dados de Positions. Encerrando."))
                return

            for m_key in meetings_to_discover:
                session_keys_for_meeting = self.get_sessions_for_meeting(m_key)
                if not session_keys_for_meeting:
                    self.add_warning(f"Aviso: Meeting {m_key} não tem sessões. Pulando.")
                    continue
                for s_key in session_keys_for_meeting:
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
            
            if not call_params:
                self.stdout.write(self.style.WARNING("Nenhum parâmetro válido (meeting_key ou session_key) fornecido para o modo direcionado. Encerrando."))
                return

            api_calls_info.append(call_params) # APENAS UMA CHAMADA API (ou poucas, se a API for flexível)
            
            if not api_calls_info:
                self.stdout.write(self.style.WARNING("Erro interno: api_calls_info está vazia após a lógica direcionada. Encerrando."))
                return


        self.stdout.write(self.style.SUCCESS(f"Total de {len(api_calls_info)} chamadas de API de Positions a serem feitas."))

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
            
            total_meetings_processed_summary = 0 # Inicializado para o summary final
            
            for i, call_params in enumerate(api_calls_info):
                current_m_key_for_log = call_params.get('meeting_key', 'N/A')
                current_s_key_for_log = call_params.get('session_key', 'N/A')

                self.stdout.write(f"Buscando e processando Positions para chamada {i+1}/{len(api_calls_info)}: Mtg {current_m_key_for_log}, Sess {current_s_key_for_log}...")
                
                position_data_from_api = self.fetch_position_data(
                    meeting_key=call_params.get('meeting_key'),
                    session_key=call_params.get('session_key'),
                    use_token=use_api_token_flag
                )

                if isinstance(position_data_from_api, dict) and "error_status" in position_data_from_api:
                    api_call_errors += 1
                    self.add_warning(f"Erro na API para Mtg {current_m_key_for_log}, Sess {current_s_key_for_log}: {position_data_from_api['error_message']}")
                    continue

                if not position_data_from_api:
                    self.stdout.write(self.style.WARNING(f"Nenhum registro de positions encontrado na API para Mtg {current_m_key_for_log}, Sess {current_s_key_for_log}."))
                    continue

                positions_found_api_total += len(position_data_from_api)
                self.stdout.write(f"Encontrados {len(position_data_from_api)} registros de positions para Mtg {current_m_key_for_log}, Sess {current_s_key_for_log}. Processando...")

                # No modo 'U', a estratégia é deletar todos os positions existentes para o(s) critério(s) da chamada API
                if actual_mode == 'U':
                    with transaction.atomic():
                        delete_filter_kwargs = {}
                        if call_params.get('meeting_key') is not None:
                            delete_filter_kwargs['meeting_key'] = call_params['meeting_key']
                        if call_params.get('session_key') is not None:
                            delete_filter_kwargs['session_key'] = call_params['session_key']
                        
                        if delete_filter_kwargs: # Só deleta se houver algum filtro
                            Position.objects.filter(**delete_filter_kwargs).delete()
                            self.stdout.write(f"Registros de positions existentes deletados para {delete_filter_kwargs}.")
                        else: # Isso não deveria acontecer no modo U direcionado, mas é uma salvaguarda
                            self.add_warning("Aviso: Modo U ativado, mas nenhum filtro (meeting_key ou session_key) para deletar. Não deletando registros.")

                # Preparar para bulk_create
                instances_to_create = []
                for position_entry_dict in position_data_from_api:
                    try:
                        position_instance = self.create_position_instance(position_entry_dict)
                        instances_to_create.append(position_instance)
                    except ValueError as val_e: 
                        self.add_warning(f"Erro de validação ao construir instância de posição: {val_e}. Dados API: {position_entry_dict}. Pulando este registro.")
                        if "Formato de data inválido" in str(val_e):
                            positions_skipped_invalid_date += 1
                        else:
                            positions_skipped_missing_data += 1
                    except Exception as build_e: 
                        self.add_warning(f"Erro inesperado ao construir instância de posição: {build_e}. Dados API: {position_entry_dict}. Pulando este registro.")
                        positions_skipped_missing_data += 1 

                if instances_to_create:
                    with transaction.atomic():
                        try:
                            created_instances = Position.objects.bulk_create(
                                instances_to_create,
                                batch_size=self.BULK_SIZE,
                                ignore_conflicts=(actual_mode == 'I') 
                            )
                            created_count = len(created_instances)
                            skipped_conflict_count = len(instances_to_create) - created_count
                        except IntegrityError as ie: 
                            self.add_warning(f"IntegrityError durante bulk_create: {ie}. Alguns registros podem ter sido ignorados. Lote de: {len(instances_to_create)}.")
                            api_call_errors += 1
                            created_count = 0 # Não podemos garantir inserções bem sucedidas em caso de IntegrityError sem ignore_conflicts
                            skipped_conflict_count = len(instances_to_create) # Considera todos como pulados/erro para não superestimar
                        except Exception as bulk_e:
                            self.add_warning(f"Erro inesperado durante bulk_create: {bulk_e}. Pulando este lote de {len(instances_to_create)}.")
                            api_call_errors += 1
                            created_count = 0
                            skipped_conflict_count = len(instances_to_create)

                        positions_inserted_db += created_count
                        positions_skipped_db += skipped_conflict_count
                    
                    self.stdout.write(self.style.SUCCESS(f"  {created_count} registros inseridos, {skipped_conflict_count} ignorados (conflito PK) para Mtg {current_m_key_for_log}, Sess {current_s_key_for_log}."))
                else:
                    self.stdout.write(self.style.WARNING(f"  Nenhum registro válido de posição para processar para Mtg {current_m_key_for_log}, Sess {current_s_key_for_log}."))
                
                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Processamento de Positions concluído!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação/atualização (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação/atualização de Positions (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo do Processamento de Positions (ORM) ---"))
            # O total de meetings processados agora é o número de chamadas API no modo de descoberta
            self.stdout.write(self.style.SUCCESS(f"Chamadas à API de Positions realizadas: {len(api_calls_info)}"))
            self.stdout.write(self.style.SUCCESS(f"Registros de Positions encontrados na API (total): {positions_found_api_total}"))
            self.stdout.write(self.style.SUCCESS(f"Registros novos inseridos no DB: {positions_inserted_db}"))
            self.stdout.write(self.style.SUCCESS(f"Registros existentes atualizados no DB: {positions_updated_db}")) 
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB em modo 'I'): {positions_skipped_db}"))
            if positions_skipped_missing_data > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (dados obrigatórios ausentes/construção): {positions_skipped_missing_data}"))
            if positions_skipped_invalid_date > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (formato de data inválido): {positions_skipped_invalid_date}"))
            if api_call_errors > 0:
                self.stdout.write(self.style.ERROR(f"Erros em chamadas à API: {api_call_errors}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de positions finalizada!"))