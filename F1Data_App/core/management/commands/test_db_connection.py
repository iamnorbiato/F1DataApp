import os
import traceback
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from psycopg2 import OperationalError

class Command(BaseCommand):
    help = 'Testa a conexão com o banco de dados PostgreSQL usando a configuração do Django.'

    def handle(self, *args, **options):
        try:
            self.stdout.write(self.style.SUCCESS("Tentando testar a conexão com o banco de dados Django..."))

            # Função para imprimir variáveis de ambiente e seus bytes
            def debug_env_var(name):
                val = os.environ.get(name)
                if val is None:
                    self.stdout.write(self.style.WARNING(f"{name} = None"))
                else:
                    # Exibe valor e bytes escapados para identificar caracteres inválidos
                    self.stdout.write(f"{name} = {val} (bytes: {val.encode(errors='backslashreplace')})")

            # Debug das variáveis de ambiente usadas para conexão
            debug_env_var('DB_NAME')
            debug_env_var('DB_USER')
            debug_env_var('DB_PASSWORD')
            debug_env_var('DB_HOST')
            debug_env_var('DB_PORT')

            # Tenta abrir cursor e executar query simples para testar conexão
            with connection.cursor() as cursor:
                cursor.execute("SELECT version();")
                db_version = cursor.fetchone()[0]

            self.stdout.write(self.style.SUCCESS(
                f"Hello World! Conexão com o PostgreSQL estabelecida e testada com sucesso."
            ))
            self.stdout.write(self.style.SUCCESS(
                f"Versão do PostgreSQL: {db_version}"
            ))

        except OperationalError as e:
            raise CommandError(
                f"Erro de conexão operacional com o PostgreSQL: {e}"
            )
        except Exception as e:
            traceback.print_exc()
            raise CommandError(
                f"Ocorreu um erro inesperado ao testar a conexão: {e}. "
                f"Certifique-se de que o Django settings.py está configurado corretamente para o DB."
            )
        finally:
            self.stdout.write(self.style.SUCCESS("Teste de conexão concluído."))
