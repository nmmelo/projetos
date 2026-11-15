# tesla-key — referência da API para clientes

Documento para entregar a quem desenvolve um projeto que vai ler dados do Tesla através do gateway.
O gateway também o serve em `GET /API.md`, com a página `GET /docs` e a especificação `GET /openapi.yaml` (sem chave).
Especificação formal: [`openapi.yaml`](openapi.yaml).

## Ligação

| Item | Valor |
|---|---|
| URL base (mesma rede Docker) | `http://tesla-key:8000` |
| URL base (LAN, se `API_PORT` publicado) | `http://<ip-nas>:<API_PORT>` |
| Autenticação | Header `X-API-Key: tk_...` (chave do cliente, criada com `tesla-setup cliente add <nome>`) |
| Formato | JSON (UTF-8) |
| Erros | `{"codigo": 403, "erro": "Forbidden", "ref": 1234}` com o código HTTP correspondente (ver "Códigos de resposta") |

Variáveis sugeridas no projeto cliente:
```bash
TESLA_GATEWAY_URL=http://tesla-key:8000
TESLA_GATEWAY_API_KEY=tk_...
```

## Permissões

Cada cliente tem um conjunto de permissões definido pelo administrador (portal admin → Clientes →
Permissões, ou `tesla-setup cliente perm`). Um endpoint sem permissão devolve **403**. O cliente pode
consultar as suas permissões em `GET /api/me`.

| Permissão | Dá acesso a | Endpoints |
|---|---|---|
| `veiculos` | Lista e estado dos veículos (online/asleep) | `/api/vehicles`, `/api/vehicles/{vin}` |
| `carga` | Bateria, autonomia, carregamento (`charge_state`) | `/data` |
| `clima` | Temperaturas e climatização (`climate_state`) | `/data` |
| `conducao` | Mudança, velocidade, potência, sem coordenadas (`drive_state`) | `/data` |
| `estado` | Odómetro, trancas, sentinela, versão (`vehicle_state`) | `/data` |
| `config` | Modelo, cor e jantes (`vehicle_config`) | `/data` |
| `localizacao` | Coordenadas GPS (`location_data`) | `/location`, `/data` |
| `imagem` | Imagem do modelo | `/image` |
| `viagens` | Viagens e posições registadas pelo gateway; estado da recolha | `/trips`, `/positions`, `/api/recolha` |
| `consumo` | Consumo estimado de créditos (todos os clientes) | `/api/consumo` |
| `carregamentos` | Carregamentos registados pelo gateway | `/charges` |
| `token` | Estado do token Tesla | `/api/token/status` |
| `acordar` | Acordar o carro (sensível) | `/wake` |
| `fleet` | Passthrough para qualquer GET da Fleet API (sensível, acesso amplo) | `/api/fleet/...` |

| Preset | Permissões |
|---|---|
| `tudo` | Todas |
| `leitura` | Todas menos `acordar` e `fleet` (omissão para novos clientes) |
| `basico` | `veiculos`, `carga`, `imagem` |
| `nenhuma` | Nenhuma (só `/api/health` e `/api/me`) |

Em `/data`, as secções sem permissão são retiradas do pedido e listadas em `seccoes_negadas`; se nenhuma
for permitida, a resposta é 403.

O administrador também pode desligar funcionalidades para todos os clientes. Nesse caso a resposta é 403
com `a funcionalidade '<nome>' está desativada no gateway`, e `GET /api/me` lista-as em
`desativadas_no_gateway`.

## Cache

Para poupar créditos, o gateway responde da cache durante um tempo configurado pelo administrador
(por omissão 60 s) em `/api/vehicles`, `/api/vehicles/{vin}`, `/data` e `/location`. Acrescentar `?fresh=1`
para forçar um pedido novo à Tesla (conta como pedido pago).

## Endpoints

