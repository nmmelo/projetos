# Conversa: watchtower-ui (interface web para o Watchtower)

Chat de 17 de novembro de 2026. As perguntas estão como foram escritas; as respostas estão resumidas, mas
mantêm as conclusões. Os ficheiros finais estão nesta pasta.

---

## 1. Interface web do Watchtower

**Pergunta:**
> existe alguma interface web para o container watchtower

**Resposta:**
Não. O Watchtower não tem interface web; a HTTP API serve só para disparar updates (`/v1/update`) e expor
métricas Prometheus (`/v1/metrics`).

Opções:

| Opção | Notas |
|---|---|
| Manter o Watchtower + Portainer | Visibilidade dos contentores pela UI do Portainer; métricas no Grafana |
| Tugtainer | UI com autenticação, cron, notificações, vários hosts por agentes |
| WUD (What's Up Docker) | Substituto mais direto, com limites de update (ex. só patch) |
| WatchWarden | Aceita as variáveis `WATCHTOWER_*`, políticas por contentor |
| Dockwatch | UI cuidada e notificações |
| Diun / Cup | Só notificação, sem updates automáticos |

---

## 2. Interface própria para a HTTP API

**Pergunta:**
> e fazer uma interface web para usar os HTTP API, sendo que esses dois container devem estar numa rede de docker

**Resposta:**
Contentor `nginx:alpine` que serve uma página HTML/JS e faz proxy para `watchtower:8080`, juntando o token
(o browser nunca o vê). Os dois contentores na rede `watchtower-net`; o watchtower sem portas publicadas.

- `POST /api/update` → `/v1/update` (todos ou `?image=a,b`)
- `GET /api/metrics` → `/v1/metrics` (lido a cada 30 s)
- Com a API de update ativa, o watchtower deixa de verificar sozinho: é preciso
  `WATCHTOWER_HTTP_API_PERIODIC_POLLS=true`.
- A página não tem autenticação: não expor à internet sem login à frente.

---

## 3. Só o contentor web

**Pergunta:**
> mas o container watchtower já existe, apenas preciso do container web

**Resposta:**
O compose passou a ter só o `watchtower-ui`, ligado à rede `watchtower-net` como rede externa. O nome do
watchtower, a porta, a rede e o token são configuráveis no `.env`. O nginx usa o DNS interno do Docker
(`127.0.0.11`) para resolver o watchtower em cada pedido, por isso a UI arranca mesmo que o watchtower esteja em
baixo. No watchtower existente basta ativar as variáveis da HTTP API e ligá-lo à rede
(`docker network connect watchtower-net watchtower`).
