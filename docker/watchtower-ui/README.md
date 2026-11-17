# watchtower-ui

Interface web simples para a HTTP API de um contentor **Watchtower** já existente. É um contentor `nginx:alpine`
que serve uma página e faz de proxy para a API, juntando o token. O token fica só no nginx e nunca chega ao browser.

## Funcionalidades

| Ação | Interface | API do Watchtower |
|---|---|---|
| Atualizar todos os contentores | `POST /api/update` | `/v1/update` |
| Atualizar só algumas imagens | `POST /api/update?image=nginx,mariadb` | `/v1/update?image=...` |
| Ver métricas (atualização a cada 30 s) | `GET /api/metrics` | `/v1/metrics` |

Métricas mostradas: contentores verificados, atualizados e com falha na última verificação, total de
verificações e verificações ignoradas. Há também um registo das ações feitas na página.

## Arquitetura

```
browser ──► :8090 watchtower-ui (nginx) ──► watchtower:8080 (HTTP API)
                    └────────── rede watchtower-net ──────────┘
```

| Contentor | Rede | Porta publicada |
|---|---|---|
| `watchtower` (já existente) | `watchtower-net` | nenhuma (a API só é acessível dentro da rede) |
| `watchtower-ui` | `watchtower-net` | `8090` (configurável) |

## Ficheiros

| Ficheiro | Descrição |
|---|---|
| `docker-compose.yml` | Contentor `watchtower-ui` ligado à rede externa |
| `.env.example` | Variáveis: porta, nome do watchtower, rede e token |
| `templates/default.conf.template` | Configuração do nginx (proxy para a API) |
| `html/index.html` | Página web (HTML + JS, sem dependências) |

## Pré-requisitos no contentor watchtower

O watchtower tem de ter a HTTP API ativa e estar na mesma rede.

**1. Variáveis de ambiente** (no compose ou `docker run` do watchtower; obriga a recriar o contentor):

| Variável | Valor | Notas |
|---|---|---|
| `WATCHTOWER_HTTP_API_UPDATE` | `true` | Ativa `/v1/update` |
| `WATCHTOWER_HTTP_API_METRICS` | `true` | Ativa `/v1/metrics` |
| `WATCHTOWER_HTTP_API_TOKEN` | token | O mesmo do `.env` da UI |
| `WATCHTOWER_HTTP_API_PERIODIC_POLLS` | `true` | Sem isto, ao ativar a API de update o watchtower deixa de verificar sozinho |

Não é preciso publicar a porta 8080 do watchtower.

**2. Rede partilhada:**

```bash
docker network create watchtower-net
docker network connect watchtower-net watchtower
```

Se o watchtower for gerido por compose, é melhor declarar a rede lá para não se perder ao recriar:

```yaml
services:
  watchtower:
    # ...
    networks:
      - watchtower-net

networks:
  watchtower-net:
    name: watchtower-net
    external: true
```

## Instalação

```bash
cd docker/watchtower-ui
cp .env.example .env
# editar .env: WATCHTOWER_HTTP_API_TOKEN igual ao do watchtower
# (para gerar um novo: openssl rand -hex 32)
docker compose up -d
```

Abrir `http://<anfitrião>:8090`.

## Verificação

```bash
# o watchtower está na rede?
docker network inspect watchtower-net --format '{{range .Containers}}{{.Name}} {{end}}'

# a UI chega à API?
curl -s http://localhost:8090/api/metrics | grep ^watchtower_

# logs
docker logs watchtower-ui
docker logs watchtower
```

| Sintoma | Causa provável |
|---|---|
| `502 Bad Gateway` | O watchtower não está na rede ou `WATCHTOWER_HOST` está errado |
| `401 Unauthorized` | Token diferente entre a UI e o watchtower |
| `404` em `/api/update` | Falta `WATCHTOWER_HTTP_API_UPDATE=true` no watchtower |
| O update fica pendente muito tempo | Normal: o pedido só termina quando o pull e a recriação acabam (timeout de 15 min) |

## Segurança

- A página **não tem autenticação**. Não a publicar na internet sem login à frente (`auth_basic` no nginx ou o
  proxy reverso do NAS/router com autenticação).
- O `.env` com o token está no `.gitignore`.