| Método | Caminho | Autenticação | Descrição |
|---|---|---|---|
| GET | `/api/health` | não | Gateway a responder |
| GET | `/api/me` | sim | Nome do cliente e permissões (sempre permitido) |
| GET | `/api/token/status` | sim | Estado do token Tesla (valida também a chave) |
| GET | `/api/vehicles` | sim | Lista de veículos da conta |
| GET | `/api/vehicles/{vin}` | sim | Estado do veículo (`online`/`asleep`/`offline`), **não acorda** o carro |
| GET | `/api/vehicles/{vin}/data` | sim | Dados completos (`vehicle_data`), com cache |
| GET | `/api/vehicles/{vin}/location` | sim | Localização simplificada (latitude, longitude, direção, velocidade) |
| GET | `/api/vehicles/{vin}/image` | sim | Imagem do modelo (render do configurador, não das câmaras) |
| GET | `/api/vehicles/{vin}/trips` | sim | Viagens registadas pelo gateway, com totais |
| GET | `/api/vehicles/{vin}/trips/{id}` | sim | Uma viagem com o percurso |
| GET | `/api/vehicles/{vin}/positions` | sim | Posições registadas (mapa de calor ou percurso) |
| GET | `/api/vehicles/{vin}/charges` | sim | Carregamentos registados pelo gateway, com totais |
| GET | `/api/vehicles/{vin}/charges/{id}` | sim | Um carregamento com a curva |
| GET | `/api/recolha` | sim | Estado da recolha de viagens |
| GET | `/api/consumo` | sim | Consumo estimado de créditos no mês (todos os clientes) |
| GET | `/api/me/consumo` | sim | Consumo estimado só deste cliente |
| GET | `/api/endpoints` | não | Esta referência em JSON |
| POST | `/api/vehicles/{vin}/wake` | sim | Acorda o carro (usar com moderação) |
| GET | `/api/fleet/{caminho}` | sim | Passthrough só de leitura para `/api/1/{caminho}` da Fleet API |

### Teste de ligação recomendado

1. `GET /api/health` → `200 {"ok": true}` — gateway acessível.
2. `GET /api/me` com a chave → `200` — chave válida; mostra as permissões do cliente.
3. `GET /api/vehicles` → `200` — ligação à Tesla OK.

---

### `GET /api/health`
```json
{ "ok": true }
```

### `GET /api/me`
```json
{
  "cliente": "portal",
  "permissoes": ["veiculos", "carga", "imagem"],
  "desativadas_no_gateway": [],
  "descricao": { "veiculos": "Lista e estado dos veículos (online/asleep)", "carga": "...", "imagem": "..." }
}
```
Útil para o cliente mostrar ou esconder funcionalidades conforme o acesso que tem.

### `GET /api/token/status`
```json
{
  "ok": true,
  "obtido_em": "2026-11-12T16:58:07+00:00",
  "expira_em": "2026-11-13T00:58:06+00:00",
  "segundos_restantes": 27285,
  "tem_refresh_token": true
}
```
O access token dura 8 h e é renovado pelo gateway; o cliente não tem de fazer nada.

### `GET /api/vehicles`
```json
{
  "response": [
    {
      "id": 1234567890123456,
      "vehicle_id": 987654321,
      "vin": "5YJ3E1EA0XXXXXXXX",
      "display_name": "Tesla",
      "state": "online",
      "in_service": false,
      "access_type": "OWNER"
    }
  ],
  "count": 1
}
```

### `GET /api/vehicles/{vin}`
Igual a um elemento de `/api/vehicles`, dentro de `response`. Usar para saber se vale a pena pedir dados:
```json
{ "response": { "vin": "5YJ3E1EA0XXXXXXXX", "display_name": "Tesla", "state": "asleep" } }
```

### `GET /api/vehicles/{vin}/data`

| Parâmetro | Omissão | Descrição |
|---|---|---|
| `endpoints` | `charge_state;climate_state;drive_state;vehicle_state;vehicle_config` | Secções pedidas, separadas por `;` (também: `location_data`, `gui_settings`) |
| `fresh` | — | `1` ignora a cache do gateway |

Sem `endpoints`, o gateway usa as secções por omissão definidas pelo administrador.

