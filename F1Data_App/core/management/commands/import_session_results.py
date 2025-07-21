# G:\Learning\F1Data\F1Data_App\core\management\commands\import_session_results.py

import requests
import json
from datetime import datetime
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings 
from django.db.models import Q # Importado para usar Q objects para filtragem
from django.db.models.functions import Cast # Importado para tratamento de tipos no ORM
from django.db.models import F, Case, When, Value, IntegerField # Importados para tratamento de tipos no ORM

from core.models import Drivers, Sessions, SessionResult # Garanta que SessionResult e Drivers estão corretos
from dotenv import load_dotenv
from update_token import update_api_token_if_needed # Assumindo que este script gerencia a validade do token
import pytz # Para manipulação de fusos horários

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg') 

class Command(BaseCommand):
    help = 'Importa dados de resultados de sessão (session_results) da API OpenF1 e os insere/atualiza na tabela sessionresult do PostgreSQL.'

    API_URL = "https://api.openf1.org/v1/session_result"
    CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json')

    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3 
    API_RETRY_DELAY_SECONDS = 5 
    BULK_SIZE = 5000 

    warnings_count = 0 
    all_warnings_details = [] 

    def add_warning(self, message):
        """Adiciona um aviso ao contador e à lista de detalhes."""
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        # self.stdout.write(self.style.WARNING(message)) # Suprimido para saída específica por sessão

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

    # Este método agora não filtra mais por session_type='Race'
    def get_session_driver_triplets_to_process(self, meeting_key_filter=None): 
        # self.stdout.write(self.style.MIGRATE_HEADING("Identificando triplas (M,S,D) para sessões 'Race' e 'sessionresult'...")) # Suprimido

        # ORIGINAL: sessions_query = Sessions.objects.filter(session_type='Race')
        # AGORA: Obtém todas as sessões, independentemente do tipo
        sessions_query = Sessions.objects.all() 

        if meeting_key_filter is not None:
            sessions_query = sessions_query.filter(meeting_key=meeting_key_filter)

        # Agora, race_sessions_pairs se torna all_sessions_pairs
        all_sessions_pairs = set(sessions_query.values_list('meeting_key', 'session_key'))
        # self.stdout.write(f"Encontrados {len(all_sessions_pairs)} pares (meeting_key, session_key) para TODAS as sessões (filtrado por meeting_key={meeting_key_filter if meeting_key_filter is not None else 'Todos'}).") # Suprimido

        drivers_query = Drivers.objects.all()
        if meeting_key_filter is not None:
            drivers_query = drivers_query.filter(meeting_key=meeting_key_filter)

        all_driver_triplets = set(
            drivers_query.values_list('meeting_key', 'session_key', 'driver_number')
        )
        # self.stdout.write(f"Encontradas {len(all_driver_triplets)} triplas de drivers.") # Suprimido

        # relevant_driver_triplets agora inclui drivers de TODAS as sessões presentes em all_sessions_pairs
        relevant_driver_triplets = set(
            (m, s, d) for m, s, d in all_driver_triplets if (m, s) in all_sessions_pairs
        )
        # self.stdout.write(f"Filtradas {len(relevant_driver_triplets)} triplas de drivers para sessões relevantes.") # Suprimido

        # Retorna todas as triplas elegíveis para serem processadas (sem filtro de existência aqui)
        # A decisão de inserir/atualizar será feita no handle()
        return sorted(list(relevant_driver_triplets))

    def fetch_session_results_data(self, session_key, use_token=True):
        if not session_key:
            raise CommandError("session_key deve ser fornecido para buscar dados de resultados de sessão da API.")

        url = f"{self.API_URL}?session_key={session_key}"

        headers = {"Accept": "application/json"}

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                self.add_warning("Token da API (OPENF1_API_TOKEN) não encontrado. Requisição será feita sem Authorization.")
                # Não levante CommandError aqui, apenas avise e deixe a requisição falhar naturalmente se necessário
            else:
                headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.add_warning("Uso do token desativado (use_token=False). Requisição será feita sem Authorization.")
            pass # continue sem token

        for attempt in range(self.API_MAX_RETRIES):
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status() # Lança HTTPError para 4xx/5xx
                return response.json()
            except requests.exceptions.RequestException as e:
                status_code = getattr(response, 'status_code', 'Unknown')
                if status_code in [500, 502, 503, 504] and attempt < self.API_MAX_RETRIES - 1:
                    delay = self.API_RETRY_DELAY_SECONDS * (2 ** attempt)
                    self.add_warning(f"Erro {status_code} da API para URL: {url}. Tentativa {attempt + 1}/{self.API_MAX_RETRIES}. Retentando em {delay} segundos...")
                    time.sleep(delay)
                else:
                    self.add_warning(f"Falha na API para URL: {url} após {attempt + 1} tentativas: {e} (Status: {status_code}).")
                    return {"error_status": status_code,
                            "error_url": url,
                            "error_message": str(e)}
        return {"error_status": "Failed after retries", "error_url": url, "error_message": "Max retries exceeded."}

    # MÉTODO build_session_result_instance agora inclui os novos campos da Model e tratamento de ArrayField
    def build_session_result_instance(self, sr_data_dict):
        """
        Constrói uma instância de modelo SessionResult a partir de um dicionário de dados da API.
        Inclui todos os campos da nova estrutura do modelo e trata inconsistências de array.
        """
        # O campo 'position' agora pode ser string, então não convertemos para int aqui.
        position = sr_data_dict.get('position') 
        driver_number = sr_data_dict.get('driver_number')
        number_of_laps = sr_data_dict.get('number_of_laps')
        meeting_key = sr_data_dict.get('meeting_key')
        session_key = sr_data_dict.get('session_key')
        
        # NOVOS CAMPOS DO JSON/MODEL SessionResult
        dnf = sr_data_dict.get('dnf', False) 
        dns = sr_data_dict.get('dns', False)
        dsq = sr_data_dict.get('dsq', False)

        # --- LÓGICA DE TRATAMENTO PARA ARRAY/NÃO-ARRAY DE DURATION E GAP_TO_LEADER ---
        # Tratamento para 'duration': se não for array, torna-se [valor, None, None]
        raw_duration = sr_data_dict.get('duration')
        if raw_duration is None:
            processed_duration = None
        elif isinstance(raw_duration, list):
            # Se já é uma lista, usa como está (ex: [87.943, 87.502, 86.983])
            processed_duration = raw_duration
        else:
            # Se não é uma lista (ex: float único), transforma em [valor, None, None]
            processed_duration = [raw_duration, None, None]

        # Tratamento para 'gap_to_leader': se não for array, torna-se [valor, None, None]
        raw_gap_to_leader = sr_data_dict.get('gap_to_leader')
        if raw_gap_to_leader is None:
            processed_gap_to_leader = None
        elif isinstance(raw_gap_to_leader, list):
            # Se já é uma lista, usa como está
            processed_gap_to_leader = raw_gap_to_leader
        else:
            # Se não é uma lista (ex: float ou string única), transforma em [valor, None, None]
            processed_gap_to_leader = [raw_gap_to_leader, None, None]
        # --- FIM DA LÓGICA DE TRATAMENTO ---

        # Validação crítica para campos NOT NULL na PK (meeting_key, session_key, driver_number)
        # meeting_key é primary_key=True no Model, session_key e driver_number são NOT NULL no DB
        if any(val is None for val in [meeting_key, session_key, driver_number]):
            missing_fields = [k for k,v in {'meeting_key': meeting_key, 'session_key': session_key, 'driver_number': driver_number}.items() if v is None]
            self.add_warning(f"Dados incompletos para SessionResult: faltam {missing_fields}. Dados: {sr_data_dict}. Pulando registro.")
            return None # Retorna None se dados da PK estiverem incompletos
        
        return SessionResult(
            position=position, # <-- Agora aceita string ou número (como string)
            driver_number=driver_number,
            number_of_laps=number_of_laps,
            # meeting_key é primary_key=True, então é usado aqui para identificar a instância
            meeting_key=meeting_key, 
            session_key=session_key, # session_key e driver_number são parte da unique_together
            # NOVOS CAMPOS ADICIONADOS (agora usando os valores processados)
            dnf=dnf,
            dns=dns,
            dsq=dsq,
            duration=processed_duration, # <-- Usando o valor processado
            gap_to_leader=processed_gap_to_leader # <-- Usando o valor processado
        )

    # Adiciona argumentos de linha de comando
    def add_arguments(self, parser):
        parser.add_argument(
            '--meeting_key',
            type=int,
            help='Filtrar importação para um meeting_key específico.'
        )
        parser.add_argument(
            '--mode',
            type=str,
            choices=['I', 'U'],
            default='I',
            help="Modo de operação: 'I' para inserir apenas novos (padrão), 'U' para forçar atualização de existentes e inserir novos."
        )

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)
        self.warnings_count = 0
        self.all_warnings_details = [] # Zera a lista de detalhes de avisos para esta execução

        # Obtém os valores dos argumentos passados
        meeting_key_filter = options['meeting_key']
        mode = options['mode']
        
        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        if use_api_token_flag:
            try:
                self.stdout.write(self.style.MIGRATE_HEADING("Verificando/Atualizando token da API (inicial)..."))
                update_api_token_if_needed() # Chamada inicial do token
                self.stdout.write(self.style.SUCCESS("Token da API verificado/atualizado com sucesso."))
            except Exception as e:
                raise CommandError(f"Falha ao verificar/atualizar o token da API: {e}. Não é possível prosseguir com a importação.")
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no env.cfg). Buscando dados históricos."))

        self.stdout.write(self.style.MIGRATE_HEADING(f"Iniciando importação de Resultados de Sessão no modo '{mode}'..."))

        total_sessions_processed = 0
        total_records_inserted = 0
        total_records_updated = 0
        total_records_skipped_mode_I = 0 # Contagem para modo 'I'

        # Identifica todas as triplas (M,S,D) relevantes (Race sessions, filtradas se meeting_key for dado)
        all_relevant_triplets = self.get_session_driver_triplets_to_process(meeting_key_filter)
        
        if not all_relevant_triplets:
            self.stdout.write(self.style.NOTICE("Nenhuma tripla de sessão/driver elegível encontrada para processamento. Encerrando."))
            return

        # Agrupa as triplas por session_key para fazer chamadas de API eficientes
        session_triplets_map = {} # {session_key: [(m_key, s_key, d_num), ...]}
        for m_key, s_key, d_num in all_relevant_triplets:
            session_triplets_map.setdefault(s_key, []).append((m_key, s_key, d_num)) 
        
        unique_session_keys_sorted = sorted(list(session_triplets_map.keys()))

        api_delay = self.API_DELAY_SECONDS 
        
        # Saída concisa por sessão
        self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo por Sessão ---"))

        for i, session_key in enumerate(unique_session_keys_sorted):
            session_inserted_count = 0
            session_updated_count = 0
            session_skipped_count = 0 # Para modo 'I'

            total_sessions_processed += 1
            
            if use_api_token_flag:
                try:
                    # Revalida o token antes de CADA chamada API, se necessário
                    update_api_token_if_needed() 
                except Exception as e:
                    self.add_warning(f"Falha ao verificar/atualizar token antes da Sessão {session_key}: {e}. Pulando esta sessão.")
                    continue 

            sr_data_from_api = self.fetch_session_results_data(session_key=session_key, use_token=use_api_token_flag) # <-- CORRIGIDO AQUI
            
            if isinstance(sr_data_from_api, dict) and "error_status" in sr_data_from_api:
                self.add_warning(f"Erro API para Sessão {session_key}: {sr_data_from_api['error_message']}. Pulando.")
                continue

            if not sr_data_from_api:
                self.add_warning(f"Aviso: API vazia para Sessão {session_key}.")
                continue
            
            # --- Lógica de Inserção/Atualização (Modo 'I' vs 'U') ---
            
            # Coleta os IDs completos (meeting_key, session_key, driver_number) dos registros existentes para esta sessão
            # E cria um mapa de (m,s,d) -> objeto SessionResult existente
            # REMOVIDO .iterator() AQUI PARA EVITAR ERRO DE 'id'
            existing_records_in_db_map = {
                (sr.meeting_key, sr.session_key, sr.driver_number): sr
                for sr in SessionResult.objects.filter(session_key=session_key)
            }
            
            instances_to_create = []
            instances_to_update_list = [] # Lista de objetos para atualização individual
            
            for sr_entry_dict in sr_data_from_api:
                m_key_api = sr_entry_dict.get('meeting_key')
                s_key_api = sr_entry_dict.get('session_key')
                d_num_api = sr_entry_dict.get('driver_number')
                
                # Validação para garantir que a tripla é relevante e completa
                current_triple = (m_key_api, s_key_api, d_num_api)
                if current_triple not in all_relevant_triplets: # Garante que só processamos triplas de Race sessions etc.
                    self.add_warning(f"Registro da API {current_triple} não relevante (não Race ou fora do filtro meeting_key). Pulando.")
                    continue

                try:
                    sr_instance_from_api = self.build_session_result_instance(sr_entry_dict)
                    if sr_instance_from_api is None: # build_session_result_instance retorna None para dados incompletos da PK
                        continue 

                    if current_triple in existing_records_in_db_map:
                        if mode == 'U':
                            # Atualizar registro existente: Pegue o objeto existente do DB e atualize seus campos
                            existing_sr_obj = existing_records_in_db_map[current_triple]
                            
                            # Atualiza os campos do objeto existente com os dados da API
                            existing_sr_obj.position = sr_instance_from_api.position
                            existing_sr_obj.number_of_laps = sr_instance_from_api.number_of_laps
                            existing_sr_obj.dnf = sr_instance_from_api.dnf
                            existing_sr_obj.dns = sr_instance_from_api.dns
                            existing_sr_obj.dsq = sr_instance_from_api.dsq
                            existing_sr_obj.duration = sr_instance_from_api.duration
                            existing_sr_obj.gap_to_leader = sr_instance_from_api.gap_to_leader
                            
                            instances_to_update_list.append(existing_sr_obj)
                            
                        else: # mode == 'I', então pula existentes
                            session_skipped_count += 1
                    else:
                        instances_to_create.append(sr_instance_from_api)

                except Exception as build_e:
                    self.add_warning(f"Erro ao preparar SR (Sess {s_key_api}, Driver {d_num_api}): {build_e}. Pulando registro.")
            
            # --- Inserção/Atualização no DB para a Sessão Atual ---
            try:
                with transaction.atomic():
                    # Inserir novos registros
                    if instances_to_create:
                        created_records = SessionResult.objects.bulk_create(instances_to_create, batch_size=self.BULK_SIZE, ignore_conflicts=True)
                        session_inserted_count += len(created_records)
                        # Se houver conflitos no modo 'I', eles são ignorados, então apenas contamos os criados
                        if mode == 'I':
                            session_skipped_count += (len(instances_to_create) - len(created_records))
                            
                    # Atualizar registros existentes (iterativo devido ao problema do 'id' com PK composta e managed=False)
                    if instances_to_update_list:
                        for obj_to_update in instances_to_update_list:
                            try:
                                # Filtra pela chave primária composta e atualiza os campos
                                SessionResult.objects.filter(
                                    meeting_key=obj_to_update.meeting_key,
                                    session_key=obj_to_update.session_key,
                                    driver_number=obj_to_update.driver_number
                                ).update(
                                    position=obj_to_update.position,
                                    number_of_laps=obj_to_update.number_of_laps,
                                    dnf=obj_to_update.dnf,
                                    dns=obj_to_update.dns,
                                    dsq=obj_to_update.dsq,
                                    duration=obj_to_update.duration,
                                    gap_to_leader=obj_to_update.gap_to_leader
                                )
                                session_updated_count += 1
                            except Exception as update_e:
                                self.add_warning(f"Erro ao ATUALIZAR SR {obj_to_update.meeting_key}-{obj_to_update.session_key}-{obj_to_update.driver_number}: {update_e}. Registro pode não ter sido atualizado.")
                                

            except IntegrityError as e:
                self.add_warning(f"Erro de integridade no DB para Sessão {session_key}: {e}. Registros podem ter sido pulados.")
            except Exception as e:
                self.add_warning(f"Erro DB ao processar Sessão {session_key}: {e}. Pulando sessão.")
            
            # Atualiza contadores totais
            total_records_inserted += session_inserted_count
            total_records_updated += session_updated_count
            total_records_skipped_mode_I += session_skipped_count

            # --- Saída de Resultados por Sessão (Formato Específico) ---
            self.stdout.write(f"session_key: {session_key}")
            self.stdout.write(f"Insert: {session_inserted_count}")
            self.stdout.write(f"update: {session_updated_count}")
            # Se você quiser o "skipped" no output por sessão no modo 'I', adicione aqui:
            # if mode == 'I':
            #     self.stdout.write(f"skipped: {session_skipped_count}")

            if api_delay > 0:
                time.sleep(api_delay) # Pequeno delay entre chamadas API
                
        # --- Resumo Final ---
        self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo Final da Importação ---"))
        self.stdout.write(self.style.SUCCESS(f"Sessões Processadas: {total_sessions_processed}"))
        self.stdout.write(self.style.SUCCESS(f"Registros Inseridos Total: {total_records_inserted}"))
        self.stdout.write(self.style.SUCCESS(f"Registros Atualizados Total: {total_records_updated}"))
        if mode == 'I':
            self.stdout.write(self.style.NOTICE(f"Registros Ignorados (já existiam no modo 'I'): {total_records_skipped_mode_I}"))
        self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas: {self.warnings_count}"))
        if self.all_warnings_details:
            self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
            for warn_msg in self.all_warnings_details:
                self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
        self.stdout.write(self.style.MIGRATE_HEADING("-----------------------------------"))
        self.stdout.write(self.style.SUCCESS("Importação de resultados de sessão finalizada!"))