# Conversa: dev-portal (Ansible + portal Python em Docker)

Chat com o Claude, a 4 e 9 de novembro de 2026. As perguntas estão como foram escritas; as respostas
estão resumidas, mas mantêm os comandos e as conclusões. Os ficheiros finais estão nesta pasta.

---

## 1. Acrescentar ping, ip, Python e servidor web ao Dockerfile

**Pergunta:**
> quero alterar este dockerfile para instalar ping, ip, python, apache2 e tudo necessário para ter um portal em python

**Resposta:**
Ao Dockerfile original (Ubuntu 26.04 com Ansible, SSH e os utilizadores `nmmelo` e `adminssc`) foram acrescentados:

| Categoria | Pacotes |
|---|---|
| Rede | `iputils-ping`, `iproute2`, `dnsutils`, `net-tools` |
| Python | `python3-dev` + venv em `/opt/portal/venv` (`flask`, `gunicorn`, `mysqlclient`, `pymysql`, `sqlalchemy`, `python-dotenv`) |
| MariaDB | `libmariadb-dev`, `pkg-config`, `build-essential`, `mariadb-client` |

O venv evita o bloqueio do pip no sistema (PEP 668, "externally managed").

---

## 2. Sem o módulo Python do Apache

**Pergunta:**
> em vez do modulo para o apache de python quero outra solução

**Resposta:**
O `mod_wsgi` foi trocado por **Gunicorn** em `127.0.0.1:8000`, com o servidor web a fazer de reverse proxy.
O `entrypoint.sh` passou a arrancar o Gunicorn antes do `sshd`.

---

## 3. docker-compose sem o Semaphore

**Pergunta:**
> e adaptar este docker-compose.yml ... podemos tirar tudo o que faz referencia a semaphore

**Resposta:**
- Removidos o serviço `semaphore`, a porta 3000 e os volumes `semaphore_*`.
- Acrescentados a porta `8080:80`, o mount `./portal:/opt/portal/app` e um volume para os logs.
- Corrigidos dois erros do ficheiro original: o secret `adminss_public_key` (o nome certo é `adminssc_public_key`) e a indentação de `file:`.

---

## 4. Código do portal numa pasta do anfitrião

**Pergunta:**
> mas perfiro criar uma pasta e colocar lá o app.y assim mesmo que o container seja desligado ou apagado o conteudo da pasta fica salvaguardado.

**Resposta:**
O `app.py` deixou de ser copiado para a imagem. O código fica só em `./portal`, no anfitrião.
Se o `app.py` não existir, o `entrypoint.sh` termina com uma mensagem de erro.
Os ficheiros em `./portal` não se perdem com `docker-compose down` nem com `down -v`.

---

## 5. Erros ao arrancar no Synology

| Erro | Causa | Solução |
|---|---|---|
| `lstat .../Dockerfile: no such file or directory` | O Dockerfile não estava na pasta ou tinha outro nome | `ls -la`, renomear para `Dockerfile` |
| `found character '\t' that cannot start any token` | Tabs no YAML | `sed -i 's/\t/  /g' docker-compose.yml` |
| `mapping values are not allowed here` | Indentação desalinhada depois de trocar os tabs | Reescrever o ficheiro e validar com `docker-compose config` |
| O contentor ia abaixo sem parar | `--reload-extra-file` sem valor no comando do Gunicorn; com `set -e` o script terminava | Remover a opção |

O nome do contentor define-se com `container_name: dev-portal` (e `hostname: dev-portal`) no `docker-compose.yml`.

---

## 6. Alterações no código não apareciam

**Pergunta:**
> mudei o app.y, mas já fiz restart ao apache2 e o texto continua a mesma

**Resposta:**
O Gunicorn carrega o código uma vez, quando arranca. Reiniciar o servidor web não chega.
Não há `systemd` no contentor, por isso não existe um serviço `gunicorn`.

| Opção | Comando |
|---|---|
| Reload | `docker exec dev-portal sh -c 'kill -HUP $(cat /run/gunicorn.pid)'` |
| Automático (desenvolvimento) | `--reload --reload-engine poll` no comando do Gunicorn |
| Templates Flask | `app.config["TEMPLATES_AUTO_RELOAD"] = True` |

Ficou com `--reload`. O Supervisor foi apresentado como alternativa para gerir os processos, mas não foi implementado.

---

## 7. Apache, Nginx ou Caddy

**Pergunta:**
> existe alguma melhor que o apache2

**Resposta:**
Para reverse proxy de Gunicorn, o padrão é o **Nginx**. O Caddy só compensa quando trata ele próprio do HTTPS.
Como o NAS Synology já faz HTTPS no Reverse Proxy do DSM, a escolha foi o **Nginx**.

```
Internet ──► DSM Reverse Proxy (HTTPS) ──► :8080 Nginx ──► Gunicorn :8000 ──► app.py
```

Ficheiros alterados: `Dockerfile` (`nginx`), `portal-nginx.conf` (novo), `entrypoint.sh` (`nginx -t` + `nginx`),
`docker-compose.yml` (volume `nginx_logs`) e `README.md`.

---

## 8. Guardar no repositório

**Pergunta:**
> quero colocar no repositorio projeto branch main na pasta docker com tudo o que preciso

**Resposta:**
Projeto guardado em `docker/dev-portal`, com um `.gitignore` que exclui `secrets/`, `workspace/` e `portal/.env`.
