# G:\Learning\F1Data\F1Data_App\core\management\commands\import_meetings.py
import requests  # Necessário para fazer requisições HTTP
import json      # Para trabalhar com JSON
from datetime import datetime  # Para parsear datas
import os        # Para variáveis de ambiente e caminhos de arquivo

from django.core.management.base import BaseCommand, CommandError
from django.db import connection  # Usado para interagir com o DB do Django
from psycopg2 import OperationalError  # Para capturar erros específicos do PostgreSQL

class Command(BaseCommand):
    help = 'Importa dados de meetings da API OpenF1 e os insere/atualiza na tabela meetings do PostgreSQL.'

    API_URL = "https://api.openf1.org/v1/meetings"  # URL base da API
    CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'import_config.json')  # Caminho para o arquivo de config

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

            if section: # Se uma seção for especificada (ex: "meetings_import_settings")
                section_data = config.get(section, default if key is None else {}) # Pega a seção ou um default vazio/completo
                if key is None: # Se nenhuma chave específica for pedida, retorna a seção inteira
                    return section_data
                else: # Se uma chave for pedida, pega o valor dentro da seção
                    return section_data.get(key, default)
            else: # Se não houver seção (configuração de nível raiz)
                if key is None: # Se nenhuma chave específica e nenhuma seção for pedida, retorna a config inteira
                    return config
                else: # Se uma chave for pedida na raiz
                    return config.get(key, default)

        except json.JSONDecodeError as e:
            raise CommandError(f"Erro ao ler/parsear o arquivo de configuração JSON '{self.CONFIG_FILE}': {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado ao acessar o arquivo de configuração: {e}")

    def set_config_value(self, key, value, section=None):
        """
        Escreve um valor de configuração de volta para o arquivo JSON.
        Atualiza apenas a chave fornecida dentro da seção especificada.
        """
        config = {}
        if os.path.exists(self.CONFIG_FILE):
            try:
                with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except json.JSONDecodeError as e:
                raise CommandError(f"Erro ao ler/parsear o arquivo de configuração JSON '{self.CONFIG_FILE}' antes de escrever: {e}")
            except Exception as e:
                raise CommandError(f"Erro inesperado ao ler o arquivo de configuração antes de escrever: {e}")

        if section:
            if section not in config:
                config[section] = {}
            config[section][key] = value
        else:
            config[key] = value
        
        try:
            with open(self.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4) # indent=4 para formatar o JSON de forma legível
            self.stdout.write(self.style.SUCCESS(f"Configuração '{key}' atualizada para '{value}' no arquivo."))
        except Exception as e:
            raise CommandError(f"Erro ao escrever no arquivo de configuração '{self.CONFIG_FILE}': {e}")

    def get_last_meeting_key(self):
        """
        Obtém o maior meeting_key existente na tabela 'meetings' do banco de dados local.
        Retorna 0 se a tabela estiver vazia.
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Verificando o último meeting_key no banco de dados local..."))
        last_key = 0
        try:
            with connection.cursor() as cursor:
                # COALESCE(MAX(meeting_key), 0) garante que retornará 0 se a tabela estiver vazia
                cursor.execute("SELECT COALESCE(MAX(meeting_key), 0) FROM meetings;")
                last_key = cursor.fetchone()[0]
            self.stdout.write(self.style.SUCCESS(f"Último meeting_key no DB: {last_key}"))
        except OperationalError as e:
            raise CommandError(f"Erro operacional de banco de dados ao buscar último meeting_key: {e}")
        except Exception as e:
            raise CommandError(f"Erro inesperado ao buscar último meeting_key: {e}")
        return last_key

    def fetch_meetings_data(self, min_meeting_key=0):
        """
        Busca os dados de meetings da API OpenF1, filtrando por meeting_key > min_meeting_key.
        min_meeting_key: O valor mínimo da meeting_key a ser buscado na API.
        """
        url = self.API_URL  # Começa com a URL base

        # Se min_meeting_key for maior que 0, adiciona o filtro meeting_key>
        if min_meeting_key > 0:
            url = f"{self.API_URL}?meeting_key>{min_meeting_key}"

        self.stdout.write(self.style.MIGRATE_HEADING(f"Buscando dados da API: {url}"))
        try:
            response = requests.get(url)
            response.raise_for_status()  # Levanta um HTTPError para respostas de erro (4xx ou 5xx)
            return response.json()
        except requests.exceptions.RequestException as e:
            raise CommandError(f"Erro ao buscar dados da API: {e}")

    def insert_meeting(self, cursor, meeting_data):
        """
        Insere um único registro de meeting no banco de dados.
        Usa ON CONFLICT (meeting_key) DO NOTHING para evitar duplicatas.
        Retorna True se inserido, False se ignorado/já existe.
        """
        meeting_key = meeting_data.get('meeting_key')
        circuit_key = meeting_data.get('circuit_key')
        circuit_short_name = meeting_data.get('circuit_short_name')
        meeting_code = meeting_data.get('meeting_code')
        location = meeting_data.get('location')
        country_key = meeting_data.get('country_key')
        country_code = meeting_data.get('country_code')
        country_name = meeting_data.get('country_name')
        meeting_name = meeting_data.get('meeting_name')
        meeting_official_name = meeting_data.get('meeting_official_name')
        gmt_offset = meeting_data.get('gmt_offset')
        date_start_str = meeting_data.get('date_start')
        year = meeting_data.get('year')

        date_start = None
        if date_start_str:
            try:
                date_start = datetime.fromisoformat(date_start_str.replace('Z', '+00:00'))
            except ValueError:
                pass # Não printa aviso de parse de data inválida para cada linha

        sql = """
        INSERT INTO meetings (
            meeting_key, circuit_key, circuit_short_name, meeting_code, location,
            country_key, country_code, country_name, meeting_name, meeting_official_name,
            gmt_offset, date_start, year
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        ) ON CONFLICT (meeting_key) DO NOTHING;
        """
        values = (
            meeting_key, circuit_key, circuit_short_name, meeting_code, location,
            country_key, country_code, country_name, meeting_name, meeting_official_name,
            gmt_offset, date_start, year
        )

        try:
            cursor.execute(sql, values)
            return cursor.rowcount > 0 # Retorna True se inseriu (rowcount = 1), False se ignorou (rowcount = 0)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Erro FATAL ao inserir meeting {meeting_key}: {e} - Dados: {values}"))
            raise

    def handle(self, *args, **options):
        """
        Método principal que é executado quando o comando é chamado.
        Orquestra a busca de dados da API e a inserção no banco de dados,
        fornecendo um sumário final.
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Iniciando a importação de Meetings..."))
        
        meetings_found_api = 0
        meetings_inserted_db = 0
        meetings_skipped_db = 0
        
        # Obter configurações de importação específicas para meetings
        # Não usamos 'batch_size' ou 'last_processed_meeting_key' do JSON aqui.
        import_settings = self.get_config_value(key=None, section='meetings_import_settings', default={}) # Obtém a seção completa
        active_import = import_settings.get('active', True)

        if not active_import:
            self.stdout.write(self.style.NOTICE("Importação de Meetings desativada na configuração. Encerrando."))
            return
        
        try:
            # 1. Obter o maior meeting_key no DB (para filtrar na API)
            max_meeting_key_in_db = self.get_last_meeting_key()

            # 2. Buscar TODOS os dados novos da API (filtrando por meeting_key > max_meeting_key_in_db)
            all_new_meetings_from_api = self.fetch_meetings_data(min_meeting_key=max_meeting_key_in_db)
            meetings_found_api = len(all_new_meetings_from_api)
            
            # Filtramos aqui apenas para garantir que só processamos meetings realmente novos
            # (se a API por algum motivo retornar algo <= max_meeting_key_in_db)
            meetings_to_process = [
                m for m in all_new_meetings_from_api if m.get('meeting_key', 0) > max_meeting_key_in_db
            ]

            self.stdout.write(self.style.SUCCESS(f"Total de {meetings_found_api} meetings encontrados na API."))
            self.stdout.write(self.style.SUCCESS(f"Total de {len(meetings_to_process)} novos meetings a serem inseridos no DB."))

            if not meetings_to_process:
                self.stdout.write(self.style.NOTICE("Nenhum novo meeting encontrado para importar. Encerrando."))
                return # Sai do handle se não houver meetings para processar

            # Inicia uma transação de banco de dados
            with connection.cursor() as cursor:
                for i, meeting in enumerate(meetings_to_process):
                    # Opcional: Mostra um progresso básico (ex: a cada 100 meetings ou no final)
                    if (i + 1) % 10 == 0 or (i + 1) == len(meetings_to_process):
                        self.stdout.write(f"Processando meeting {i+1}/{len(meetings_to_process)}...")

                    inserted = self.insert_meeting(cursor, meeting)
                    if inserted is True:
                        meetings_inserted_db += 1
                    elif inserted is False:
                        meetings_skipped_db += 1 # Conta os que já existiam, mas que são novos da API

            # Confirma todas as inserções/ignorações se não houver erros
            connection.commit()

        except OperationalError as e:
            connection.rollback()  # Desfaz todas as operações em caso de erro no DB
            raise CommandError(f"Erro operacional de banco de dados durante a importação: {e}")
        except Exception as e:
            connection.rollback()  # Desfaz todas as operações em caso de erro
            raise CommandError(f"Erro inesperado durante a importação de meetings: {e}")
        finally:
            # Sumário final
            self.stdout.write(self.style.MIGRATE_HEADING("\n--- Resumo da Importação de Meetings ---"))
            self.stdout.write(self.style.SUCCESS(f"Meetings encontrados na API: {meetings_found_api}"))
            self.stdout.write(self.style.SUCCESS(f"Meetings novos a serem inseridos: {len(meetings_to_process)}"))
            self.stdout.write(self.style.SUCCESS(f"Meetings inseridos no DB: {meetings_inserted_db}"))
            self.stdout.write(self.style.NOTICE(f"Meetings ignorados (já existiam no DB): {meetings_skipped_db}"))
            self.stdout.write(self.style.MIGRATE_HEADING("-------------------------------------"))
            self.stdout.write(self.style.SUCCESS("Importação de meetings concluída!"))