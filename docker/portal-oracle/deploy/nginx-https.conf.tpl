# Nginx - Portal Oracle "@NOME@" com HTTPS. Gerado a partir de deploy/nginx-https.conf.tpl
# Debian/Ubuntu: /etc/nginx/sites-available/@NOME@   RHEL: /etc/nginx/conf.d/@NOME@.conf
#
# @SSL_CERT@ tem de ter o certificado do servidor seguido da cadeia (intermedios).
# @SSL_KEY@  e a chave privada, sem password.

# HTTP: so redireciona para HTTPS
server {
    listen @HTTP_PORT@;
    server_name @SERVER_NAME@;
    return 301 @HTTPS_URL@$request_uri;
}

server {
    listen @HTTPS_PORT@ ssl;
    server_name @SERVER_NAME@;

    ssl_certificate     @SSL_CERT@;
    ssl_certificate_key @SSL_KEY@;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;
    # Opcional, depois de confirmar que o HTTPS funciona (o browser passa a recusar HTTP):
    # add_header Strict-Transport-Security "max-age=31536000" always;

    client_max_body_size 20m;          # upload de backups

    access_log /var/log/nginx/@NOME@.access.log;
    error_log  /var/log/nginx/@NOME@.error.log;

    location /static/ {
        alias @DIR@/app/static/;
        expires 1h;
    }

    # Gunicorn parado ou a reiniciar (atualizacao): pagina estatica em vez de "502 Bad Gateway".
    # (o modo de manutencao do portal e outra coisa: e a aplicacao que responde 503 com a mensagem)
    error_page 502 504 /indisponivel.html;
    location = /indisponivel.html {
        root @DIR@/app/static;
        internal;
    }

    location / {
        proxy_pass         http://@BIND@;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 180s;
    }
}
