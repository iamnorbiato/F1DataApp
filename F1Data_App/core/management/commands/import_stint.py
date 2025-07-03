# G:\Learning\F1Data\F1Data_App\core\management\commands\import_stint.py
import requests
import json
from datetime import datetime, timezone, timedelta
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings 

# Importa os modelos necessários
from core.models import Meetings, Stint # Precisamos de Meetings para obter todos os meeting_keys mestre
from dotenv import load_dotenv
from update_token import update_api_token_if_needed
import pytz # Para manipulação de fusos horários (caso necessário para formatação de data)

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg') 

class Command(BaseCommand):
    help = 'Importa dados de stints da API OpenF1 e os insere na tabela stint do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/stints" # URL da API para stints
    CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json')

    API_DELAY_SECONDS = 0.2
    API_MAX_RETRIES = 3 
    API_RETRY_DELAY_SECONDS = 5 
    BULK_SIZE = 5000 

    warnings_count = 0

    def get_config_value(self, key=None, default=None, section=None):
        config = {}
        if not os.path.exists(self.CONFIG_FILE):
            self.stdout.write(self.style.WARNING(f"Aviso: Arquivo de configuração '{self.CONFIG_FILE}' não encontrado. Usando valor padrão para '{key}'."))
            self.warnings_count += 1
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

    def get_all_meeting_keys_from_meetings_table(self): # <--- NOVO MÉTODO (adaptado de import_sessions)
        """
        Obtém todos os meeting_keys da tabela 'meetings' (a tabela mestre).
        Retorna uma lista ordenada de meeting_keys.
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Obtendo todos os meeting_keys da tabela 'meetings' (tabela mestre)..."))
        meeting_keys = []
        try:
            meeting_keys = list(Meetings.objects.all().order_by('meeting_key').values_list('meeting_key', flat=True))
            self.stdout.write(self.style.SUCCESS(f"Encontrados {len(meeting_keys)} meeting_keys na tabela 'meetings'."))
            return meeting_keys
        except Exception as e:
            raise CommandError(f"Erro ao buscar todos os meeting_keys da tabela 'meetings' via ORM: {e}")

    def fetch_stints_data(self, meeting_key, use_token=True): # <--- ADAPTADO: Agora para API por meeting_key EXATO
        """
        Busca dados de stints da API OpenF1 para um meeting_key específico.
        """
        if not meeting_key:
            raise CommandError("meeting_key deve ser fornecido para buscar dados de stints da API.")

        url = f"{self.API_URL}?meeting_key={meeting_key}" # <--- Filtro meeting_key EXATO

        headers = {"Accept": "application/json"}

        if use_token:
            api_token = os.getenv('OPENF1_API_TOKEN')
            if not api_token:
                self.warnings_count += 1
                raise CommandError("Token da API (OPENF1_API_TOKEN) não encontrado. Requisição será feita sem Authorization.")
            else:
                headers["Authorization"] = f"Bearer {api_token}"
        else:
            self.warnings_count += 1
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
                    self.stdout.write(self.style.WARNING(f"  Aviso: Erro {status_code} da API para URL: {url}. Tentativa {attempt + 1}/{self.API_MAX_RETRIES}. Retentando em {delay} segundos..."))
                    self.warnings_count += 1
                    time.sleep(delay)
                else:
                    return {"error_status": status_code,
                            "error_url": url,
                            "error_message": str(e)}
        return {"error_status": "Failed after retries", "error_url": url, "error_message": "Max retries exceeded."}

    def build_stint_instance(self, stint_data_dict): # <--- NOVO MÉTODO
        """
        Constrói uma instância de modelo Stint a partir de um dicionário de dados.
        """
        # Mapeamento e tratamento de dados da API para as colunas do DB
        meeting_key = stint_data_dict.get('meeting_key')
        session_key = stint_data_dict.get('session_key')
        stint_number = stint_data_dict.get('stint_number')
        driver_number = stint_data_dict.get('driver_number')
        
        lap_start = stint_data_dict.get('lap_start')
        lap_end = stint_data_dict.get('lap_end')
        compound = stint_data_dict.get('compound')
        tyre_age_at_start = stint_data_dict.get('tyre_age_at_start')

        # Validação crítica para campos NOT NULL na PK
        if any(val is None for val in [meeting_key, session_key, stint_number, driver_number]):
            missing_fields = [k for k,v in {'meeting_key': meeting_key, 'session_key': session_key, 'stint_number': stint_number, 'driver_number': driver_number}.items() if v is None]
            raise ValueError(f"Dados incompletos para Stint: faltam {missing_fields}. Dados: {stint_data_dict}")

        return Stint(
            meeting_key=meeting_key,
            session_key=session_key,
            stint_number=stint_number,
            driver_number=driver_number,
            lap_start=lap_start,
            lap_end=lap_end,
            compound=compound,
            tyre_age_at_start=tyre_age_at_start
        )

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)
        self.warnings_count = 0
        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'
        if use_api_token_flag:
            try:
                update_api_token_if_needed()
            except Exception as e:
                raise CommandError(f"Falha ao verificar/atualizar o token da API: {e}. Não é possível prosseguir com a importação.")
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no env.cfg). Buscando dados históricos."))

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação de Stints (ORM)..."))
        stints_inserted_db = 0
        stints_skipped_db = 0
        meeting_keys_processed_count = 0 

        try:
            # 1. Obter TODOS os meeting_keys da tabela 'meetings' (a tabela mestre)
            all_meeting_keys = self.get_all_meeting_keys_from_meetings_table()

            if not all_meeting_keys:
                self.stdout.write(self.style.NOTICE("Nenhum meeting_key encontrado na tabela 'meetings' para importar stints. Encerrando."))
                return

            self.stdout.write(self.style.SUCCESS(f"Total de {len(all_meeting_keys)} meeting_keys elegíveis para processamento."))

            api_delay = self.API_DELAY_SECONDS 

            # Loop principal pelos meeting_keys
            for i, meeting_key in enumerate(all_meeting_keys):
                self.stdout.write(f"Processando Meeting {meeting_key} para dados de stints ({i+1}/{len(all_meeting_keys)})...")
                meeting_keys_processed_count += 1

                current_stints_objects = [] # Coleta todos os objetos Stint válidos para este meeting_key

                try: # Este try encapsula a chamada de API e a construção dos objetos para UM MEETING
                    stints_data_from_api = self.fetch_stints_data(meeting_key=meeting_key, use_token=use_api_token_flag)

                    if isinstance(stints_data_from_api, dict) and "error_status" in stints_data_from_api:
                        self.stdout.write(self.style.ERROR(f"  Erro na API para Meeting {meeting_key}: {stints_data_from_api['error_message']}. Pulando este meeting."))
                        self.warnings_count += 1
                        continue

                    if not stints_data_from_api:
                        self.stdout.write(self.style.WARNING(f"  Aviso: Nenhuma entrada de stint encontrada na API para Meeting {meeting_key}."))
                        self.warnings_count += 1
                        continue

                    self.stdout.write(f"  Encontrados {len(stints_data_from_api)} registros para Meeting {meeting_key}. Construindo objetos e inserindo...")

                    for stint_entry_dict in stints_data_from_api:
                        try:
                            stint_instance = self.build_stint_instance(stint_entry_dict)
                            if stint_instance is not None:
                                current_stints_objects.append(stint_instance)
                        except Exception as build_e:
                            self.stdout.write(self.style.ERROR(f"Erro ao construir instância de stint para Meeting {meeting_key}: {build_e}. Pulando este registro."))
                            self.warnings_count += 1

                    if current_stints_objects:
                        with transaction.atomic():
                            created_instances = Stint.objects.bulk_create(current_stints_objects, batch_size=self.BULK_SIZE, ignore_conflicts=True)
                            stints_inserted_db += len(created_instances)
                            stints_skipped_db += (len(current_stints_objects) - len(created_instances))
                            self.stdout.write(self.style.SUCCESS(f"  {len(created_instances)} registros inseridos (total para este meeting)."))
                    else:
                        self.stdout.write(self.style.WARNING(f"  Aviso: Nenhuma entrada de stint válida encontrada para inserção para Meeting {meeting_key} (após construção de instância)."))
                        self.warnings_count += 1

                    if api_delay > 0:
                        time.sleep(api_delay)

                except Exception as meeting_overall_e:
                    self.stdout.write(self.style.ERROR(f"Erro ao processar Meeting {meeting_key}: {meeting_overall_e}. Este meeting não foi processado por completo. Pulando para o próximo meeting."))
                    self.warnings_count += 1

            self.stdout.write(self.style.SUCCESS("Importação de Stints concluída com sucesso para todos os meetings elegíveis!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Stints (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Stints (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Meetings processados: {meeting_keys_processed_count}"))
            self.stdout.write(self.style.SUCCESS(f"Registros inseridos no DB: {stints_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB): {stints_skipped_db}"))
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de stints finalizada!"))