Resposta (resumida; a Tesla devolve mais campos):
```json
{
  "response": {
    "vin": "5YJ3E1EA0XXXXXXXX",
    "display_name": "Tesla",
    "state": "online",
    "charge_state": {
      "battery_level": 78,
      "battery_range": 221.4,
      "charge_limit_soc": 80,
      "charging_state": "Disconnected",
      "charger_power": 0,
      "time_to_full_charge": 0.0
    },
    "climate_state": {
      "inside_temp": 19.5,
      "outside_temp": 14.0,
      "is_climate_on": false
    },
    "drive_state": {
      "shift_state": null,
      "speed": null,
      "power": 0
    },
    "vehicle_state": {
      "odometer": 12345.6,
      "locked": true,
      "sentry_mode": false,
      "car_version": "2026.38.6"
    }
  },
  "cache": false
}
```

| Campo extra do gateway | Significado |
|---|---|
| `cache` | `true` se veio da cache (por omissão 60 s, configurável) |
| `idade_s` | Idade da cache em segundos (só quando `cache` é `true`) |
| `seccoes_negadas` | Secções pedidas sem permissão (só aparece quando há alguma) |

Unidades da Tesla: distâncias e `odometer` em **milhas** (× 1,609 para km), `speed` em **mph**,
temperaturas em **°C**, `charging_state` em `Disconnected`, `Stopped`, `Charging`, `Complete`, `NoPower`.
Latitude/longitude só aparecem com `endpoints=location_data` e o scope `vehicle_location`; é mais simples usar `/location`.

**Carro a dormir:** responde `408` (não é erro do cliente):
```json
{ "erro": "veículo a dormir ou offline", "estado": "asleep" }
```

### `GET /api/vehicles/{vin}/location`

Requer o scope **`vehicle_location`** (ver "Ativar a localização" no README). Usa a mesma cache do `/data`.

| Parâmetro | Descrição |
|---|---|
| `fresh` | `1` ignora a cache |

```json
{
  "vin": "5YJ3E1EA0XXXXXXXX",
  "latitude": 38.7223,
  "longitude": -9.1393,
  "direcao": 90,
  "velocidade_kmh": 50,
  "mudanca": "D",
  "gps_em": "2026-11-13T15:02:11+00:00",
  "mapa": "https://www.openstreetmap.org/?mlat=38.7223&mlon=-9.1393#map=17/38.7223/-9.1393",
  "cache": false
}
```
`velocidade_kmh` e `mudanca` são `null` com o carro parado. `403` se a Tesla não devolver coordenadas
(falta o scope) e `408` com o carro a dormir.

### `GET /api/vehicles/{vin}/image`

Imagem do modelo gerada pelo compositor do configurador Tesla, com a cor e as jantes do carro. O gateway
guarda-a em disco (30 dias por omissão), por isso só o primeiro pedido vai à Tesla.

| Parâmetro | Omissão | Valores |
|---|---|---|
| `view` | `STUD_3QTR` | `STUD_3QTR` (3/4 frente), `STUD_SIDE`, `STUD_REAR`, `STUD_FRONT`, `STUD_SEAT` (interior) |
| `size` | `800` | 200–2048 px de largura |
| `fresh` | — | `1` gera de novo |
| `formato` | — | `url` devolve só o URL do compositor em JSON, sem descarregar |

Resposta: a imagem (`image/png` ou `image/jpeg`), pronta para `<img src>` ou para guardar. O browser pode guardá-la
5 minutos (`Cache-Control: max-age=300`, com `ETag`); depois de mudar `TESLA_IMAGE_OPTIONS`, pedir uma vez com `?fresh=1`. Com `formato=url`:
```json
{ "url": "https://static-assets.tesla.com/v1/compositor/?model=m3&view=STUD_3QTR&size=800&options=...&bkba_opt=1",
  "origem": "config", "view": "STUD_3QTR", "size": 800 }
```
`origem`: `config` (códigos definidos em `TESLA_IMAGE_OPTIONS`) ou `auto` (aproximado, a partir da cor e
jantes do carro). No modo `auto`, o primeiro pedido precisa do carro acordado (`408` se estiver a dormir).

