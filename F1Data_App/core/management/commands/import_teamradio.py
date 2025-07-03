# G:\Learning\F1Data\F1Data_App\core\management\commands\import_teamradio.py
import requests
import json
from datetime import datetime
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings # Importado para settings.BASE_DIR

# Importa os modelos necessários
from core.models import Sessions, TeamRadio # Precisamos de Sessions para obter os pares (meeting_key, session_key)
from dotenv import load_dotenv
from update_token import update_api_token_if_needed
import pytz # Para manipulação de fusos horários

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg') 

class Command(BaseCommand):
    help = 'Importa dados de rádio da equipe (team_radio) da API OpenF1 e os insere na tabela teamradio do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/team_radio" # URL da API para team_radio

    API_DELAY_SECONDS = 0.2 # Delay de 0.2 segundos entre as chamadas da API (ajuste conforme necessário)
    API_MAX_RETRIES = 3 
    API_RETRY_DELAY_SECONDS = 5 

    warnings_count = 0
    all_warnings_details = []

    def add_warning(self, message):
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

    def get_config_value(self, key=None, default=None, section=None):
        # Este método não é mais usado por não usarmos import_config.json
        pass # Apenas um placeholder, ele deve ser removido ou não ser chamado.

    def get_meeting_session_pairs_to_process(self):
        """
        Identifica quais pares (meeting_key, session_key) da tabela 'sessions'
        ainda não têm dados de team_radio importados na tabela 'teamradio'.
        Retorna uma lista de tuplas (meeting_key, session_key) a serem processadas.
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Identificando pares (meeting_key, session_key) a processar para teamradio..."))

        # Obter todos os pares (meeting_key, session_key) da tabela 'sessions'
        self.stdout.write("Buscando todos os pares (meeting_key, session_key) da tabela 'sessions'...")
        all_session_pairs = set(
            Sessions.objects.all().values_list('meeting_key', 'session_key')
        )
        self.stdout.write(f"Encontrados {len(all_session_pairs)} pares (meeting_key, session_key) na tabela 'sessions'.")

        # Obter todos os pares (meeting_key, session_key) já presentes na tabela 'teamradio'
        self.stdout.write("Buscando pares (meeting_key, session_key) já presentes na tabela 'teamradio'...")
        existing_teamradio_pairs = set(
            TeamRadio.objects.all().values_list('meeting_key', 'session_key')
        )
        self.stdout.write(f"Encontrados {len(existing_teamradio_pairs)} pares (meeting_key, session_key) na tabela 'teamradio'.")

        # Calcular a diferença: pares em 'sessions' mas não em 'teamradio'
        # ORDENADO para processamento consistente
        pairs_to_process = sorted(list(all_session_pairs - existing_teamradio_pairs))

        self.stdout.write(self.style.SUCCESS(f"Identificados {len(pairs_to_process)} pares (meeting_key, session_key) que precisam de dados de teamradio."))
        return pairs_to_process

    def fetch_teamradio_data(self, meeting_key, session_key, use_token=True): # <--- Adicionado use_token
        """
        Busca os dados de rádio da equipe da API OpenF1 para um par (meeting_key, session_key) específico.
        meeting_key: A chave do meeting para filtrar a busca.
        session_key: A chave da sessão para filtrar a busca.
        use_token: Se True, usa o token de autorização. Se False, não.
        """
        if not meeting_key or not session_key:
            raise CommandError("meeting_key e session_key devem ser fornecidos para buscar dados de teamradio da API.")

        url = f"{self.API_URL}?meeting_key={meeting_key}&session_key={session_key}" # Constrói a URL com ambos os filtros

        headers = {
            "Accept": "application/json"
        }

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

    def insert_teamradio_entry(self, tr_data):
        """
        Insere um único registro de rádio da equipe no banco de dados usando ORM.
        Usa ON CONFLICT (meeting_key, session_key, driver_number, date) DO NOTHING para evitar duplicatas.
        Retorna True se inserido, False se ignorado/já existe.
        """
        try:
            if not isinstance(tr_data, dict):
                raise ValueError(f"Dados de teamradio inesperados: esperado um dicionário, mas recebeu {type(tr_data)}: {tr_data}")

            # Mapeamento e tratamento de dados da API para as colunas do DB
            meeting_key = tr_data.get('meeting_key')
            session_key = tr_data.get('session_key')
            driver_number = tr_data.get('driver_number')
            date_str = tr_data.get('date') # O JSON usa 'date'
            recording_url = tr_data.get('recording_url')

            # Validação crítica para campos NOT NULL na PK
            if any(val is None for val in [meeting_key, session_key, driver_number, date_str]):
                missing_fields = [k for k,v in {'meeting_key': meeting_key, 'session_key': session_key, 'driver_number': driver_number, 'date': date_str}.items() if v is None]
                raise ValueError(f"Dados de teamradio incompletos para PK: faltam campos NOT NULL {missing_fields}. Dados API: {tr_data}")

            date_obj = None
            if date_str:
                try:
                    date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                except ValueError:
                    raise ValueError(f"Formato de data inválido '{date_str}' para teamradio entry (Meeting {meeting_key}, Session {session_key}, Driver {driver_number}).")

            # Usamos create e IntegrityError para a lógica ON CONFLICT DO NOTHING
            TeamRadio.objects.create(
                meeting_key=meeting_key,
                session_key=session_key,
                driver_number=driver_number,
                date=date_obj,
                recording_url=recording_url
            )
            return True # Inserido com sucesso
        except IntegrityError:
            # Captura erro de chave primária duplicada (ON CONFLICT (PK) DO NOTHING)
            return False # Já existia, ignorado
        except Exception as e:
            # Loga o erro, mas re-levanta para ser tratado no handle()
            data_debug = f"Mtg {tr_data.get('meeting_key', 'N/A')}, Sess {tr_data.get('session_key', 'N/A')}, Driver {tr_data.get('driver_number', 'N/A')}"
            self.stdout.write(self.style.ERROR(f"Erro FATAL ao inserir/processar registro de teamradio ({data_debug}): {e} - Dados API: {tr_data}"))
            raise # Re-levanta o erro para o handle() capturá-lo e fazer rollback

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


        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação de Team Radio (ORM)..."))

        tr_entries_inserted_db = 0
        tr_entries_skipped_db = 0
        pairs_processed_count = 0 

        try: # Este try encapsula toda a lógica principal do handle
            # 1. Obter a lista de pares (meeting_key, session_key) a processar
            pairs_to_process = self.get_meeting_session_pairs_to_process()

            if not pairs_to_process:
                self.stdout.write(self.style.NOTICE("Nenhum novo par (meeting_key, session_key) encontrado para importar teamradio. Encerrando."))
                return # Sai do handle se não houver pares para processar

            self.stdout.write(self.style.SUCCESS(f"Total de {len(pairs_to_process)} pares (meeting_key, session_key) elegíveis para processamento."))
            self.stdout.write(f"Iniciando loop por {len(pairs_to_process)} pares elegíveis...")

            # Obter o delay configurado para a API de team_radio (usa constante da classe)
            api_delay = self.API_DELAY_SECONDS 

            # Inicia uma transação de banco de dados
            with transaction.atomic(): # Usa a transação atômica do Django
                for i, (meeting_key, session_key) in enumerate(pairs_to_process):
                    # Mostra progresso para cada par processado
                    self.stdout.write(f"Processando par {i+1}/{len(pairs_to_process)}: meeting_key={meeting_key}, session_key={session_key}...")
                    pairs_processed_count += 1

                    # 2. Buscar dados de teamradio para o par atual da API
                    tr_data_from_api = self.fetch_teamradio_data(meeting_key=meeting_key, session_key=session_key, use_token=use_api_token_flag) # Passa a flag

                    if isinstance(tr_data_from_api, dict) and "error_status" in tr_data_from_api:
                        self.stdout.write(self.style.ERROR(f"  Erro na API para Sess {session_key}, Mtg {meeting_key}: {tr_data_from_api['error_message']}. Pulando esta sessão."))
                        self.add_warning(f"Erro API Sess {session_key}, Mtg {meeting_key}: {tr_data_from_api['error_message']}") # Adiciona ao detalhe
                        continue 

                    if not tr_data_from_api:
                        self.stdout.write(self.style.WARNING(f"  Aviso: Nenhuma registro de teamradio encontrado na API para Sess {session_key}, Mtg {meeting_key}."))
                        self.add_warning(f"Aviso: API vazia para Sess {session_key}, Mtg {meeting_key}.")
                        continue

                    self.stdout.write(f"  Encontrados {len(tr_data_from_api)} registros para Sess {session_key}. Filtrando por driver e construindo...")

                    # 3. Inserir cada registro no DB
                    for tr_entry in tr_data_from_api:
                        try: # Este try-except interno é para lidar com erros de UM registro e continuar os outros
                            inserted = self.insert_teamradio_entry(tr_entry)
                            if inserted is True:
                                tr_entries_inserted_db += 1
                            elif inserted is False:
                                tr_entries_skipped_db += 1
                        except Exception as tr_insert_e:
                            self.stdout.write(self.style.ERROR(f"Erro ao processar/inserir UM REGISTRO de teamradio para meeting_key={meeting_key}, session_key={session_key}: {tr_insert_e}. Pulando para o próximo registro."))
                            self.add_warning(f"Erro inserir TR (Mtg {meeting_key}, Sess {session_key}): {tr_insert_e}") # Adiciona ao detalhe

                    # Adiciona um delay APÓS cada chamada de API (loop do par meeting_key/session_key)
                    if api_delay > 0:
                        time.sleep(api_delay)


            self.stdout.write(self.style.SUCCESS("Importação de Team Radio concluída com sucesso para todos os pares elegíveis!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Team Radio (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Team Radio (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Pares (meeting_key, session_key) processados: {pairs_processed_count}"))
            self.stdout.write(self.style.SUCCESS(f"Registros inseridos no DB: {tr_entries_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB): {tr_entries_skipped_db}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de team radio finalizada!"))