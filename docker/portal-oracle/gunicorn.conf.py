# Gunicorn: as execucoes de shrink correm em threads em segundo plano,
# por isso os pedidos HTTP sao curtos e o timeout pode ser normal.
bind = "127.0.0.1:8000"          # o Nginx no mesmo contentor faz de proxy
workers = 2
threads = 4
timeout = 120
preload_app = True          # cria chaves/BD SQLite uma unica vez, antes dos workers
accesslog = "-"
errorlog = "-"
# NAO usar max_requests: reciclar um worker terminaria execucoes em curso.