Num portal, a imagem pode ser servida pelo próprio portal (o browser não tem a chave):
```python
@app.get("/carro.png")
def carro():
    r = requests.get(f"{URL}/api/vehicles/{VIN}/image?size=800", headers=H, timeout=30)
    return Response(r.content, mimetype=r.headers.get("Content-Type", "image/png"))
```

### Viagens (`/trips`, `/positions`)

A Tesla não tem histórico de viagens: é o gateway que as regista, com leituras periódicas (recolha ativada
pelo administrador). Uma viagem começa quando a mudança passa a D/R/N (ou há velocidade) e termina quando
volta a P ou o carro adormece. A distância vem do odómetro; o percurso tem um ponto por leitura
(por omissão de 2 em 2 minutos), por isso é aproximado.

**Filtros** (comuns a `/trips` e `/positions`, usar um):

| Parâmetro | Exemplo | Intervalo |
|---|---|---|
| `ano` | `2026` | o ano |
| `mes` | `2026-11` | o mês |
| `semana` | `2026-W46` | semana ISO (segunda a domingo) |
| `dias` | `30` | últimos N dias |
| `de`, `ate` | `2026-11-01`, `2026-11-15` | intervalo livre (`ate` exclusivo) |
| (nenhum) | | tudo |

#### `GET /api/vehicles/{vin}/trips`
`limite` (omissão 200, máx. 5000).
```json
{
  "viagens": [
    {
      "id": 12, "vin": "...", "estado": "fechada",
      "inicio": "2026-11-14T08:02:11+00:00", "fim": "2026-11-14T08:31:40+00:00",
      "lat_inicio": 38.70, "lon_inicio": -9.10, "lat_fim": 38.77, "lon_fim": -9.17,
      "odo_inicio_km": 16093.4, "odo_fim_km": 16113.6, "km": 20.2,
      "bateria_inicio": 80, "bateria_fim": 76, "velocidade_max_kmh": 112, "pontos": 15
    }
  ],
  "resumo": { "n": 1, "km": 20.2, "bateria": 4, "vmax": 112 },
  "de": "2026-11-01", "ate": "2026-12-01"
}
```
`estado` é `aberta` enquanto a viagem decorre (sem `fim`, `km` nem `bateria_fim`). `origem` é `leituras`
ou `inferida` (aconteceu entre duas leituras com o carro a dormir: km pelo odómetro, `pontos` = 0, sem percurso).

#### `GET /api/vehicles/{vin}/trips/{id}`
A viagem (mesmos campos) mais `percurso`:
```json
{ "id": 12, "km": 20.2, "percurso": [
  { "ts": "2026-11-14T08:02:11+00:00", "lat": 38.70, "lon": -9.10, "velocidade_kmh": 0, "direcao": 90, "bateria": 80, "odo_km": 16093.4 } ] }
```

#### `GET /api/vehicles/{vin}/positions`

| Parâmetro | Omissão | Descrição |
|---|---|---|
| `agregado` | `1` | `1`: grelha de ~100 m com contagem `n` (para mapa de calor); `0`: pontos individuais |
| `so_viagens` | — | `1` exclui as posições de estacionamento |

```json
{ "pontos": [ { "lat": 38.722, "lon": -9.139, "n": 14 } ], "agregado": true, "de": "2026-01-01", "ate": "2027-01-01" }
```
Com `agregado=0`: `{ "ts", "lat", "lon", "velocidade_kmh", "viagem_id" }` por ponto.

Exemplo de mapa de calor no portal (Leaflet + leaflet.heat): pedir `/positions?mes=2026-11` no servidor do
portal e passar `[lat, lon, n]` ao `L.heatLayer`.

