# G:\Learning\F1Data\F1Data_App\update_token.py
import os
import sys
import requests
import json
import time
from datetime import datetime, timezone
from dotenv import load_dotenv, set_key

# --- DEFINIÇÃO DO CAMINHO DO ARQUIVO DE CONFIGURAÇÃO ---
# Calcula o caminho absoluto para 'env.cfg' que está no mesmo diretório do script.
ENV_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'env.cfg')
# ----------------------------------------------------

def get_env_variable(key):
    """Obtém uma variável de ambiente, levantando erro se não encontrada."""
    value = os.getenv(key)
    if not value:
        print(f"ERRO DE CONFIGURAÇÃO: Variável de ambiente '{key}' não configurada no {os.path.basename(ENV_FILE_PATH)}", file=sys.stderr)
        sys.exit(1)
    return value

def is_token_expired(expiration_timestamp_str):
    """
    Verifica se o token expirou.
    expiration_timestamp_str: Timestamp Unix em segundos (string).
    Retorna True se expirado ou se o timestamp for inválido/ausente, False caso contrário.
    """
    if not expiration_timestamp_str:
        print("AVISO: OPENF1_TOKEN_EXPIRATION está vazio. Token considerado expirado.")
        return True

    try:
        expiration_timestamp = int(expiration_timestamp_str)
        current_timestamp = int(datetime.now(timezone.utc).timestamp())

        # Adiciona uma margem de segurança de 60 segundos (1 minuto)
        if current_timestamp >= expiration_timestamp - 60:
            print(f"ALERTA: Token expirado ou próximo de expirar. Expira em: {datetime.fromtimestamp(expiration_timestamp, timezone.utc)} UTC. Agora: {datetime.fromtimestamp(current_timestamp, timezone.utc)} UTC.")
            return True
        else:
            print(f"INFO: Token ainda válido. Expira em: {datetime.fromtimestamp(expiration_timestamp, timezone.utc)} UTC. Agora: {datetime.fromtimestamp(current_timestamp, timezone.utc)} UTC.")
            return False
    except ValueError:
        print(f"ERRO: OPENF1_TOKEN_EXPIRATION '{expiration_timestamp_str}' não é um número válido. Token considerado expirado.", file=sys.stderr)
        return True

def get_new_token(auth_url, username, password):
    """
    Obtém um novo token de acesso da API de autenticação OpenF1.
    CORRIGIDO: Agora usa 'application/x-www-form-urlencoded' e 'data=payload'.
    """
    print(f"Tentando obter um novo token de: {auth_url}")
    try:
        payload = {
            "username": username,
            "password": password
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json"
        }

        response = requests.post(auth_url, data=payload, headers=headers)
        response.raise_for_status()

        token_data = response.json()
        access_token = token_data.get('access_token')
        expires_in = int(token_data.get('expires_in')) # <--- MUDANÇA AQUI: Converte para int()

        if not access_token or expires_in is None:
            raise ValueError(f"Resposta da API de autenticação incompleta: {token_data}. Esperado 'access_token' e 'expires_in'.")

        # Calcula o timestamp de expiração
        expiration_timestamp = int(datetime.now(timezone.utc).timestamp()) + expires_in

        print("Novo token obtido com sucesso!")
        return access_token, expiration_timestamp

    except requests.exceptions.RequestException as e:
        raise Exception(f"Erro ao conectar ou obter token da API de autenticação: {e}")
    except json.JSONDecodeError:
        raise Exception("Erro ao decodificar a resposta JSON da API de autenticação.")
    except ValueError as e:
        raise Exception(f"Erro nos dados da resposta da API de autenticação: {e}")
    except Exception as e:
        raise Exception(f"Erro inesperado ao obter novo token: {e}")

def update_env_file(new_token, expiration_timestamp):
    """
    Atualiza as variáveis OPENF1_API_TOKEN e OPENF1_TOKEN_EXPIRATION no arquivo env.cfg.
    """
    print(f"Atualizando {os.path.basename(ENV_FILE_PATH)} em: {ENV_FILE_PATH}")
    try:
        set_key(ENV_FILE_PATH, 'OPENF1_API_TOKEN', new_token)
        set_key(ENV_FILE_PATH, 'OPENF1_TOKEN_EXPIRATION', str(expiration_timestamp))
        print(f"Arquivo {os.path.basename(ENV_FILE_PATH)} atualizado com o novo token e data de expiração.")
    except Exception as e:
        raise Exception(f"Erro ao atualizar o arquivo {os.path.basename(ENV_FILE_PATH)}: {e}")

def update_api_token_if_needed():
    """
    Função principal para ser chamada por outros módulos.
    Verifica se o token expirou e o renova se necessário.
    """
    # Carrega as variáveis do env.cfg usando o caminho definido
    load_dotenv(dotenv_path=ENV_FILE_PATH)

    current_token = os.getenv('OPENF1_API_TOKEN')
    token_expiration_str = os.getenv('OPENF1_TOKEN_EXPIRATION')

    if not current_token or is_token_expired(token_expiration_str):
        print("Token atual expirado ou ausente. Tentando obter um novo token...")

        # Obtém as credenciais para a autenticação do env.cfg
        auth_url = get_env_variable('OPENF1_AUTH_URL')
        username = get_env_variable('OPENF1_USERNAME')
        password = get_env_variable('OPENF1_PASSWORD')

        new_token, new_expiration_timestamp = get_new_token(auth_url, username, password)
        update_env_file(new_token, new_expiration_timestamp)
        print("Processo de atualização de token concluído com sucesso!")
    else:
        print("INFO: Token da API OpenF1 verificado e válido. Nenhuma ação necessária.")

if __name__ == "__main__":
    # Este bloco só é executado se você rodar 'python update_token.py' diretamente
    try:
        print("Executando update_token.py diretamente para verificação/atualização de status do token.")
        update_api_token_if_needed()
    except ValueError as e:
        print(f"ERRO DE CONFIGURAÇÃO (ao rodar update_token.py diretamente): {e}", file=sys.stderr)
    except Exception as e:
        print(f"ERRO INESPERADO (ao rodar update_token.py diretamente): {e}", file=sys.stderr)
    sys.exit(1)