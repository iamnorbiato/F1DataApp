# G:\Learning\F1Data\F1Data_App\core\management\commands\import_weather.py
import requests
import json
from datetime import datetime, timezone, timedelta
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings

# Importa os modelos necessários
from core.models import Sessions, Weather
from dotenv import load_dotenv
from update_token import update_api_token_if_needed
import pytz # Para manipulação de fusos horários

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

class Command(BaseCommand):
    help = 'Importa dados de clima (weather) da API OpenF1 e os insere na tabela weather do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/weather"

    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3
    API_RETRY_DELAY_SECONDS = 5

    def add_arguments(self, parser):
        # Adiciona o argumento opcional --meeting_key
        parser.add_argument('--meeting_key', type=int, help='Especifica um Meeting Key para importar/atualizar dados de clima (opcional).')

    def add_warning(self, message):
        # Inicializa se não estiverem inicializados (para segurança, embora handle os inicialize)
        if not hasattr(self, 'warnings_count'):
            self.warnings_count = 0
            self.all_warnings_details = []
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    def get_meeting_session_pairs_from_sessions(self, meeting_key_filter=None):
        """
        Obtém todos os pares (meeting_key, session_key) da tabela 'sessions'.
        Pode filtrar por um meeting_key específico.
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Obtendo pares (meeting_key, session_key) da tabela 'sessions'..."))
        query = Sessions.objects.all()
        if meeting_key_filter:
            query = query.filter(meeting_key=meeting_key_filter)

        session_pairs = sorted(list(query.values_list('meeting_key', 'session_key')))

        self.stdout.write(f"Encontrados {len(session_pairs)} pares (meeting_key, session_key) na tabela 'sessions'{' para o meeting_key especificado' if meeting_key_filter else ''}.")
        return session_pairs

    def fetch_weather_data(self, meeting_key, session_key, use_token=True):
        """
        Busca os dados de clima da API OpenF1 para um par (meeting_key, session_key) específico.
        """
        if not meeting_key or not session_key:
            raise CommandError("meeting_key e session_key devem ser fornecidos para buscar dados de weather da API.")

        url = f"{self.API_URL}?meeting_key={meeting_key}&session_key={session_key}"

        headers = {
            "Accept": "application/json"
        }

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                self.add_warning("Token da API (OPENF1_API_TOKEN) não encontrado em env. Requisição para "
                                 f"Mtg {meeting_key}, Sess {session_key} será feita sem Authorization.")
            else:
                headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.add_warning("Uso do token desativado. Requisição para "
                             f"Mtg {meeting_key}, Sess {session_key} será feita sem Authorization.")

        for attempt in range(self.API_MAX_RETRIES):
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                status_code = getattr(response, 'status_code', 'Unknown')
                error_msg = f"Erro {status_code} da API para URL: {url} - {e}"
                if status_code in [500, 502, 503, 504] and attempt < self.API_MAX_RETRIES - 1:
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

    def process_weather_entry(self, weather_data):
        """
        Processa um único registro de clima, inserindo ou atualizando.
        Retorna 'inserted', 'updated', ou 'skipped_missing_data'/'skipped_invalid_date'.
        """
        try:
            if not isinstance(weather_data, dict):
                raise ValueError(f"Dados de weather inesperados: esperado um dicionário, mas recebeu {type(weather_data)}: {weather_data}")

            # Mapeamento e tratamento de dados da API para as colunas do DB
            session_key = weather_data.get('session_key')
            meeting_key = weather_data.get('meeting_key')
            session_date_str = weather_data.get('date') # O JSON usa 'date'

            wind_direction = weather_data.get('wind_direction')
            air_temperature = weather_data.get('air_temperature')
            humidity = weather_data.get('humidity')
            pressure = weather_data.get('pressure')
            rainfall = weather_data.get('rainfall')
            wind_speed = weather_data.get('wind_speed')
            track_temperature = weather_data.get('track_temperature')

            # Validação crítica para campos NOT NULL na PK
            if any(val is None for val in [session_key, meeting_key, session_date_str]):
                missing_fields = [k for k,v in {'session_key': session_key, 'meeting_key': meeting_key, 'date': session_date_str}.items() if v is None]
                return 'skipped_missing_data'

            session_date_obj = None
            if session_date_str:
                try:
                    session_date_obj = datetime.fromisoformat(session_date_str.replace('Z', '+00:00'))
                except ValueError:
                    self.add_warning(f"Formato de data inválido '{session_date_str}' para weather (Mtg {meeting_key}, Sess {session_key}).")
                    return 'skipped_invalid_date'

            # Usa update_or_create para inserir ou atualizar
            obj, created = Weather.objects.update_or_create(
                session_key=session_key,
                meeting_key=meeting_key,
                session_date=session_date_obj, # Mapeia 'date' da API para 'session_date' do DB
                defaults={
                    'wind_direction': wind_direction,
                    'air_temperature': air_temperature,
                    'humidity': humidity,
                    'pressure': pressure,
                    'rainfall': rainfall,
                    'wind_speed': wind_speed,
                    'track_temperature': track_temperature
                }
            )
            return 'inserted' if created else 'updated'
        except Exception as e:
            data_debug = f"Mtg {weather_data.get('meeting_key', 'N/A')}, Sess {weather_data.get('session_key', 'N/A')}, Date {weather_data.get('date', 'N/A')}"
            self.stdout.write(self.style.ERROR(f"Erro FATAL ao processar registro de weather ({data_debug}): {e} - Dados API: {weather_data}"))
            raise


    def handle(self, *args, **options):
        # AQUI: load_dotenv inicial para carregar configs do ambiente inicial
        load_dotenv(dotenv_path=ENV_FILE_PATH)

        self.warnings_count = 0
        self.all_warnings_details = []

        meeting_key_param = options.get('meeting_key')

        # Use_api_token_flag lida abaixo, após possível atualização de token
        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação/atualização de Weather (ORM)..."))
        self.stdout.write(f"Parâmetros recebidos: meeting_key={meeting_key_param if meeting_key_param else 'Nenhum'}")

        weather_entries_found_api = 0
        weather_entries_inserted_db = 0
        weather_entries_updated_db = 0
        weather_entries_skipped_missing_data = 0
        weather_entries_skipped_invalid_date = 0
        api_call_errors = 0


        try:
            if use_api_token_flag:
                try:
                    self.stdout.write("Verificando e atualizando o token da API, se necessário...")
                    update_api_token_if_needed()
                    # >>> CORREÇÃO CRÍTICA AQUI: Recarrega as variáveis de ambiente após possível atualização <<<
                    # Isso garante que os.getenv() abaixo leia o TOKEN NOVO do arquivo env.cfg
                    load_dotenv(dotenv_path=ENV_FILE_PATH, override=True)
                    current_api_token = os.getenv('OPENF1_API_TOKEN')
                    if not current_api_token:
                        raise CommandError("Token da API (OPENF1_API_TOKEN) não disponível após verificação/atualização. Não é possível prosseguir com importação autenticada.")
                    self.stdout.write(self.style.SUCCESS("Token da API verificado/atualizado com sucesso."))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Falha ao verificar/atualizar o token da API: {e}. Prosseguindo sem usar o token da API."))
                    use_api_token_flag = False # Desativa o uso do token se houve falha na atualização

            if not use_api_token_flag:
                self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False ou falha na obtenção do token). Buscando dados sem autenticação."))

            # Determina quais pares (meeting_key, session_key) devem ser processados
            # Buscará todos os pares da tabela Sessions, opcionalmente filtrando por meeting_key
            pairs_to_fetch = self.get_meeting_session_pairs_from_sessions(meeting_key_filter=meeting_key_param)

            if not pairs_to_fetch:
                self.stdout.write(self.style.NOTICE("Nenhum par (meeting_key, session_key) encontrado para buscar dados de clima. Encerrando."))
                return

            self.stdout.write(self.style.SUCCESS(f"Total de {len(pairs_to_fetch)} pares (meeting_key, session_key) elegíveis para busca na API."))

            for i, (meeting_key, session_key) in enumerate(pairs_to_fetch):
                self.stdout.write(f"Buscando e processando clima para par {i+1}/{len(pairs_to_fetch)}: Mtg {meeting_key}, Sess {session_key}...")

                # Garante que api_token seja lido novamente para cada chamada, se necessário,
                # embora o load_dotenv(override=True) acima já garanta que os.getenv pegue o mais novo.
                # A variável 'use_api_token_flag' já reflete o status correto.
                weather_data_from_api = self.fetch_weather_data(meeting_key=meeting_key, session_key=session_key, use_token=use_api_token_flag)

                if isinstance(weather_data_from_api, dict) and "error_status" in weather_data_from_api:
                    api_call_errors += 1
                    self.add_warning(f"Erro na API para Mtg {meeting_key}, Sess {session_key}: {weather_data_from_api['error_message']}")
                    continue

                if not weather_data_from_api:
                    self.stdout.write(self.style.WARNING(f"Nenhum registro de clima encontrado na API para Mtg {meeting_key}, Sess {session_key}."))
                    continue

                weather_entries_found_api += len(weather_data_from_api)
                self.stdout.write(f"Encontrados {len(weather_data_from_api)} registros de clima para Mtg {meeting_key}, Sess {session_key}. Processando...")

                for weather_entry in weather_data_from_api:
                    try:
                        with transaction.atomic():
                            result = self.process_weather_entry(weather_entry)
                            if result == 'inserted':
                                weather_entries_inserted_db += 1
                            elif result == 'updated':
                                weather_entries_updated_db += 1
                            elif result == 'skipped_missing_data':
                                weather_entries_skipped_missing_data += 1
                                self.add_warning(f"Clima ignorado: dados obrigatórios ausentes para Mtg {weather_entry.get('meeting_key', 'N/A')}, Sess {weather_entry.get('session_key', 'N/A')}.")
                            elif result == 'skipped_invalid_date':
                                weather_entries_skipped_invalid_date += 1
                    except Exception as weather_process_e:
                        self.add_warning(f"Erro ao processar UM REGISTRO de clima (Mtg {weather_entry.get('meeting_key', 'N/A')}, Sess {weather_entry.get('session_key', 'N/A')}, Date {weather_entry.get('date', 'N/A')}): {weather_process_e}. Pulando para o próximo registro.")

                if self.API_DELAY_SECONDS > 0:
                    time.sleep(self.API_DELAY_SECONDS)

            self.stdout.write(self.style.SUCCESS("Processamento de Clima concluído!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação/atualização (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação/atualização de Clima (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo do Processamento de Clima (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Pares (meeting_key, session_key) processados: {len(pairs_to_fetch) if 'pairs_to_fetch' in locals() else 0}"))
            self.stdout.write(self.style.SUCCESS(f"Registros de Clima encontrados na API (total): {weather_entries_found_api}"))
            self.stdout.write(self.style.SUCCESS(f"Registros novos inseridos no DB: {weather_entries_inserted_db}"))
            self.stdout.write(self.style.SUCCESS(f"Registros existentes atualizados no DB: {weather_entries_updated_db}"))
            if weather_entries_skipped_missing_data > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (dados obrigatórios ausentes): {weather_entries_skipped_missing_data}"))
            if weather_entries_skipped_invalid_date > 0:
                self.stdout.write(self.style.WARNING(f"Registros ignorados (formato de data inválido): {weather_entries_skipped_invalid_date}"))
            if api_call_errors > 0:
                self.stdout.write(self.style.ERROR(f"Erros em chamadas à API: {api_call_errors}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Processamento de clima finalizado!"))