### `GET /api/recolha` (permissão `viagens`)
```json
{
  "ativa": true,
  "intervalos": { "conduzir": 120, "parado": 600, "minutos_parado": 30, "dormir": 1800 },
  "veiculos": [ { "vin": "...", "estado": "parado", "ultima_leitura": "2026-11-14T19:40:02+00:00",
                  "proxima_leitura": "2026-11-14T19:50:02+00:00", "bateria": 76,
                  "leituras_hoje": 34, "limite_diario": 150 } ]
}
```
`estado`: `conduzir`, `parado`, `asleep` ou `erro`.

### `GET /api/consumo` (permissão `consumo`)

Estimativa do mês (`?mes=AAAA-MM`, omissão: mês atual) feita pelo gateway com os preços definidos pelo
administrador. O valor oficial está no dashboard de developer.tesla.com.
```json
{
  "mes": "2026-11", "total_usd": 1.05, "credito_usd": 10.0, "credito_usado_pct": 10.6,
  "categorias": { "dados": { "pedidos": 483, "preco": 0.002, "custo": 0.966, "hoje": 41, "limite_dia": 200 },
                  "wake": { "pedidos": 4, "preco": 0.02, "custo": 0.08, "hoje": 0, "limite_dia": 15 },
                  "comando": { "pedidos": 9, "preco": 0.001, "custo": 0.009, "hoje": 0, "limite_dia": 50 } },
  "por_cliente": [ { "cliente": "recolha", "categoria": "dados", "n": 300, "custo": 0.6 } ],
  "por_caminho": [ { "categoria": "dados", "caminho": "vehicles/.../vehicle_data", "n": 420 } ],
  "por_dia": [ { "dia": "2026-11-14", "n": 61 } ],
  "nota": "estimativa contada pelo gateway; ..."
}
```

### `GET /api/me/consumo` (sempre permitido)
```json
{ "cliente": "portal", "mes": "2026-11", "total_usd": 0.32,
  "categorias": { "dados": { "pedidos": 120, "custo": 0.24 }, "wake": { "pedidos": 4, "custo": 0.08 } } }
```

### Carregamentos (`/charges`)

Registados pelo gateway (todos, desde que a recolha está ativa) e completados com o histórico de
Superchargers da Tesla. Filtros iguais aos de `/trips` (`ano`, `mes`, `semana`, `dias`, `de`/`ate`), mais
`origem=leituras|inferido|supercharger` e `limite`.

#### `GET /api/vehicles/{vin}/charges`
```json
{
  "carregamentos": [
    { "id": 7, "vin": "...", "inicio": "2026-11-15T17:53:00+00:00", "fim": "2026-11-15T18:23:00+00:00",
      "estado": "terminado", "origem": "leituras", "tipo": "AC", "lat": 38.75, "lon": -9.15, "local": null,
      "bateria_inicio": 40, "bateria_fim": 80, "energia_kwh": 25.1, "potencia_max_kw": 11.0,
      "custo": null, "moeda": null, "sessao_tesla": null, "odo_km": 16253.2, "pontos": 4 }
  ],
  "resumo": { "n": 1, "kwh": 25.1, "custo": 0.0, "moeda": null, "dc": 0, "sc": 0, "bateria": 40 },
  "de": "2026-11-01", "ate": "2026-12-01"
}
```
| Campo | Significado |
|---|---|
| `estado` | `a_carregar` (a decorrer) ou `terminado` |
| `origem` | `leituras`, `inferido` (bateria subiu ≥ 3 % entre leituras, sem curva) ou `supercharger` (importado da Tesla) |
| `tipo` | `AC` (casa, wallbox) ou `DC` (rápido / Supercharger) |
| `local` | Nome do Supercharger (quando conhecido) |
| `custo`, `moeda` | Valor faturado pela Tesla (Superchargers) |

#### `GET /api/vehicles/{vin}/charges/{id}`
O carregamento mais `curva`:
```json
{ "id": 7, "energia_kwh": 25.1, "curva": [ { "ts": "...", "bateria": 52, "potencia_kw": 11.0, "energia_kwh": 8.2 } ] }
```

### `GET /api/endpoints`
Lista de endpoints com método, caminho, permissão, descrição, parâmetros e exemplo (sem chave). O portal admin
mostra a mesma informação na página **API**.

