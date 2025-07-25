# G:\Learning\F1Data\F1Data_App\core\management\commands\import_teamradio.py

import requests
import json
import os
import time
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, IntegrityError, OperationalError
from django.conf import settings

from core.models import Sessions, TeamRadio # Adicionado Sessions
from dotenv import load_dotenv
from update_token import update_api_token_if_needed

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')


class Command(BaseCommand):
    help = 'Importa dados de Team Radio da API OpenF1 filtrando por meeting_key e modo de operação (insert-only ou insert+update).'

    API_URL = "https://api.openf1.org/v1/team_radio"
    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5
    BULK_SIZE = 5000

    def add_arguments(self, parser):
        parser.add_argument('--meeting_key', type=int, help='Meeting key para buscar dados de Team Radio. (Opcional, se omitido, processa todos os meetings)')
        parser.add_argument('--mode', type=str, choices=['I', 'U'], default='I', help='Modo de operação: I=insert only, U=insert/update.')

    def add_warning(self, message):
        if not hasattr(self, 'warnings_count'):
            self.warnings_count = 0
            self.all_warnings_details = []
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    def get_meeting_session_pairs_to_fetch(self, meeting_key_filter=None, mode='I'):
        """
        Obtém todos os pares (meeting_key, session_key) da tabela 'Sessions'
        para buscar dados de Team Radio.
        Agora, considera *todos* os tipos de sessão.
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Obtendo pares (meeting_key, session_key) para buscar Team Radio..."))
        
        # Obter todas as sessões (removido o filtro session_type='Race')
        sessions_query = Sessions.objects.all()
        if meeting_key_filter:
            sessions_query = sessions_query.filter(meeting_key=meeting_key_filter)
        
        all_sessions_pairs = set(sessions_query.values_list('meeting_key', 'session_key'))
        self.stdout.write(f"Encontrados {len(all_sessions_pairs)} pares (meeting_key, session_key) para todas as sessões{' para o meeting_key especificado' if meeting_key_filter else ''}.")

        pairs_to_process = set()
        if mode == 'I':
            # Para 'I', precisamos subtrair o que já existe em TeamRadio (considerando M,S)
            existing_tr_ms_pairs = set(
                TeamRadio.objects.filter(
                    meeting_key__in=[m for m,s in all_sessions_pairs],
                    session_key__in=[s for m,s in all_sessions_pairs]
                ).values_list('meeting_key', 'session_key').distinct()
            )
            pairs_to_process = all_sessions_pairs - existing_tr_ms_pairs
            self.stdout.write(f"Modo 'I': {len(pairs_to_process)} pares (M,S) serão considerados para busca de novos Team Radio (ainda não existentes).")
        else: # mode == 'U'
            pairs_to_process = all_sessions_pairs
            self.stdout.write(f"Modo 'U': Todas as {len(pairs_to_process)} pares (M,S) relevantes serão consideradas para atualização/inserção.")

        # Ordena para processamento consistente
        return sorted(list(pairs_to_process))

    def fetch_team_radio_data(self, session_key, use_token=True):
        url = f"{self.API_URL}?session_key={session_key}"
        headers = {"Accept": "application/json"}

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                self.add_warning(f"Token da API (OPENF1_API_TOKEN) não encontrado. Requisição para Sess {session_key} será feita sem Authorization.")
            else:
                headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.add_warning(f"Uso do token desativado. Requisição para Sess {session_key} será feita sem Authorization.")

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

    def process_team_radio_entry(self, tr_data_dict, mode):
        def to_datetime(val):
            return datetime.fromisoformat(val.replace("Z", "+00:00")) if val else None

        meeting_key = tr_data_dict.get("meeting_key")
        session_key = tr_data_dict.get("session_key")
        date_str = tr_data_dict.get("date")
        
        # O campo 'date' é crucial para a PK e deve ser validado
        if any(val is None for val in [meeting_key, session_key, date_str]):
            missing_fields = [k for k,v in {
                'meeting_key': meeting_key, 'session_key': session_key, 'date': date_str
            }.items() if v is None]
            self.add_warning(f"Team Radio ignorado: dados obrigatórios ausentes para Mtg {meeting_key}, Sess {session_key}. Faltando: {missing_fields}")
            return 'skipped_missing_data'

        date_obj = to_datetime(date_str)
        if date_obj is None:
            self.add_warning(f"Formato de data inválido para Team Radio (Mtg {meeting_key}, Sess {session_key}): '{date_str}'.")
            return 'skipped_invalid_date'

        defaults = {
            "driver_number": tr_data_dict.get("driver_number"),
            "lap_number": tr_data_dict.get("lap_number"),
            "recording_url": tr_data_dict.get("recording_url"),
            "seconds_in_lap": tr_data_dict.get("seconds_in_lap"),
            "transcript": tr_data_dict.get("transcript"),
        }

        try:
            if mode == 'U':
                obj, created = TeamRadio.objects.update_or_create(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    date=date_obj, # 'date' é parte da PK
                    defaults=defaults
                )
                return 'inserted' if created else 'updated'
            else: # mode == 'I'
                if TeamRadio.objects.filter(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    date=date_obj
                ).exists():
                    return 'skipped'
                
                TeamRadio.objects.create(
                    meeting_key=meeting_key,
                    session_key=session_key,
                    date=date_obj,
                    **defaults
                )
                return 'inserted'
        except IntegrityError:
            self.add_warning(f"IntegrityError ao processar Team Radio (Mtg {meeting_key}, Sess {session_key}, Date {date_str}). Provavelmente duplicata. Ignorando.")
            return 'skipped'
        except Exception as e:
            self.add_warning(f"Erro FATAL ao processar Team Radio (Mtg {meeting_key}, Sess {session_key}, Date {date_str}): {e}. Dados API: {tr_data_dict}")
            raise


    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        self.warnings_count = 0
        self.all_warnings_details = []

        meeting_key_param = options.get('meeting_key')
        mode_param = options.get('mode', 'I')

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação/atualização de Team Radio (ORM)..."))
        self.stdout.write(f"Parâmetros recebidos: meeting_key={meeting_key_param if meeting_key_param else 'Nenhum'}, mode={mode_param}")

        tr_found_api_total = 0
        tr_inserted_db = 0
        tr_updated_db = 0
        tr_skipped_db = 0
        tr_skipped_missing_data = 0
        tr_skipped_invalid_date = 0
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

            pairs_to_fetch = self.get_meeting_session_pairs_to_fetch(
                meeting_key_filter=meeting_key_param,
                mode=mode_param
            )

            if not pairs_to_fetch:
                self.stdout.write(self.style.NOTICE("Nenhum par (M,S) encontrado para buscar dados de Team Radio. Encerrando."))
                return

            self.stdout.write(self.style.SUCCESS(f"Total de {len(pairs_to_fetch)} pares (M,S) elegíveis para busca na API."))

            for i, (m_key, s_key) in enumerate(pairs_to_fetch):
                self.stdout.write(f"Buscando e processando Team Radio para par {i+1}/{len(pairs_to_fetch)}: Mtg {m_key}, Sess {s_key}...")

                tr_data_from_api = self.fetch_team_radio_data(
                    session_key=s_key,
                    use_token=use_api_token_flag
                )

                if isinstance(tr_data_from_api, dict) and "error_status" in tr_data_from_api:
                    api_call_errors += 1
                    self.add_warning(f"Erro na API para Mtg {m_key}, Sess {s_key}: {tr_data_from_api['error_message']}")
                    continue

                if not tr_data_from_api:
                    self.stdout.write(self.style.WARNING(f"Nenhum registro de Team Radio encontrado na API para Mtg {m_key}, Sess {s_key}."))
                    continue

                tr_found_api_total += len(tr_data_from_api)
                self.stdout.write(f"Encontrados {len(tr_data_from_api)} registros de Team Radio para Mtg {m_key}, Sess {s_key}. Processando...")

                # Em modo 'U', a melhor estratégia para 'team_radio' é deletar todos os registros existentes para essa (M,S)
                # e depois inserir os novos, garantindo consistência com a API.
                if mode_param == 'U':
                    with transaction.atomic():
                        TeamRadio.objects.filter(
                            meeting_key=m_key,
                            session_key=s_key
                        ).delete()
                        self.stdout.write(f"Registros de Team Radio existentes deletados para Mtg {m_key}, Sess {s_key}.")
                
                for tr_entry_dict in tr_data_from_api:
                    try:
                        result = self.process_team_radio_entry(tr_entry_dict, mode=mode_param)
                        if result == 'inserted':
                            tr_inserted_db += 1
                        elif result == 'updated':
                            tr_updated_db += 1
                        elif result == 'skipped':
                            tr_skipped_db += 1
                        elif result == 'skipped_missing_data':
                            tr_skipped_missing_data += 1
                        elif result == 'skipped_invalid_date':
                            tr_skipped_invalid_date += 1
                    except Exception as tr_process_e:
                        self.add_warning(f"Erro ao processar UM REGISTRO de Team Radio (Mtg {m_key}, Sess {s_key}): {tr_process_e}. Pulando para o próximo.")


                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Processamento de Team Radio concluído!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação/atualização (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação/atualização de Team Radio (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo do Processamento de Team Radio (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Pares (M,S) processados: {len(pairs_to_fetch) if 'pairs_to_fetch' in locals() else 0}"))
            self.stdout.write(self.style.SUCCESS(f"Registros de Team Radio encontrados na API (total): {tr_found_api_total}"))
            self.stdout.write(self.style.SUCCESS(f"Registros novos inseridos no DB: {tr_inserted_db}"))
            self.stdout.write(self.style.SUCCESS(f"Registros existentes atualizados no DB: {tr_updated_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB em modo 'I'): {tr_skipped_db}"))
            if tr_skipped_missing_data > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (dados obrigatórios ausentes): {tr_skipped_missing_data}"))
            if tr_skipped_invalid_date > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (formato de data inválido): {tr_skipped_invalid_date}"))
            if api_call_errors > 0:
                self.stdout.write(self.style.ERROR(f"Erros em chamadas à API: {api_call_errors}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Processamento de Team Radio finalizado!"))