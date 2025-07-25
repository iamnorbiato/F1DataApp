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

from core.models import Drivers, Sessions, SessionResult, Meetings # Adicionado Meetings para iterar por meeting_keys
from dotenv import load_dotenv
from update_token import update_api_token_if_needed
import pytz

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

class Command(BaseCommand):
    help = 'Importa dados de resultados de sessão (session_results) da API OpenF1 e os insere/atualiza na tabela sessionresult do PostgreSQL.'

    API_URL = "https://api.openf1.org/v1/session_result"

    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5
    BULK_SIZE = 5000

    def add_arguments(self, parser):
        parser.add_argument(
            '--meeting_key',
            type=int,
            help='Filtrar importação para um meeting_key específico (opcional). Se omitido, processa todos os meetings.'
        )
        parser.add_argument(
            '--mode',
            type=str,
            choices=['I', 'U'],
            default='I',
            help="Modo de operação: 'I' para inserir apenas novos (padrão), 'U' para forçar atualização de existentes e inserir novos."
        )

    def add_warning(self, message):
        if not hasattr(self, 'warnings_count'):
            self.warnings_count = 0
            self.all_warnings_details = []
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    # get_config_value foi removido, pois não é mais usado.

    def get_meeting_session_driver_triplets_to_fetch(self, meeting_key_filter=None, mode='I'):
        """
        Identifica triplas (meeting_key, session_key, driver_number) a processar para SessionResult.
        Considera meeting_key_filter e modo ('I' para apenas novos, 'U' para todos relevantes).
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando triplas (meeting_key, session_key, driver_number) a processar para 'SessionResult'..."))

        sessions_query = Sessions.objects.all() # Não filtra por session_type='Race'
        if meeting_key_filter:
            sessions_query = sessions_query.filter(meeting_key=meeting_key_filter)
        
        all_session_pairs = set(sessions_query.values_list('meeting_key', 'session_key'))
        self.stdout.write(f"Encontrados {len(all_session_pairs)} pares (meeting_key, session_key) em 'Sessions'{' para o meeting_key especificado' if meeting_key_filter else ''}.")

        all_driver_triplets = set(
            Drivers.objects.filter(
                meeting_key__in=[pair[0] for pair in all_session_pairs],
                session_key__in=[pair[1] for pair in all_session_pairs]
            ).values_list('meeting_key', 'session_key', 'driver_number')
        )
        self.stdout.write(f"Encontradas {len(all_driver_triplets)} triplas de drivers relevantes para as sessões consideradas.")

        triplets_to_process = set()
        if mode == 'I':
            # Para 'I', precisamos subtrair o que já existe em SessionResult (considerando M,S,D)
            existing_sr_msd_triplets = set(
                SessionResult.objects.filter(
                    meeting_key__in=[m for m,s,d in all_driver_triplets],
                    session_key__in=[s for m,s,d in all_driver_triplets],
                    driver_number__in=[d for m,s,d in all_driver_triplets]
                ).values_list('meeting_key', 'session_key', 'driver_number').distinct()
            )
            triplets_to_process = all_driver_triplets - existing_sr_msd_triplets
            self.stdout.write(f"Modo 'I': {len(triplets_to_process)} triplas (M,S,D) serão consideradas para busca de novos resultados (ainda não existentes).")
        else: # mode == 'U'
            triplets_to_process = all_driver_triplets
            self.stdout.write(f"Modo 'U': Todas as {len(triplets_to_process)} triplas (M,S,D) relevantes serão consideradas para atualização/inserção.")

        return sorted(list(triplets_to_process))

    def fetch_session_results_data(self, session_key, use_token=True):
        if not session_key:
            self.add_warning("session_key deve ser fornecido para buscar dados de resultados de sessão da API.")
            return {"error_status": "InvalidParams", "error_message": "Missing session_key"}

        url = f"{self.API_URL}?session_key={session_key}"

        headers = {"Accept": "application/json"}

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

    def create_session_result_instance(self, sr_data_dict):
        """
        Constrói uma *nova* instância de modelo SessionResult a partir de um dicionário de dados da API.
        Este método NÃO lida com INSERT/UPDATE, apenas com a construção do objeto.
        """
        position = sr_data_dict.get('position')
        driver_number = sr_data_dict.get('driver_number')
        number_of_laps = sr_data_dict.get('number_of_laps')
        meeting_key = sr_data_dict.get('meeting_key')
        session_key = sr_data_dict.get('session_key')

        dnf = sr_data_dict.get('dnf', False)
        dns = sr_data_dict.get('dns', False)
        dsq = sr_data_dict.get('dsq', False)

        raw_duration = sr_data_dict.get('duration')
        if raw_duration is None:
            processed_duration = None
        elif isinstance(raw_duration, list):
            processed_duration = raw_duration
        else:
            processed_duration = [raw_duration, None, None]

        raw_gap_to_leader = sr_data_dict.get('gap_to_leader')
        if raw_gap_to_leader is None:
            processed_gap_to_leader = None
        elif isinstance(raw_gap_to_leader, list):
            processed_gap_to_leader = raw_gap_to_leader
        else:
            processed_gap_to_leader = [raw_gap_to_leader, None, None]

        # Validação crítica para campos NOT NULL da PK
        if any(val is None for val in [meeting_key, session_key, driver_number]):
            missing_fields = [k for k,v in {'meeting_key': meeting_key, 'session_key': session_key, 'driver_number': driver_number}.items() if v is None]
            raise ValueError(f"Dados incompletos para SessionResult: faltam {missing_fields}. Dados: {sr_data_dict}")
        
        return SessionResult(
            position=position,
            driver_number=driver_number,
            number_of_laps=number_of_laps,
            meeting_key=meeting_key,
            session_key=session_key,
            dnf=dnf,
            dns=dns,
            dsq=dsq,
            duration=processed_duration,
            gap_to_leader=processed_gap_to_leader
        )

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)
        self.warnings_count = 0
        self.all_warnings_details = []

        meeting_key_param = options.get('meeting_key')
        mode_param = options.get('mode', 'I')
        
        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando importação/atualização de Resultados de Sessão (ORM)..."))
        self.stdout.write(f"Parâmetros recebidos: meeting_key={meeting_key_param if meeting_key_param else 'Nenhum'}, mode={mode_param}")

        sr_found_api_total = 0
        sr_inserted_db = 0
        sr_updated_db = 0 # No delete-then-insert, tudo é contado como inserido
        sr_skipped_db = 0 # Usado apenas para mode 'I' com ignore_conflicts
        sr_skipped_missing_data = 0
        api_call_errors = 0

        # Inicializa meetings_to_process com uma lista vazia antes do try
        meetings_to_process = []
        total_meetings_to_process_for_summary = 0

        try:
            if use_api_token_flag:
                try:
                    self.stdout.write("Verificando e atualizando o token da API, se necessário...")
                    update_api_token_if_needed()
                    # Recarrega as variáveis de ambiente após possível atualização
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

            if meeting_key_param:
                meetings_to_process.append(meeting_key_param)
            else:
                self.stdout.write(self.style.NOTICE("Nenhum meeting_key especificado. Processando todos os meetings existentes em 'Meetings'."))
                meetings_to_process = list(Meetings.objects.values_list('meeting_key', flat=True).distinct().order_by('meeting_key'))
                if not meetings_to_process:
                    self.stdout.write(self.style.WARNING("Nenhum meeting_key encontrado na tabela 'Meetings'. Encerrando."))
                    return

            total_meetings_to_process_for_summary = len(meetings_to_process)

            for m_idx, current_meeting_key in enumerate(meetings_to_process):
                self.stdout.write(self.style.MIGRATE_HEADING(f"\nIniciando processamento para Meeting Key: {current_meeting_key} ({m_idx + 1}/{total_meetings_to_process_for_summary})"))

                # Obter as triplas (M,S,D) relevantes para o meeting_key atual
                triplets_to_fetch_for_meeting = self.get_meeting_session_driver_triplets_to_fetch(
                    meeting_key_filter=current_meeting_key,
                    mode=mode_param
                )

                if not triplets_to_fetch_for_meeting:
                    self.stdout.write(self.style.NOTICE(f"Nenhuma tripla (M,S,D) encontrada para buscar resultados de sessão para Mtg {current_meeting_key}. Pulando este meeting."))
                    continue

                self.stdout.write(self.style.SUCCESS(f"Total de {len(triplets_to_fetch_for_meeting)} triplas (M,S,D) elegíveis para busca na API para Mtg {current_meeting_key}."))

                # Agrupa as triplas por session_key para fazer chamadas de API eficientes
                session_keys_to_fetch_api = sorted(list(set(s_key for _, s_key, _ in triplets_to_fetch_for_meeting)))
                
                for i, s_key in enumerate(session_keys_to_fetch_api):
                    self.stdout.write(f"Buscando e processando resultados de sessão para Sess {i+1}/{len(session_keys_to_fetch_api)}: Mtg {current_meeting_key}, Sess {s_key}...")
                    
                    sr_data_from_api = self.fetch_session_results_data(
                        session_key=s_key,
                        use_token=use_api_token_flag
                    )

                    if isinstance(sr_data_from_api, dict) and "error_status" in sr_data_from_api:
                        api_call_errors += 1
                        self.add_warning(f"Erro na API para Mtg {current_meeting_key}, Sess {s_key}: {sr_data_from_api['error_message']}")
                        continue

                    if not sr_data_from_api:
                        self.stdout.write(self.style.WARNING(f"Nenhum registro de resultado de sessão encontrado na API para Mtg {current_meeting_key}, Sess {s_key}."))
                        continue

                    sr_found_api_total += len(sr_data_from_api)
                    self.stdout.write(f"Encontrados {len(sr_data_from_api)} registros de resultados de sessão para Mtg {current_meeting_key}, Sess {s_key}. Processando...")

                    # Coleta as instâncias de modelo a serem criadas para esta sessão
                    current_session_results_instances = []
                    
                    if mode_param == 'U':
                        with transaction.atomic():
                            # Deleta todos os registros de resultado de sessão existentes para esta sessão (M,S)
                            # Isso garante que a atualização seja um espelho exato do que a API retorna.
                            SessionResult.objects.filter(
                                meeting_key=current_meeting_key,
                                session_key=s_key
                            ).delete()
                            self.stdout.write(self.style.NOTICE(f"Registros de resultados de sessão existentes deletados para Mtg {current_meeting_key}, Sess {s_key} (modo U)."))
                    
                    for sr_entry_dict in sr_data_from_api:
                        # Verifica se a tripla (M,S,D) do registro da API está na lista de triplas elegíveis para este meeting.
                        # Isso é importante para evitar processar resultados de drivers/sessões que não deveriam ser importados (ex: por filtro de modo 'I').
                        current_api_triple = (
                            sr_entry_dict.get('meeting_key'),
                            sr_entry_dict.get('session_key'),
                            sr_entry_dict.get('driver_number')
                        )
                        if current_api_triple not in triplets_to_fetch_for_meeting:
                            # Este caso pode ocorrer se a API retornar dados para drivers/sessões
                            # que já existem no DB no modo 'I', mas não estão na lista `triplets_to_fetch_for_meeting`
                            # porque já foram considerados "processados" (existentes no DB).
                            # Ou se a API retornar dados para drivers que não estão na sua tabela `Drivers`.
                            self.add_warning(f"Registro de SR ignorado: tripla (Mtg {current_api_triple[0]}, Sess {current_api_triple[1]}, Driver {current_api_triple[2]}) da API não é elegível para este meeting/modo.")
                            sr_skipped_db += 1 # Conta como pulado por não ser elegível
                            continue

                        try:
                            # Cria a instância do modelo SessionResult
                            sr_instance = self.create_session_result_instance(sr_entry_dict)
                            current_session_results_instances.append(sr_instance)
                        except ValueError as val_e: # Erros de validação (dados ausentes/inválidos)
                            self.add_warning(f"Erro de validação ao construir instância de SR para Mtg {current_api_triple[0]}, Sess {current_api_triple[1]}, Driver {current_api_triple[2]}: {val_e}. Pulando este registro.")
                            sr_skipped_missing_data += 1
                        except Exception as build_e: # Outros erros na construção
                            self.add_warning(f"Erro inesperado ao construir instância de SR para Mtg {current_api_triple[0]}, Sess {current_api_triple[1]}, Driver {current_api_triple[2]}: {build_e}. Pulando este registro.")
                            sr_skipped_missing_data += 1
                    
                    if current_session_results_instances:
                        with transaction.atomic():
                            # Usar bulk_create para inserir as instâncias
                            created_count = 0
                            skipped_conflict_count = 0
                            try:
                                created_records = SessionResult.objects.bulk_create(
                                    current_session_results_instances,
                                    batch_size=self.BULK_SIZE,
                                    ignore_conflicts=(mode_param == 'I') # Apenas ignora se for modo 'I'
                                )
                                created_count = len(created_records)
                                if mode_param == 'I':
                                    skipped_conflict_count = len(current_session_results_instances) - created_count
                            except IntegrityError as ie:
                                self.add_warning(f"IntegrityError durante bulk_create para Mtg {current_meeting_key}, Sess {s_key}: {ie}. Alguns registros podem ter sido ignorados.")
                                api_call_errors += 1 # Contabilizar como erro de API que impediu inserção
                                continue
                            except Exception as bulk_e:
                                self.add_warning(f"Erro inesperado durante bulk_create para Mtg {current_meeting_key}, Sess {s_key}: {bulk_e}. Pulando este lote.")
                                api_call_errors += 1
                                continue

                            sr_inserted_db += created_count
                            sr_skipped_db += skipped_conflict_count # Conflitos no modo 'I'
                        
                        self.stdout.write(self.style.SUCCESS(f"  {created_count} registros inseridos, {skipped_conflict_count} ignorados (conflito PK) para Mtg {current_meeting_key}, Sess {s_key}."))
                    else:
                        self.stdout.write(self.style.WARNING(f"  Nenhum registro válido de resultado de sessão para processar para Mtg {current_meeting_key}, Sess {s_key}."))
                    
                    if self.API_DELAY_SECONDS > 0:
                        time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Processamento de Resultados de Sessão concluído!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação/atualização (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação/atualização de Resultados de Sessão (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo Final do Processamento de Resultados de Sessão (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Total de Meetings processados: {total_meetings_to_process_for_summary}"))
            self.stdout.write(self.style.SUCCESS(f"Registros de SR encontrados na API (total): {sr_found_api_total}"))
            self.stdout.write(self.style.SUCCESS(f"Registros novos inseridos no DB: {sr_inserted_db}"))
            # sr_updated_db não é usado explicitamente com a estratégia delete-then-bulk_create,
            # pois tudo é reinserido no modo U. Você pode considerá-lo 0 ou o total de inseridos no modo U.
            self.stdout.write(self.style.SUCCESS(f"Registros existentes atualizados no DB: {sr_updated_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB em modo 'I'): {sr_skipped_db}"))
            if sr_skipped_missing_data > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (dados obrigatórios ausentes/construção): {sr_skipped_missing_data}"))
            if api_call_errors > 0:
                self.stdout.write(self.style.ERROR(f"Erros em chamadas à API: {api_call_errors}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de resultados de sessão finalizada!"))