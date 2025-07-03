# G:\Learning\F1Data\F1Data_App\core\management\commands\import_session_results.py
import requests
import json
from datetime import datetime
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings 

from core.models import Drivers, Sessions, SessionResult 
from dotenv import load_dotenv
from update_token import update_api_token_if_needed
import pytz # Para manipulação de fusos horários

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg') 

class Command(BaseCommand):
    help = 'Importa dados de resultados de sessão (session_results) da API OpenF1 e os insere na tabela sessionresult do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/session_result" # URL da API para session_results
    CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json')

    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3 
    API_RETRY_DELAY_SECONDS = 5 
    BULK_SIZE = 5000 

    warnings_count = 0 # Contador total de avisos
    all_warnings_details = [] # Lista para armazenar as mensagens detalhadas de aviso

    def add_warning(self, message):
        """Adiciona um aviso ao contador e à lista de detalhes."""
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message)) # Ainda mostra na tela se for um aviso

    def get_config_value(self, key=None, default=None, section=None):
        config = {}
        if not os.path.exists(self.CONFIG_FILE):
            self.add_warning(f"Aviso: Arquivo de configuração '{self.CONFIG_FILE}' não encontrado. Usando valor padrão para '{key}'.")
            return default
        try:
            with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)

            if section:
                section_data = config.get(section, default if key is None else {})
                if key is None:
                    return section_data
                else:
                    return section_data.get(key, default)
            else:
                if key is None:
                    return config
                else:
                    return config.get(key, default)

        except json.JSONDecodeError as e:
            raise CommandError(f"Erro ao ler/parsear o arquivo de configuração JSON '{self.CONFIG_FILE}': {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado ao acessar o arquivo de configuração: {e}")

    def get_session_driver_triplets_to_process(self): 
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando triplas (M,S,D) para sessões 'Race' de drivers e 'sessionresult'..."))
        
        self.stdout.write("Buscando pares (meeting_key, session_key) de sessões 'Race'...")
        race_sessions_pairs = set(
            Sessions.objects.filter(session_type='Race').values_list('meeting_key', 'session_key')
        )
        self.stdout.write(f"Encontrados {len(race_sessions_pairs)} pares (meeting_key, session_key) para sessões 'Race'.")

        self.stdout.write("Buscando todas as triplas de drivers (meeting_key, session_key, driver_number) da tabela 'drivers'...")
        all_driver_triplets = set(
            Drivers.objects.all().values_list('meeting_key', 'session_key', 'driver_number')
        )
        self.stdout.write(f"Encontradas {len(all_driver_triplets)} triplas de drivers.")

        relevant_driver_triplets = set(
            (m, s, d) for m, s, d in all_driver_triplets if (m, s) in race_sessions_pairs
        )
        self.stdout.write(f"Filtradas {len(relevant_driver_triplets)} triplas de drivers para sessões 'Race'.")

        self.stdout.write("Buscando triplas já presentes na tabela 'sessionresult'...")
        existing_sr_triplets = set(
            SessionResult.objects.all().values_list('meeting_key', 'session_key', 'driver_number')
        )
        self.stdout.write(f"Encontradas {len(existing_sr_triplets)} triplas já presentes na tabela 'sessionresult'.")

        triplets_to_process = sorted(list(relevant_driver_triplets - existing_sr_triplets))
        
        self.stdout.write(self.style.SUCCESS(f"Identificadas {len(triplets_to_process)} triplas (M,S,D) de drivers de sessões 'Race' que precisam de dados de resultados de sessão."))
        return triplets_to_process

    def fetch_session_results_data(self, session_key, use_token=True): # API filtra por session_key
        if not session_key:
            raise CommandError("session_key deve ser fornecido para buscar dados de resultados de sessão da API.")

        url = f"{self.API_URL}?session_key={session_key}" # API filtra apenas por session_key

        headers = {"Accept": "application/json"}

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                self.add_warning("Token da API (OPENF1_API_TOKEN) não encontrado. Requisição será feita sem Authorization.")
                raise CommandError("Token da API (OPENF1_API_TOKEN) não encontrado. Requisição será feita sem Authorization.")
            else:
                headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.add_warning("Uso do token desativado (use_token=False). Requisição será feita sem Authorization.")
            pass

        for attempt in range(self.API_MAX_RETRIES):
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                status_code = getattr(response, 'status_code', 'Unknown')
                if status_code in [500, 502, 503, 504] and attempt < self.API_MAX_RETRIES - 1:
                    delay = self.API_RETRY_DELAY_SECONDS * (2 ** attempt)
                    self.add_warning(f"Erro {status_code} da API para URL: {url}. Tentativa {attempt + 1}/{self.API_MAX_RETRIES}. Retentando em {delay} segundos...")
                    time.sleep(delay)
                else:
                    return {"error_status": status_code,
                            "error_url": url,
                            "error_message": str(e)}
            return {"error_status": "Failed after retries", "error_url": url, "error_message": "Max retries exceeded."}

    def build_session_result_instance(self, sr_data_dict):
        """
        Constrói uma instância de modelo SessionResult a partir de um dicionário de dados.
        Retorna a instância do modelo ou None se o registro deve ser ignorado.
        """
        position = sr_data_dict.get('position') 
        driver_number = sr_data_dict.get('driver_number')
        time_gap_raw = sr_data_dict.get('time_gap')
        number_of_laps = sr_data_dict.get('number_of_laps')
        meeting_key = sr_data_dict.get('meeting_key')
        session_key = sr_data_dict.get('session_key')

        # Validação crítica para campos NOT NULL na PK
        if any(val is None for val in [meeting_key, session_key, driver_number]):
            missing_fields = [k for k,v in {'meeting_key': meeting_key, 'session_key': session_key, 'driver_number': driver_number}.items() if v is None]
            raise ValueError(f"Dados incompletos para SessionResult: faltam {missing_fields}. Dados: {sr_data_dict}")
        
        # --- CORREÇÃO AQUI: Tratar position=None para 0 (se DDL for NOT NULL) ---
