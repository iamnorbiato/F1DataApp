# G:\Learning\F1Data\F1Data_App\core\management\commands\test_db_connection.py
from django.core.management.base import BaseCommand, CommandError
from django.db import connection # Importa a conexão de banco de dados padrão do Django
from psycopg2 import OperationalError # Mantemos para capturar erros específicos do PostgreSQL se a conexão falhar

class Command(BaseCommand):
    help = 'Testa a conexão com o banco de dados PostgreSQL usando a configuração do Django.'

    def handle(self, *args, **options):
        try:
            self.stdout.write(self.style.SUCCESS("Tentando testar a conexão com o banco de dados Django..."))

            # Usa a conexão padrão do Django. O Django já a estabeleceu.
            # Apenas verificamos se ela está ativa e executamos um comando simples.
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
            # OperationalError é do psycopg2, mas pode ser levantado pela conexão Django se houver problema.
            raise CommandError(
                f"Erro de conexão operacional com o PostgreSQL: {e}"
            )
        except Exception as e:
            # Captura qualquer outro erro que possa ocorrer (ex: Django settings não configuradas)
            raise CommandError(
                f"Ocorreu um erro inesperado ao testar a conexão: {e}. "
                f"Certifique-se de que o Django settings.py está configurado corretamente para o DB."
            )
        finally:
            # Django gerencia o fechamento da conexão automaticamente na maioria dos casos,
            # mas em alguns comandos pode ser útil forçar o fechamento.
            # connection.close() # Geralmente não é necessário, o Django cuida
            self.stdout.write(self.style.SUCCESS("Teste de conexão concluído."))