### `POST /api/vehicles/{vin}/wake`
Acorda o carro. Demora 10–60 s até `state` passar a `online` (consultar `GET /api/vehicles/{vin}`).
Custa mais e gasta bateria — não usar em ciclos automáticos.

### `GET /api/fleet/{caminho}`
Leitura direta de outros endpoints GET da Fleet API, por exemplo `/api/fleet/vehicles/{vin}/nearby_charging_sites`.
A resposta é a da Tesla, sem alterações.

## Códigos de resposta

Em caso de erro o gateway devolve só o código, o nome e uma referência:
```json
{ "codigo": 403, "erro": "Forbidden", "ref": 1234 }
```
O motivo fica registado no gateway: portal admin → Definições → Logs → **API** (pesquisa por `ref`). O
administrador pode ativar a opção "Também o motivo", que acrescenta `"detalhe": "..."` à resposta (útil a
desenvolver). Com o carro a dormir, o 408 inclui também `"estado": "asleep"`.

| Código | Quando | O que o cliente deve fazer |
|---|---|---|
| 200 | OK | — |
| 400 | Parâmetro inválido, ou pedido recusado pela Tesla (passthrough) | Rever os parâmetros |
| 401 | `X-API-Key` em falta, errada ou cliente desativado | Verificar a chave; não repetir em ciclo |
| 403 | Chave válida, mas sem a permissão, funcionalidade desligada no gateway, ou scope em falta na Tesla | Ver `GET /api/me`; pedir a permissão ao administrador |
| 404 | Caminho inexistente ou recurso não encontrado | Rever o URL (`GET /api/endpoints`) |
| 405 | Método errado (ex.: `GET .../wake`) | Usar o método da tabela |
| 408 | Carro a dormir ou offline | Mostrar o último valor conhecido; tentar mais tarde |
| 429 | Limite de pedidos da Tesla | Esperar e reduzir a frequência |
| 500 | Erro interno do gateway | Indicar a `ref` ao administrador |
| 502 | Falha na Tesla ou na renovação do token | Tentar mais tarde |
| 503 | Gateway sem tokens (falta o `tesla-setup token`) | Avisar o administrador |

Mostrar ao utilizador o código e a `ref` (ex.: "Erro 403 (ref 1234)") permite ao administrador encontrar o
motivo exato.

## Boas práticas para o cliente

| Prática | Porquê |
|---|---|
| Chamar `GET /api/vehicles/{vin}` antes de `/data` | Não gasta pedidos de dados com o carro a dormir |
| Não chamar `wake` automaticamente | Custo e bateria |
| Intervalo mínimo de 5–10 min entre leituras | O crédito mensal da Tesla é limitado; a cache do gateway ajuda |
| Guardar o último valor conhecido | Para mostrar quando o carro está a dormir (408) |
| Timeout de 30 s nos pedidos | A Tesla pode demorar |

## Exemplos

**curl**
```bash
curl -s http://tesla-key:8000/api/health
curl -s -H "X-API-Key: $TESLA_GATEWAY_API_KEY" http://tesla-key:8000/api/vehicles
curl -s -H "X-API-Key: $TESLA_GATEWAY_API_KEY" \
  "http://tesla-key:8000/api/vehicles/$VIN/data?endpoints=charge_state;climate_state"
```

**Python** — ver [`gateway/cliente_exemplo.py`](gateway/cliente_exemplo.py):
```python
import os, requests

URL = os.environ["TESLA_GATEWAY_URL"]
H = {"X-API-Key": os.environ["TESLA_GATEWAY_API_KEY"]}

v = requests.get(f"{URL}/api/vehicles", headers=H, timeout=30).json()["response"][0]
if v["state"] == "online":
    r = requests.get(f"{URL}/api/vehicles/{v['vin']}/data", headers=H, timeout=30)
    if r.status_code == 200:
        cs = r.json()["response"]["charge_state"]
        print(cs["battery_level"], "%", round(cs["battery_range"] * 1.609), "km")
```