#        if position is None:
#            position = 0 # Valor padrão 0 se for None da API
#            self.add_warning(f"Aviso: 'position' é null para Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}. Usando 0.")
        # ------------------------------------------------------------------

        # Tratamento de time_gap para string (CharField no modelo) ou 0.0 se não numérico
#        time_gap_parsed = None # Deve ser None se não houver ou se não for string, para campos VARCHAR NULL
#        if time_gap_raw is not None:
#            try:
                # Tenta converter para float para saber se é numérico (para aviso), mas armazena como string.
#                float(str(time_gap_raw).replace(',', '.').strip()) 
#                time_gap_parsed = str(time_gap_raw).strip() # Se é numérico, salva como string.
#            except ValueError:
#                self.add_warning(f"Aviso: Valor de 'time_gap' '{time_gap_raw}' não é numérico. Usando NULL para Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}.")
#                time_gap_parsed = None # Se não for numérico, define como None
        # Se time_gap_raw é None, time_gap_parsed já será None

        return SessionResult(
            position=position,
            driver_number=driver_number,
            time_gap=time_gap_raw,
            number_of_laps=number_of_laps,
            meeting_key=meeting_key,
            session_key=session_key
        )

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)
        self.warnings_count = 0
        self.all_warnings_details = [] # Zera a lista de detalhes de avisos para esta execução

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        if use_api_token_flag:
            try:
                update_api_token_if_needed()
            except Exception as e:
                raise CommandError(f"Falha ao verificar/atualizar o token da API: {e}. Não é possível prosseguir com a importação.")
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no env.cfg). Buscando dados históricos."))

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação de Resultados de Sessão (ORM)..."))

        sr_inserted_db = 0
        sr_skipped_db = 0
        total_race_session_driver_triplets_eligible = 0 
        sessions_api_calls_processed_count = 0 

        try:
            total_race_session_driver_triplets_eligible = 0 
            
            all_driver_triplets = set(
                Drivers.objects.all().values_list('meeting_key', 'session_key', 'driver_number')
            )

            race_sessions_pairs = set(
                Sessions.objects.filter(session_type='Race').values_list('meeting_key', 'session_key')
            )

            relevant_driver_triplets = set(
                (m, s, d) for m, s, d in all_driver_triplets if (m, s) in race_sessions_pairs
            )
            
            existing_sr_triplets = set(
                SessionResult.objects.all().values_list('meeting_key', 'session_key', 'driver_number')
            )
            triplets_to_process = sorted(list(relevant_driver_triplets - existing_sr_triplets))

            total_race_session_driver_triplets_eligible = len(triplets_to_process)
            
            if not triplets_to_process:
                self.stdout.write(self.style.NOTICE("Nenhuma tripla (M,S,D) de sessões 'Race' encontrada para importar resultados de sessão. Encerrando."))
                return

            self.stdout.write(self.style.SUCCESS(f"Total de {total_race_session_driver_triplets_eligible} triplas (M,S,D) de drivers de sessões 'Race' para processamento."))
            
            api_delay = self.API_DELAY_SECONDS 

            session_pairs_to_fetch_api = {}
            for m_key, s_key, d_num in triplets_to_process:
                session_pairs_to_fetch_api.setdefault(s_key, []).append(d_num) 
            
            unique_session_keys_to_fetch = sorted(list(session_pairs_to_fetch_api.keys()))

            for i, session_key in enumerate(unique_session_keys_to_fetch): # Loop apenas por session_key
                # --- PRÉ-SUMÁRIO POR SESSÃO ---
                session_start_time = time.time()
                current_session_inserted_db = 0
                current_session_skipped_db = 0
                current_session_warnings = 0
                # ------------------------------

                self.stdout.write(f"Processando Sessão (Sess {session_key}) para resultados ({i+1}/{len(unique_session_keys_to_fetch)})...")
                sessions_api_calls_processed_count += 1
                current_session_results_objects = []
                
                try: 
                    sr_data_from_api = self.fetch_session_results_data(session_key=session_key, use_token=use_api_token_flag)
                    
                    if isinstance(sr_data_from_api, dict) and "error_status" in sr_data_from_api:
                        self.stdout.write(self.style.ERROR(f"  Erro na API para Sess {session_key}: {sr_data_from_api['error_message']}. Pulando esta sessão."))
                        self.add_warning(f"Erro API Sess {session_key}: {sr_data_from_api['error_message']}") # Adiciona ao detalhe
                        continue 

                    if not sr_data_from_api:
                        self.stdout.write(self.style.WARNING(f"  Aviso: Nenhuma entrada de resultados encontrada na API para Sess {session_key}."))
                        self.add_warning(f"Aviso: API vazia para Sess {session_key}.")
                        continue
                    
                    self.stdout.write(f"  Encontrados {len(sr_data_from_api)} registros para Sess {session_key}. Filtrando por driver e construindo...")

                    relevant_drivers_for_current_session_set = session_pairs_to_fetch_api.get(session_key, set()) 
                    
                    for sr_entry_dict in sr_data_from_api:
                        driver_num_from_api = sr_entry_dict.get('driver_number')
                        if driver_num_from_api in relevant_drivers_for_current_session_set: # Filtra drivers aqui
                            try:
                                sr_instance = self.build_session_result_instance(sr_entry_dict)
                                if sr_instance is not None:
                                    current_session_results_objects.append(sr_instance)
                            except Exception as build_e:
                                self.stdout.write(self.style.ERROR(f"Erro ao construir instância de resultado de sessão (Sess {session_key}, Driver {driver_num_from_api}): {build_e}. Pulando este registro."))
                                self.add_warning(f"Erro construir SR (Sess {session_key}, Driver {driver_num_from_api}): {build_e}")
                            
                        if current_session_results_objects:
                            with transaction.atomic(): # Transação atômica para TODO o lote desta SESSÃO
                                created_instances = SessionResult.objects.bulk_create(current_session_results_objects, batch_size=self.BULK_SIZE, ignore_conflicts=True)
                                sr_inserted_db += len(created_instances)
                                sr_skipped_db += (len(current_session_results_objects) - len(created_instances))
                                current_session_inserted_db += len(created_instances)
                                current_session_skipped_db += (len(current_session_results_objects) - len(created_instances))
