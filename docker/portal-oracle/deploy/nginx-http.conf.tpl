# Nginx - Portal Oracle "@NOME@" so com HTTP. Gerado a partir de deploy/nginx-http.conf.tpl
# Debian/Ubuntu: /etc/nginx/sites-available/@NOME@   RHEL: /etc/nginx/conf.d/@NOME@.conf
server {
    listen @HTTP_PORT@;
    server_name @SERVER_NAME@;

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
