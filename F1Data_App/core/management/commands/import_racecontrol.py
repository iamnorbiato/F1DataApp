# G:\Learning\F1Data\F1Data_App\core\management\commands\import_racecontrol.py
import requests
import json
from datetime import datetime, timezone # Importar timezone também, se usado para offset
import os
import time # Para o sleep da API

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, OperationalError, IntegrityError, transaction
from django.conf import settings

# Importa os modelos RaceControl
from core.models import RaceControl, Meetings, Sessions # Incluí Meetings e Sessions para get_meeting_keys_to_process se for usado
from dotenv import load_dotenv # <-- Removido, pois update_api_token_if_needed já o faz
from update_token import update_api_token_if_needed # <--- ADICIONADO

# --- CORREÇÃO AQUI: Usa settings.BASE_DIR para o caminho do env.cfg ---
ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')
# -----------------------------------------------------------------------

class Command(BaseCommand):
    help = 'Importa dados de controle de corrida (race_control) da API OpenF1 e os insere na tabela racecontrol do PostgreSQL usando ORM.'

    API_URL = "https://api.openf1.org/v1/race_control" # URL da API para race_control

    API_DELAY_SECONDS = 0.2 # <--- ADICIONADO: Delay de 0.2 segundos entre as chamadas da API (ajuste conforme necessário)

    def get_last_processed_meeting_key_from_racecontrol(self):
        self.stdout.write(self.style.MIGRATE_HEADING("Verificando o último meeting_key processado na tabela 'racecontrol'..."))
        last_key = 0
        try:
            last_rc_entry = RaceControl.objects.order_by('-meeting_key').first()
            if last_rc_entry:
                self.stdout.write(f"Último meeting_key encontrado na tabela 'racecontrol': {last_rc_entry.meeting_key}")
                return last_rc_entry.meeting_key
            self.stdout.write("Nenhum meeting_key encontrado no DB. Começando do zero.")
            return 0
        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados ao buscar o último meeting_key: {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado ao buscar o último meeting_key: {e}")

    def fetch_racecontrol_data(self, min_meeting_key=0, use_token=True): # <--- ADICIONADO: use_token
        """
        Busca todos os dados de controle de corrida da API OpenF1 com meeting_key > min_meeting_key.
        min_meeting_key: O valor mínimo do meeting_key a ser buscado na API.
        use_token: Se True, usa o token de autorização. Se False, não.
        """
        url = self.API_URL # Começa com a URL base

        if min_meeting_key > 0:
            url = f"{self.API_URL}?meeting_key>{min_meeting_key}"

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

        self.stdout.write(self.style.MIGRATE_HEADING(f"Buscando dados de controle de corrida da API: {url}"))
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise CommandError(f"Erro ao buscar dados de controle de corrida da API ({url}): {e}")

    def insert_racecontrol_entry(self, rc_data):
        """
        Insere um único registro de controle de corrida no banco de dados usando ORM.
        Usa ON CONFLICT (meeting_key, session_key, session_date) DO NOTHING para evitar duplicatas.
        Retorna True se inserido, False se ignorado/já existe.
        """
        try:
            if not isinstance(rc_data, dict):
                raise ValueError(f"Dados de controle de corrida inesperados: esperado um dicionário, mas recebeu {type(rc_data)}: {rc_data}")

            # Mapeamento e tratamento de dados da API para as colunas do DB
            meeting_key = rc_data.get('meeting_key')
            session_key = rc_data.get('session_key')
            session_date_str = rc_data.get('date') # O JSON usa 'date'
            driver_number = rc_data.get('driver_number')
            lap_number = rc_data.get('lap_number')
            category = rc_data.get('category')
            flag = rc_data.get('flag')
            scope = rc_data.get('scope')
            sector = rc_data.get('sector')
            message = rc_data.get('message')

            # Validação crítica para campos NOT NULL na PK
            if any(val is None for val in [meeting_key, session_key, session_date_str]):
                missing_fields = [k for k,v in {'meeting_key': meeting_key, 'session_key': session_key, 'date': session_date_str}.items() if v is None]
                raise ValueError(f"Dados de controle de corrida incompletos para PK: faltam campos NOT NULL {missing_fields}. Dados API: {rc_data}")

            session_date_obj = None
            if session_date_str:
                try:
                    session_date_obj = datetime.fromisoformat(session_date_str.replace('Z', '+00:00'))
                except ValueError:
                    raise ValueError(f"Formato de data inválido '{session_date_str}' para racecontrol entry (Meeting {meeting_key}, Session {session_key}).")

            # Usamos create e IntegrityError para a lógica ON CONFLICT DO NOTHING
            RaceControl.objects.create(
                meeting_key=meeting_key,
                session_key=session_key,
                session_date=session_date_obj, # Mapeia 'date' da API para 'session_date' do DB
                driver_number=driver_number,
                lap_number=lap_number,
                category=category,
                flag=flag,
                scope=scope,
                sector=sector,
                message=message
            )
            return True # Inserido com sucesso
        except IntegrityError:
            # Captura erro de chave primária duplicada (ON CONFLICT (PK) DO NOTHING)
            return False # Já existia, ignorado
        except Exception as e:
            # Loga o erro, mas re-levanta para ser tratado no handle()
            data_debug = f"Mtg {rc_data.get('meeting_key', 'N/A')}, Sess {rc_data.get('session_key', 'N/A')}, Date {rc_data.get('date', 'N/A')}"
            self.stdout.write(self.style.ERROR(f"Erro FATAL ao inserir/processar registro de controle de corrida ({data_debug}): {e} - Dados API: {rc_data}"))
            raise # Re-levanta o erro para o handle() capturá-lo e fazer rollback

    def handle(self, *args, **options):
        load_dotenv() # Carrega as variáveis do .env

        # >>>>> ADICIONADO: Lógica para usar/não usar o token <<<<<
        use_api_token_flag = os.getenv('USE_API_TOKEN', 'True').lower() == 'true' # Lê a flag do .env

        if use_api_token_flag:
            try:
                update_api_token_if_needed() # Verifica/renova o token
            except Exception as e:
                raise CommandError(f"Falha ao verificar/atualizar o token da API: {e}. Não é possível prosseguir com a importação.")
        else:
            self.stdout.write(self.style.NOTICE("Uso do token desativado (USE_API_TOKEN=False no .env). Buscando dados históricos."))

        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação de Controle de Corrida (ORM)..."))

        rc_entries_found_api = 0
        rc_entries_inserted_db = 0
        rc_entries_skipped_db = 0

        try: # Este try encapsula toda a lógica principal do handle
            # 1. Obter o maior meeting_key já processado na tabela 'racecontrol'
            last_meeting_key_in_rc_table = self.get_last_processed_meeting_key_from_racecontrol()

            # 2. Buscar TODOS os dados de controle de corrida da API com meeting_key > last_meeting_key_in_rc_table
            all_rc_entries_from_api = self.fetch_racecontrol_data(min_meeting_key=last_meeting_key_in_rc_table, use_token=use_api_token_flag) # Passa a flag
            rc_entries_found_api = len(all_rc_entries_from_api)

            rc_entries_to_process = all_rc_entries_from_api # Agora processamos tudo o que a API entregar

            self.stdout.write(self.style.SUCCESS(f"Total de {rc_entries_found_api} entradas de controle de corrida encontradas na API (com meeting_key > {last_meeting_key_in_rc_table})."))
            self.stdout.write(self.style.SUCCESS(f"Total de {len(rc_entries_to_process)} entradas de controle de corrida a serem processadas para inserção."))

            if not rc_entries_to_process:
                self.stdout.write(self.style.NOTICE("Nenhum novo registro de controle de corrida encontrado para importar. Encerrando."))
                return # Sai do handle se não houver registros para processar

            # Obter o delay configurado para a API de racecontrol
            api_delay = self.API_DELAY_SECONDS 

            # Inicia uma transação de banco de dados
            with transaction.atomic(): # Usa a transação atômica do Django
                for i, rc_data in enumerate(rc_entries_to_process):
                    # Opcional: Mostra um progresso básico a cada 10 registros ou no final do lote
                    if (i + 1) % 10 == 0 or (i + 1) == len(rc_entries_to_process):
                        meeting_key_debug = rc_data.get('meeting_key', 'N/A')
                        session_key_debug = rc_data.get('session_key', 'N/A')
                        self.stdout.write(f"Processando registro {i+1}/{len(rc_entries_to_process)} (Mtg {meeting_key_debug}, Sess {session_key_debug})...")

                    try: # Este try-except interno é para lidar com erros de UM registro e continuar os outros
                        inserted = self.insert_racecontrol_entry(rc_data)
                        if inserted is True:
                            rc_entries_inserted_db += 1
                        elif inserted is False:
                            rc_entries_skipped_db += 1
                    except Exception as rc_insert_e:
                        self.stdout.write(self.style.ERROR(f"Erro ao processar/inserir UM REGISTRO de controle de corrida: {rc_insert_e}. Pulando para o próximo registro."))

                    # Adiciona um delay APÓS cada chamada de API (loop do item, se houver muitas chamadas)
                    if self.API_DELAY_SECONDS > 0:
                        time.sleep(self.API_DELAY_SECONDS)


            self.stdout.write(self.style.SUCCESS("Importação de Controle de Corrida concluída com sucesso para todos os registros elegíveis!"))

        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados durante a importação (ORM): {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado durante a importação de Controle de Corrida (ORM): {e}")
        finally:
            # Sumário final
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Controle de Corrida (ORM) ---"))
            self.stdout.write(self.style.SUCCESS(f"Registros encontrados na API (total): {rc_entries_found_api}"))
            self.stdout.write(self.style.SUCCESS(f"Registros novos a serem inseridos: {len(rc_entries_to_process)}"))
            self.stdout.write(self.style.SUCCESS(f"Registros inseridos no DB: {rc_entries_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Registros ignorados (já existiam no DB): {rc_entries_skipped_db}"))
            self.stdout.write(self.style.MIGRATE_HEADING("---------------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de controle de corrida finalizada!"))