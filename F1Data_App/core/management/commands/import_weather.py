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
    # CONFIG_FILE removido, pois não estamos usando import_config.json
    # CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json') # REMOVIDO

    API_DELAY_SECONDS = 0.2 # <--- Ajustado de volta para 0.2, como padrão
    API_MAX_RETRIES = 3 # Adicionado para consistência
    API_RETRY_DELAY_SECONDS = 5 # Adicionado para consistência

    warnings_count = 0 # <--- ADICIONADO: Contador de avisos na classe
    all_warnings_details = [] # <--- ADICIONADO: Lista para detalhes de avisos

    def add_warning(self, message): # <--- ADICIONADO: Método para adicionar avisos
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    def get_meeting_session_pairs_to_process(self):
        """
        Identifica quais pares (meeting_key, session_key) da tabela 'sessions'
        ainda não têm dados de weather importados na tabela 'weather'.
        Retorna uma lista de tuplas (meeting_key, session_key) a serem processadas.
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando pares (meeting_key, session_key) a processar para weather..."))
        
        # Obter todos os pares (meeting_key, session_key) da tabela 'sessions'
        self.stdout.write("Buscando todos os pares (meeting_key, session_key) da tabela 'sessions'...")
        all_session_pairs = set(
            Sessions.objects.all().values_list('meeting_key', 'session_key')
        )
        self.stdout.write(f"Encontrados {len(all_session_pairs)} pares (meeting_key, session_key) na tabela 'sessions'.")

        # Obter todos os pares (meeting_key, session_key) já presentes na tabela 'weather'
        self.stdout.write("Buscando pares (meeting_key, session_key) já presentes na tabela 'weather'...")
        existing_weather_pairs = set(
            Weather.objects.all().values_list('meeting_key', 'session_key')
        )
        self.stdout.write(f"Encontrados {len(existing_weather_pairs)} pares (meeting_key, session_key) na tabela 'weather'.")

        # Calcular a diferença: pares em 'sessions' mas não em 'weather'
        # ORDENADO para processamento consistente
        pairs_to_process = sorted(list(all_session_pairs - existing_weather_pairs))
        
        self.stdout.write(self.style.SUCCESS(f"Identificados {len(pairs_to_process)} pares (meeting_key, session_key) que precisam de dados de weather."))
        return pairs_to_process

    def fetch_weather_data(self, meeting_key, session_key, use_token=True):
        """
        Busca os dados de clima da API OpenF1 para um par (meeting_key, session_key) específico.
        meeting_key: A chave do meeting para filtrar a busca.
        session_key: A chave da sessão para filtrar a busca.
        use_token: Se True, usa o token de autorização. Se False, não.
        """
        if not meeting_key or not session_key:
            raise CommandError("meeting_key e session_key devem ser fornecidos para buscar dados de weather da API.")

        url = f"{self.API_URL}?meeting_key={meeting_key}&session_key={session_key}" # Constrói a URL com ambos os filtros

        headers = {
            "Accept": "application/json"
        }

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                self.add_warning("Token da API (OPENF1_API_TOKEN) não encontrado. Requisição será feita sem Authorization.") # Usando add_warning
                raise CommandError("Token da API (OPENF1_API_TOKEN) não encontrado. Requisição será feita sem Authorization.")
            else:
                headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.add_warning("Uso do token desativado (use_token=False). Requisição será feita sem Authorization.") # Usando add_warning
            pass 

        self.stdout.write(self.style.MIGRATE_HEADING(f"Buscando dados de weather para meeting_key {meeting_key}, session_key {session_key} da API: {url}"))
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"error_status": getattr(response, 'status_code', 'Unknown'), 
                    "error_url": url, 
                    "error_message": str(e)}

    def insert_weather_entry(self, weather_data):
        """
        Insere um único registro de clima no banco de dados usando ORM.
        Usa ON CONFLICT (session_key, meeting_key, session_date) DO NOTHING para evitar duplicatas.
        Retorna True se inserido, False se ignorado/já existe.
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
                raise ValueError(f"Dados de weather incompletos para PK: faltam campos NOT NULL {missing_fields}. Dados API: {weather_data}")

            session_date_obj = None
            if session_date_str:
                try:
                    session_date_obj = datetime.fromisoformat(session_date_str.replace('Z', '+00:00'))
                except ValueError:
                    raise ValueError(f"Formato de data inválido '{session_date_str}' para weather entry (Meeting {meeting_key}, Session {session_key}).")

            # Usamos create e IntegrityError para a lógica ON CONFLICT DO NOTHING
            Weather.objects.create(
                session_key=session_key,
                meeting_key=meeting_key,
                session_date=session_date_obj,
                wind_direction=wind_direction,
                air_temperature=air_temperature,
                humidity=humidity,
                pressure=pressure,
                rainfall=rainfall,
                wind_speed=wind_speed,
                track_temperature=track_temperature
            )
            return True # Inserido com sucesso
        except IntegrityError:
            # Captura erro de chave primária duplicada (ON CONFLICT (PK) DO NOTHING)
            return False # Já existia, ignorado
        except Exception as e:
            # Loga o erro, mas re-levanta para ser tratado no handle()
            data_debug = f"Mtg {weather_data.get('meeting_key', 'N/A')}, Sess {weather_data.get('session_key', 'N/A')}, Date {weather_data.get('date', 'N/A')}"
            self.stdout.write(self.style.ERROR(f"Erro FATAL ao inserir/processar registro de weather ({data_debug}): {e} - Dados API: {weather_data}"))
            raise # Re-levanta o erro para o handle() capturá-lo e fazer rollback

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH) 

        self.warnings_count = 0 
        self.all_warnings_details = [] # Zera a lista de detalhes de avisos para esta execução

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true' # Lê a flag do .env

        if use_api_token_flag:
            try:
                update_api_token_if_needed() # Verifica/renova o token
            except Exception as e:
                raise CommandError(f"Falha ao verificar/atualizar o token da API: {e}. Não é possível prosseguir com a importação.")
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no env.cfg). Buscando dados históricos."))

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação de Weather (ORM)..."))

        weather_entries_inserted_db = 0
        weather_entries_skipped_db = 0
        pairs_processed_count = 0 

        try: # Este try encapsula toda a lógica principal do handle
            # 1. Obter a lista de pares (meeting_key, session_key) a processar
            pairs_to_process = self.get_meeting_session_pairs_to_process()

            if not pairs_to_process:
                self.stdout.write(self.style.NOTICE("Nenhum novo par (meeting_key, session_key) encontrado para importar weather. Encerrando."))
                return # Sai do handle se não houver pares para processar

            self.stdout.write(self.style.SUCCESS(f"Total de {len(pairs_to_process)} pares (meeting_key, session_key) elegíveis para processamento."))
            self.stdout.write(f"Iniciando loop por {len(pairs_to_process)} pares elegíveis...")

            api_delay = self.API_DELAY_SECONDS 

            # Inicia uma transação de banco de dados
            with transaction.atomic(): # Usa a transação atômica do Django
                for i, (meeting_key, session_key) in enumerate(pairs_to_process):
                    # Mostra progresso para cada par processado
                    self.stdout.write(f"Processando par {i+1}/{len(pairs_to_process)}: meeting_key={meeting_key}, session_key={session_key}...")
                    pairs_processed_count += 1

                    # 2. Buscar dados de weather para o par (meeting_key, session_key) atual da API
                    weather_data_from_api = self.fetch_weather_data(meeting_key=meeting_key, session_key=session_key, use_token=use_api_token_flag) # Passa a flag

                    if isinstance(weather_data_from_api, dict) and "error_status" in weather_data_from_api:
                        self.stdout.write(self.style.ERROR(f"  Erro na API para par (Mtg {meeting_key}, Sess {session_key}): {weather_data_from_api['error_message']}. Pulando este par."))
                        self.add_warning(f"Erro API par (Mtg {meeting_key}, Sess {session_key}): {weather_data_from_api['error_message']}") # Adiciona ao detalhe
                        continue 

                    if not weather_data_from_api:
                        self.stdout.write(self.style.WARNING(f"  Aviso: Nenhuma entrada de weather encontrada na API para meeting_key={meeting_key}, session_key={session_key}."))
                        self.add_warning(f"Aviso: API vazia para par (Mtg {meeting_key}, Sess {session_key}).") # Adiciona ao detalhe
                        continue
                    
                    # 3. Inserir cada entrada no DB
                    for weather_entry in weather_data_from_api:
                        try: # Este try-except interno é para lidar com erros de UMA entrada e continuar os outros
                            inserted = self.insert_weather_entry(weather_entry)
                            if inserted is True:
                                weather_entries_inserted_db += 1
                            elif inserted is False:
                                weather_entries_skipped_db += 1
                        except Exception as weather_insert_e:
                            self.stdout.write(self.style.ERROR(f"Erro ao processar/inserir UMA ENTRADA de weather para meeting_key={meeting_key}, session_key={session_key}: {weather_insert_e}. Pulando para o próximo registro."))
                            self.add_warning(f"Erro inserir Weather (Mtg {meeting_key}, Sess {session_key}): {weather_insert_e}") # Adiciona ao detalhe

                    # Adiciona um delay APÓS cada chamada de API (loop do par meeting_key/session_key)
                    if api_delay > 0:
                        time.sleep(api_delay)


            self.stdout.write(self.style.SUCCESS("Importação de Weather concluída com sucesso para todos os pares elegíveis!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Weather (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Weather (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Pares (meeting_key, session_key) processados: {pairs_processed_count}"))
            self.stdout.write(self.style.SUCCESS(f"Registros inseridos no DB: {weather_entries_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB): {weather_entries_skipped_db}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
                self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
                self.stdout.write(self.style.SUCCESS("Importação de weather finalizada!"))
