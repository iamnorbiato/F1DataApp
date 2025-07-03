#F1Data_Web Dockerfile
# Imagem base com Nginx (é tudo o que precisamos para proxyar)
FROM nginx:stable-alpine

# Remove a configuração padrão do Nginx (<<--- ESTA LINHA SERÁ REMOVIDA!)
RUN rm /etc/nginx/conf.d/default.conf

# Copia a sua configuração personalizada do Nginx
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expõe a porta padrão do Nginx
EXPOSE 80

# Comando para iniciar o Nginx
CMD ["nginx", "-g", "daemon off;"]