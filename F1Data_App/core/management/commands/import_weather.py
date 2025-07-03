# G:\Learning\F1Data\F1Data_App\core\management\commands\import_weather.py
import requests
import json
from datetime import datetime
import os
import time # Para o sleep da API

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction

# Importa os modelos necessários
from core.models import Sessions, Weather # Precisamos de Sessions para obter os pares (meeting_key, session_key)
from dotenv import load_dotenv # Para carregar variáveis do .env
from update_token import update_api_token_if_needed # Para verificar/renovar o token da API

# --- CORREÇÃO AQUI: Usa settings.BASE_DIR para o caminho do env.cfg ---
ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')
# -----------------------------------------------------------------------

class Command(BaseCommand):
    help = 'Importa dados de clima (weather) da API OpenF1 e os insere na tabela weather do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/weather" # URL da API para weather
    CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json') # Caminho para o arquivo de config

    API_DELAY_SECONDS = 0.2 # <--- Adicionado: Delay de 0.2 segundos entre as chamadas da API (ajuste conforme necessário)

    def get_config_value(self, key=None, default=None, section=None):
        """
        Lê um valor de configuração de um arquivo JSON.
        Se 'key' for None e 'section' for fornecido, retorna o dicionário completo da seção.
        Assume que o arquivo está na mesma pasta do script.
        """
        config = {}
        if not os.path.exists(self.CONFIG_FILE):
            self.stdout.write(self.style.WARNING(f"Aviso: Arquivo de configuração '{self.CONFIG_FILE}' não encontrado. Usando valor padrão para '{key}'."))
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

    def fetch_weather_data(self, meeting_key, session_key, use_token=True): # <--- Adicionado use_token
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
                self.warnings_count += 1
                raise CommandError("Token da API (OPENF1_API_TOKEN) não encontrado. Requisição será feita sem Authorization.")
            else:
                headers["Authorization"] = f"Bearer {api_token}"
        else:
            # A mensagem geral de desativação do token será controlada no handle()
            pass 

        self.stdout.write(self.style.MIGRATE_HEADING(f"Buscando dados de weather para meeting_key {meeting_key}, session_key {session_key} da API: {url}"))
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise CommandError(f"Erro ao buscar dados de weather da API ({url}): {e}")

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
        load_dotenv() # Carrega as variáveis do .env

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true' # Lê a flag do .env

        if use_api_token_flag:
            try:
                update_api_token_if_needed() # Verifica/renova o token
            except Exception as e:
                raise CommandError(f"Falha ao verificar/atualizar o token da API: {e}. Não é possível prosseguir com a importação.")
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no .env). Buscando dados históricos."))

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
            # A transação atomic() está agora DENTRO DO LOOP FOR para atomicity por item
            for i, (meeting_key, session_key) in enumerate(pairs_to_process):
                try: # Este try encapsula a transação atômica para UM PAR (MEETING, SESSION)
                    with transaction.atomic(): # Transação atômica por CADA PAR
                        # Mostra progresso para cada par processado (se necessário)
                        self.stdout.write(f"Processando par {i+1}/{len(pairs_to_process)}: meeting_key={meeting_key}, session_key={session_key}...")
                        pairs_processed_count += 1

                        # 2. Buscar dados de weather para o par (meeting_key, session_key) atual da API
                        weather_data_from_api = self.fetch_weather_data(meeting_key=meeting_key, session_key=session_key, use_token=use_api_token_flag)

                        if not weather_data_from_api:
                            self.stdout.write(self.style.WARNING(f"Aviso: Nenhuma entrada de weather encontrada na API para meeting_key={meeting_key}, session_key={session_key}."))
                            continue # Pula para o próximo par se não houver dados

                        # self.stdout.write(f"Encontradas {len(weather_data_from_api)} entradas para meeting_key={meeting_key}, session_key={session_key}. Inserindo...") # Removido para output limpo

                        # 3. Inserir cada entrada no DB
                        for weather_entry in weather_data_from_api:
                            try: # Este try-except interno é para lidar com erros de UMA entrada e continuar as outras (dentro do atomic)
                                inserted = self.insert_weather_entry(weather_entry)
                                if inserted is True:
                                    weather_entries_inserted_db += 1
                                elif inserted is False:
                                    weather_entries_skipped_db += 1
                            except Exception as weather_insert_e:
                                self.stdout.write(self.style.ERROR(f"Erro ao processar/inserir UMA ENTRADA de weather para meeting_key={meeting_key}, session_key={session_key}: {weather_insert_e}. Pulando para a próxima entrada."))

                    # Adiciona um delay APÓS cada chamada de API (loop do par meeting_key/session_key)
                    if api_delay > 0:
                        time.sleep(api_delay)

                except Exception as pair_process_e: # Captura erros de processamento do PAR (API call, ou loop interno)
                    self.stdout.write(self.style.ERROR(f"Erro ao processar par (Mtg {meeting_key}, Sess {session_key}): {pair_process_e}. Este par não foi processado por completo. Pulando para o próximo par."))
                    # A transação já foi desfeita pelo atomic() interno se um erro fatal ocorreu nele.


            self.stdout.write(self.style.SUCCESS("Importação de Weather concluída com sucesso para todos os pares elegíveis!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Weather (ORM): {e}")
        finally:
            # Sumário final
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Weather (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Pares (meeting_key, session_key) processados: {pairs_processed_count}"))
            self.stdout.write(self.style.SUCCESS(f"Registros inseridos no DB: {weather_entries_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB): {weather_entries_skipped_db}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de weather finalizada!"))