#                                self.stdout.write(self.style.SUCCESS(f"  {len(created_instances)} registros inseridos (total para esta sessão)."))
                            
                        else:
                            self.add_warning(f"Aviso: Nenhuma entrada de resultado de sessão válida encontrada para inserção para Sess {session_key} (após filtragem de driver).")
                            
                        if api_delay > 0:
                            time.sleep(api_delay)

                except Exception as session_overall_e: 
                    self.stdout.write(self.style.ERROR(f"Erro ao processar Sessão (Sess {session_key}): {session_overall_e}. Esta sessão não foi processada por completo. Pulando para a próxima sessão."))
                    self.add_warning(f"Erro geral SR (Sess {session_key}): {session_overall_e}")
                finally:
                    # --- PRÉ-SUMÁRIO POR SESSÃO ---
                    session_end_time = time.time()
                    session_duration = session_end_time - session_start_time
                    self.stdout.write(self.style.MIGRATE_HEADING(f"  Resumo da Sessão {session_key}:"))
                    self.stdout.write(self.style.SUCCESS(f"    Inseridos: {current_session_inserted_db}"))
                    self.stdout.write(self.style.NOTICE(f"    Ignorados (existiam): {current_session_skipped_db}"))
                    self.stdout.write(self.style.WARNING(f"    Avisos específicos da sessão: {self.warnings_count - (total_race_session_driver_triplets_eligible - total_race_session_driver_triplets_eligible)}")) # Ajuste para warnings específicos da sessão
                    self.stdout.write(self.style.NOTICE(f"    Duração: {session_duration:.2f} segundos."))
                    # A variável meeting_key não é acessível diretamente aqui.
                    # Para mostrar Mtg key, teria que passar para o finally ou definir no loop.
                    # self.stdout.write(self.style.NOTICE(f"    Meeting: {meeting_key}")) 
                    # -----------------------------
                
            self.stdout.write(self.style.SUCCESS("Importação de Resultados de Sessão concluída com sucesso para todas as sessões e drivers elegíveis!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Resultados de Sessão (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Resultados de Sessão (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Triplas (meeting_key, session_key, driver_number) de drivers de Race elegíveis: {total_race_session_driver_triplets_eligible}")) 
            self.stdout.write(self.style.SUCCESS(f"Sessões (meeting_key, session_key) com API processada: {sessions_api_calls_processed_count}")) 
            self.stdout.write(self.style.SUCCESS(f"Registros inseridos no DB: {sr_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB): {sr_skipped_db}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details: # Se houver detalhes de avisos para mostrar
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de resultados de sessão finalizada!"))