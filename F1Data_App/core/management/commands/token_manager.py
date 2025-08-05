# G:\Learning\F1Data\F1Data_App\core\management\commands\token_manager.py
import requests
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv, set_key
import threading
import signal

# Importando settings do Django para obter BASE_DIR
from django.conf import settings 

API_URL_TOKEN = "https://api.openf1.org/token"
# A correção do caminho para o arquivo env.cfg
ENV_FILE_PATH = os.path.join(settings.BASE_DIR, 'env.cfg')

API_MAX_RETRIES = 3
API_RETRY_DELAY_SECONDS = 5

token_lock = threading.Lock()
cached_token = None
cached_token_expiration = None

command_instance_global = None

def signal_handler(signum, frame):
    """Handler para o sinal de interrupção (Ctrl+C)."""
    if command_instance_global:
        command_instance_global.stdout.write(command_instance_global.style.WARNING("\nExecução cancelada pelo usuário. Encerrando o processo..."))
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

def is_token_expired(expiration_str):
    if not expiration_str:
        return True
    
    try:
        expiration_dt = datetime.fromisoformat(expiration_str.replace('Z', '+00:00')) - timedelta(seconds=60)
        return expiration_dt <= datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return True

def get_api_token(command_instance):
    global cached_token, cached_token_expiration, command_instance_global
    
    command_instance_global = command_instance
    stdout = command_instance.stdout
    style = command_instance.style

    with token_lock:
        if cached_token and not is_token_expired(cached_token_expiration):
            return cached_token

        load_dotenv(dotenv_path=ENV_FILE_PATH)
        api_token = os.getenv('OPENF1_API_TOKEN')
        token_expiration_str = os.getenv('OPENF1_TOKEN_EXPIRATION')

        if api_token and not is_token_expired(token_expiration_str):
            cached_token = api_token
            cached_token_expiration = token_expiration_str
            return cached_token

        stdout.write(style.WARNING("Token expirado ou ausente. Tentando obter um novo token...\n"))
        
        auth_url = os.getenv('OPENF1_AUTH_URL')
        username = os.getenv('OPENF1_USERNAME')
        password = os.getenv('OPENF1_PASSWORD')

        if not auth_url or not username or not password:
            stdout.write(style.ERROR("Erro: OPENF1_AUTH_URL, OPENF1_USERNAME ou OPENF1_PASSWORD não configurados no env.cfg.\n"))
            return None
        
        payload = {
            "username": username,
            "password": password
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json"
        }

        for attempt in range(API_MAX_RETRIES):
            try:
                response = requests.post(auth_url, data=payload, headers=headers)
                response.raise_for_status()
                
                token_data = response.json()
                new_token = token_data.get('access_token')
                expires_in = int(token_data.get('expires_in'))

                if new_token and expires_in:
                    stdout.write(style.SUCCESS("Novo token obtido com sucesso!\n"))
                    
                    new_expiration_dt = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                    new_expiration_str = new_expiration_dt.isoformat()

                    set_key(dotenv_path=ENV_FILE_PATH, key_to_set='OPENF1_API_TOKEN', value_to_set=new_token)
                    set_key(dotenv_path=ENV_FILE_PATH, key_to_set='OPENF1_TOKEN_EXPIRATION', value_to_set=new_expiration_str)
                    stdout.write(style.SUCCESS("Arquivo env.cfg atualizado com o novo token e data de expiração.\n"))

                    cached_token = new_token
                    cached_token_expiration = new_expiration_str
                    
                    return cached_token
                else:
                    stdout.write(style.ERROR("Erro: A resposta da API não contém 'access_token' ou 'expires_in'.\n"))
            
            except requests.exceptions.RequestException as e:
                status_code = getattr(response, 'status_code', 'Unknown')
                if attempt < API_MAX_RETRIES - 1:
                    delay = API_RETRY_DELAY_SECONDS * (2 ** attempt)
                    stdout.write(style.WARNING(f"Aviso: Erro {status_code} da API de token. Tentativa {attempt + 1}/{API_MAX_RETRIES}. Retentando em {delay} segundos...\n"))
                    time.sleep(delay)
                else:
                    stdout.write(style.ERROR(f"Erro fatal: Falha ao obter novo token após {API_MAX_RETRIES} tentativas. Erro: {e}\n"))
                    break
    
    return None