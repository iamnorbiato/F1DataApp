# F1Data_App Dockerfile
# Use uma imagem oficial do Python como base
FROM python:3.10-slim-buster

# Define o diretório de trabalho dentro do contêiner para onde manage.py está
WORKDIR /app

# Cria um diretório para scripts e copia o script de importação de dados
RUN mkdir -p /app/scripts

# Copia o requirements.txt para o *diretório temporário* /tmp no contêiner
# para que possa ser acessado de qualquer WORKDIR para a instalação.
COPY requirements.txt /tmp/requirements.txt

# Instala as dependências Python
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copia o restante do código da aplicação para o WORKDIR atual (/app)
COPY . .

# Expõe a porta que o Django usará (padrão é 8000)
EXPOSE 8000

# Comando para iniciar o servidor Django. Como o WORKDIR é /app,
# o manage.py será encontrado.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]