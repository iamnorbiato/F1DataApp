# G:\Learning\F1Data\F1Data_App\core\management\commands\import_starting_grid.py
import requests
import json
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings 

# Importa os modelos necessários
from core.models import StartingGrid # Apenas o modelo da tabela alvo
from dotenv import load_dotenv
from update_token import update_api_token_if_needed
import pytz # Para manipulação de fusos horários (não usado diretamente neste, mas boa prática)

ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg') 

class Command(BaseCommand):
    help = 'Importa dados de starting grid da API OpenF1 de forma eficiente e os insere/atualiza na tabela startinggrid do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/starting_grid" # URL da API para starting_grid
    CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json')

    API_DELAY_SECONDS = 0.2 # Delay APÓS a única chamada da API (se a API retornar tudo)
    API_MAX_RETRIES = 3 
    API_RETRY_DELAY_SECONDS = 5 
    BULK_SIZE = 5000 # Tamanho do lote para bulk_create (se aplicável para bulk_create)

    warnings_count = 0
    all_warnings_details = [] # Lista para armazenar as mensagens detalhadas de aviso

    def add_warning(self, message):
        """Adiciona um aviso ao contador e à lista de detalhes."""
        self.warnings_count += 1
        self.all_warnings_details.append(message)
        self.stdout.write(self.style.WARNING(message))

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

    def fetch_starting_grid_data(self, use_token=True): # <--- ADAPTADO: Não recebe min_meeting_key
        """
        Busca todos os dados de starting grid da API OpenF1 (sem filtros de meeting_key, esperando tudo).
        """
        url = self.API_URL # API base URL, sem parâmetros
        
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

    def handle(self, *args, **options):
        load_dotenv(dotenv_path=ENV_FILE_PATH)
        self.warnings_count = 0
        self.all_warnings_details = []

        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true'

        if use_api_token_flag:
            try:
                update_api_token_if_needed()
            except Exception as e:
                raise CommandError(f"Falha ao verificar/atualizar o token da API: {e}. Não é possível prosseguir com a importação.")
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no env.cfg). Buscando dados históricos."))

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação de Starting Grid (ORM - Otimizado API)..."))

        sg_inserted_db = 0
        sg_updated_db = 0 # <--- NOVO: Contador para registros atualizados
        sg_skipped_db = 0 # Para erros ou outros motivos que não seja update/insert
        
        try: # Este try encapsula toda a lógica principal do handle
            # 1. Buscar TODOS os dados de starting grid da API (sem filtros)
            all_sg_from_api = self.fetch_starting_grid_data(use_token=use_api_token_flag)
            
            if isinstance(all_sg_from_api, dict) and "error_status" in all_sg_from_api:
                raise CommandError(f"Erro fatal ao buscar dados da API: {all_sg_from_api['error_message']}. URL: {all_sg_from_api['error_url']}")

            if not all_sg_from_api:
                self.stdout.write(self.style.NOTICE("Nenhuma entrada de starting grid encontrada na API para importar. Encerrando."))
                return

            self.stdout.write(self.style.SUCCESS(f"Total de {len(all_sg_from_api)} entradas de starting grid encontradas na API."))
            self.stdout.write(f"Iniciando processamento e inserção/atualização de {len(all_sg_from_api)} registros...")

            api_delay = self.API_DELAY_SECONDS 
            
            sg_objects_to_process = []
            for i, sg_entry_dict in enumerate(all_sg_from_api):
                # Construir instância do modelo
                try:
                    # Mapeamento dos campos da API para as colunas do DB
                    meeting_key = sg_entry_dict.get('meeting_key')
                    session_key = sg_entry_dict.get('session_key')
                    position = sg_entry_dict.get('position')
                    driver_number = sg_entry_dict.get('driver_number')
                    lap_duration = sg_entry_dict.get('lap_duration')

                    # Validação crítica para campos NOT NULL na PK
                    if any(val is None for val in [meeting_key, session_key, driver_number]):
                        missing_fields = [k for k,v in {'meeting_key': meeting_key, 'session_key': session_key, 'driver_number': driver_number}.items() if v is None]
                        raise ValueError(f"Dados incompletos para StartingGrid: faltam {missing_fields}. Dados: {sg_entry_dict}")

                    # Tratamento de lap_duration (numeric(8,3))
                    lap_duration_parsed = None
                    if lap_duration is not None:
                        try:
                            lap_duration_parsed = float(str(lap_duration).replace(',', '.').strip())
                        except ValueError:
                            self.add_warning(f"Aviso: Valor de 'lap_duration' '{lap_duration}' não é numérico. Ignorando para Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}.")
                            lap_duration_parsed = None
                    
                    if position is None:
                        position = 0 # Default para 0 se o API enviar NULL e DDL for NOT NULL
                        self.add_warning(f"Aviso: 'position' é null para Mtg {meeting_key}, Sess {session_key}, Driver {driver_number}. Usando 0.")

                    # Critérios para buscar o registro (PK)
                    lookup_criteria = {
                        'meeting_key': meeting_key,
                        'session_key': session_key,
                        'driver_number': driver_number
                    }

                    # Valores a serem definidos ou atualizados (não-PK)
                    defaults_for_update = {
                        'position': position,
                        'lap_duration': lap_duration_parsed
                    }
                    
                    sg_objects_to_process.append(
                        StartingGrid(
                            meeting_key=meeting_key,
                            session_key=session_key,
                            driver_number=driver_number,
                            position=position,
                            lap_duration=lap_duration_parsed
                        )
                    )

                except Exception as build_e:
                    self.add_warning(f"Erro ao construir instância de starting grid: {build_e}. Dados: {sg_entry_dict}. Pulando este registro.")

            if sg_objects_to_process:
                with transaction.atomic():                    
                    for sg_obj in sg_objects_to_process:
                        try:
                            # update_or_create é a forma correta de fazer UPSERT com ORM item por item.
                            # O lookup é pela PK.
                            lookup = {
                                'meeting_key': sg_obj.meeting_key,
                                'session_key': sg_obj.session_key,
                                'driver_number': sg_obj.driver_number
                            }
                            # Os defaults são os campos que serão atualizados se a PK for encontrada
                            defaults = {
                                'position': sg_obj.position,
                                'lap_duration': sg_obj.lap_duration
                            }
                            
                            obj, created = StartingGrid.objects.update_or_create(**lookup, defaults=defaults)
                            if created:
                                sg_inserted_db += 1
                            else:
                                sg_updated_db += 1 # Conta como atualizado
                        except Exception as item_e:
                            self.add_warning(f"Erro ao inserir/atualizar um registro (Mtg {sg_obj.meeting_key}, Sess {sg_obj.session_key}, Driver {sg_obj.driver_number}): {item_e}. Pulando.")
                            sg_skipped_db += 1 # Contabiliza falhas como skipped/ignorados


            if api_delay > 0:
                time.sleep(api_delay)


            self.stdout.write(self.style.SUCCESS("Importação de Starting Grid concluída com sucesso para todas as entradas elegíveis!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Starting Grid (ORM): {e}")
        finally:
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Starting Grid (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Entradas encontradas na API (total): {sg_found_api if 'sg_found_api' in locals() else 0}"))
            self.stdout.write(self.style.SUCCESS(f"Entradas a serem processadas: {len(sg_to_process) if 'sg_to_process' in locals() else 0}"))
            self.stdout.write(self.style.SUCCESS(f"Registros inseridos no DB: {sg_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros atualizados no DB: {sg_updated_db}")) # Novo sumário
            self.stdout.write(self.style.NOTICE(f"Registros ignorados/falhos no DB: {sg_skipped_db}")) # Novo sumário
            self.stdout.write(self.style.WARNING(f"Total de Avisos/Alertas durante a execução: {self.warnings_count}"))
            if self.all_warnings_details:
                self.stdout.write(self.style.WARNING("\nDetalhes dos Avisos/Alertas:"))
                for warn_msg in self.all_warnings_details:
                    self.stdout.write(self.style.WARNING(f" - {warn_msg}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de starting grid finalizada!"))