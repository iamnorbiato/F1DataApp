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
import pytz # Para manipulação de fusos horários

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

class Command(BaseCommand):
    help = 'Importa dados de posição (position) da API OpenF1 e os insere na tabela positions do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/position"

    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5
    BULK_SIZE = 5000

    def add_arguments(self, parser):
        parser.add_argument('--meeting_key', type=int, help='Especifica um Meeting Key para importar/atualizar (opcional).')
        parser.add_argument('--mode', choices=['I', 'U'], default='I',
                            help='Modo de operação: I=Insert apenas (padrão), U=Update (atualiza existentes e insere novos).')

    def add_warning(self, message):
        if not hasattr(self, 'warnings_count'):
            self.warnings_count = 0
            self.all_warnings_details = []
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    def get_meeting_session_driver_triplets_to_fetch(self, meeting_key_filter=None, mode='I'):
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando triplas (meeting_key, session_key, driver_number) a processar para 'Positions'..."))

        sessions_query = Sessions.objects.all()
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
            existing_position_msd_triplets = set(
                Position.objects.filter(
                    meeting_key__in=[m for m,s,d in all_driver_triplets],
                    session_key__in=[s for m,s,d in all_driver_triplets],
                    driver_number__in=[d for m,s,d in all_driver_triplets]
                ).values_list('meeting_key', 'session_key', 'driver_number').distinct()
            )
            triplets_to_process = all_driver_triplets - existing_position_msd_triplets
            self.stdout.write(f"Modo 'I': {len(triplets_to_process)} triplas (M,S,D) serão consideradas para busca de novas posições (ainda não existentes).")
        else:
            triplets_to_process = all_driver_triplets
            self.stdout.write(f"Modo 'U': Todas as {len(triplets_to_process)} triplas (M,S,D) relevantes serão consideradas para atualização/inserção.")

        return sorted(list(triplets_to_process))


    def fetch_position_data(self, meeting_key, session_key, driver_number, use_token=True):
        if not (meeting_key and session_key and driver_number):
            self.add_warning(f"Dados incompletos para fetch_position_data: Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}. Pulando chamada.")
            return {"error_status": "InvalidParams", "error_message": "Missing meeting_key, session_key, or driver_number"}

        url = f"{self.API_URL}?meeting_key={meeting_key}&session_key={session_key}&driver_number={driver_number}"

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

    def create_position_instance(self, position_data_dict):
        """
        Constrói uma *nova* instância de modelo Position a partir de um dicionário de dados.
        Este método NÃO lida com INSERT/UPDATE, apenas com a construção do objeto.
        """
        def to_datetime(val):
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
        mode_param = options.get('mode', 'I')

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação/atualização de Positions (ORM)..."))
        self.stdout.write(f"Parâmetros recebidos: meeting_key={meeting_key_param if meeting_key_param else 'Nenhum'}, mode={mode_param}")

        positions_found_api_total = 0
        positions_inserted_db = 0
        positions_updated_db = 0 # No delete-then-insert, tudo é contado como inserido
        positions_skipped_db = 0 # Usado apenas para mode 'I' com ignore_conflicts
        positions_skipped_missing_data = 0
        positions_skipped_invalid_date = 0
        api_call_errors = 0

        # Inicializa meetings_to_process com uma lista vazia antes do try
        # Isso garante que a variável exista mesmo que um erro impeça sua definição nos blocos if/else
        meetings_to_process = []
        total_meetings_to_process_for_summary = 0

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

                triplets_to_fetch = self.get_meeting_session_driver_triplets_to_fetch(
                    meeting_key_filter=current_meeting_key,
                    mode=mode_param
                )

                if not triplets_to_fetch:
                    self.stdout.write(self.style.NOTICE(f"Nenhuma tripla (M,S,D) encontrada para buscar dados de posição para Mtg {current_meeting_key}. Pulando este meeting."))
                    continue

                self.stdout.write(self.style.SUCCESS(f"Total de {len(triplets_to_fetch)} triplas (M,S,D) elegíveis para busca na API para Mtg {current_meeting_key}."))

                for i, (m_key, s_key, d_num) in enumerate(triplets_to_fetch):
                    self.stdout.write(f"Buscando e processando posição para tripla {i+1}/{len(triplets_to_fetch)}: Mtg {m_key}, Sess {s_key}, Driver {d_num}...")
                    
                    position_data_from_api = self.fetch_position_data(
                        meeting_key=m_key,
                        session_key=s_key,
                        driver_number=d_num,
                        use_token=use_api_token_flag
                    )

                    if isinstance(position_data_from_api, dict) and "error_status" in position_data_from_api:
                        api_call_errors += 1
                        self.add_warning(f"Erro na API para Mtg {m_key}, Sess {s_key}, Driver {d_num}: {position_data_from_api['error_message']}")
                        continue

                    if not position_data_from_api:
                        self.stdout.write(self.style.WARNING(f"Nenhum registro de posição encontrado na API para Mtg {m_key}, Sess {s_key}, Driver {d_num}."))
                        continue

                    positions_found_api_total += len(position_data_from_api)
                    self.stdout.write(f"Encontrados {len(position_data_from_api)} registros de posição para Mtg {m_key}, Sess {s_key}, Driver {d_num}. Processando...")

                    if mode_param == 'U':
                        with transaction.atomic():
                            Position.objects.filter(
                                meeting_key=m_key,
                                session_key=s_key,
                                driver_number=d_num
                            ).delete()
                            self.stdout.write(self.style.NOTICE(f"Registros de posição existentes deletados para Mtg {m_key}, Sess {s_key}, Driver {d_num} (modo U)."))
                    
                    current_triplet_position_instances = []
                    for position_entry_dict in position_data_from_api:
                        try:
                            # Chamar create_position_instance para CONSTRUIR o objeto
                            position_instance = self.create_position_instance(position_entry_dict)
                            current_triplet_position_instances.append(position_instance)
                        except ValueError as val_e: # Erros de validação (dados ausentes/inválidos)
                            self.add_warning(f"Erro de validação ao construir instância de posição para Mtg {m_key}, Sess {s_key}, Driver {d_num}: {val_e}. Pulando este registro.")
                            if "Formato de data inválido" in str(val_e):
                                positions_skipped_invalid_date += 1
                            else:
                                positions_skipped_missing_data += 1
                        except Exception as build_e: # Outros erros na construção
                            self.add_warning(f"Erro inesperado ao construir instância de posição para Mtg {m_key}, Sess {s_key}, Driver {d_num}: {build_e}. Pulando este registro.")
                            positions_skipped_missing_data += 1 # Contabilizar como dado inválido/ausente

                    if current_triplet_position_instances:
                        with transaction.atomic():
                            # Usar bulk_create para inserir as instâncias
                            created_count = 0
                            skipped_conflict_count = 0
                            try:
                                created_instances = Position.objects.bulk_create(
                                    current_triplet_position_instances,
                                    batch_size=self.BULK_SIZE,
                                    ignore_conflicts=(mode_param == 'I') # Apenas ignora se for modo 'I'
                                )
                                created_count = len(created_instances)
                                if mode_param == 'I':
                                    skipped_conflict_count = len(current_triplet_position_instances) - created_count
                            except IntegrityError as ie: # Captura erros de integridade específicos do bulk_create se ignore_conflicts for False ou não funcionar como esperado
                                self.add_warning(f"IntegrityError durante bulk_create para Mtg {m_key}, Sess {s_key}, Driver {d_num}: {ie}. Alguns registros podem ter sido ignorados.")
                                # Neste caso, é difícil saber quantos foram inseridos/pulados com bulk_create se não for ignore_conflicts=True
                                # Para simplicidade, vamos considerar um erro e contabilizar no resumo
                                api_call_errors += 1
                                continue # Pula para a próxima tripla
                            except Exception as bulk_e:
                                self.add_warning(f"Erro inesperado durante bulk_create para Mtg {m_key}, Sess {s_key}, Driver {d_num}: {bulk_e}. Pulando este lote.")
                                api_call_errors += 1
                                continue # Pula para a próxima tripla

                            positions_inserted_db += created_count
                            positions_skipped_db += skipped_conflict_count
                        
                        self.stdout.write(self.style.SUCCESS(f"  {created_count} registros inseridos, {skipped_conflict_count} ignorados (conflito PK) para Mtg {m_key}, Sess {s_key}, Driver {d_num}."))
                    else:
                        self.stdout.write(self.style.WARNING(f"  Nenhum registro válido de posição para processar para Mtg {m_key}, Sess {s_key}, Driver {d_num}."))
                    
                    if self.API_DELAY_SECONDS > 0:
                        time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Processamento de Positions concluído!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação/atualização (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação/atualização de Positions (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo do Processamento de Positions (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Total de Meetings processados: {total_meetings_to_process_for_summary}"))
            self.stdout.write(self.style.SUCCESS(f"Registros de Positions encontrados na API (total): {positions_found_api_total}"))
            self.stdout.write(self.style.SUCCESS(f"Registros novos inseridos no DB: {positions_inserted_db}"))
            # O contador de "atualizados" não é relevante com a estratégia delete-then-insert + bulk_create
            # A menos que você queira redefinir 'positions_updated_db' para 0 e contar tudo como inserido.
            # Vou manter o comportamento atual (zerado) para 'U' já que tudo é reinserido.
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