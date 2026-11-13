# tesla-key — referência da API para clientes

Documento para entregar a quem desenvolve um projeto que vai ler dados do Tesla através do gateway.
Especificação formal: [`openapi.yaml`](openapi.yaml).

## Ligação

| Item | Valor |
|---|---|
| URL base (mesma rede Docker) | `http://tesla-key:8000` |
| URL base (LAN, se `API_PORT` publicado) | `http://<ip-nas>:<API_PORT>` |
| Autenticação | Header `X-API-Key: tk_...` (chave do cliente, criada com `tesla-setup cliente add <nome>`) |
| Formato | JSON (UTF-8) |
| Erros | Sempre `{"erro": "<mensagem>"}` com o código HTTP correspondente |

Variáveis sugeridas no projeto cliente:
```bash
TESLA_GATEWAY_URL=http://tesla-key:8000
TESLA_GATEWAY_API_KEY=tk_...
```

## Endpoints

| Método | Caminho | Autenticação | Descrição |
|---|---|---|---|
| GET | `/api/health` | não | Gateway a responder |
| GET | `/api/token/status` | sim | Estado do token Tesla (valida também a chave) |
| GET | `/api/vehicles` | sim | Lista de veículos da conta |
| GET | `/api/vehicles/{vin}` | sim | Estado do veículo (`online`/`asleep`/`offline`), **não acorda** o carro |
| GET | `/api/vehicles/{vin}/data` | sim | Dados completos (`vehicle_data`), com cache |
| POST | `/api/vehicles/{vin}/wake` | sim | Acorda o carro (usar com moderação) |
| GET | `/api/fleet/{caminho}` | sim | Passthrough só de leitura para `/api/1/{caminho}` da Fleet API |

### Teste de ligação recomendado

1. `GET /api/health` → `200 {"ok": true}` — gateway acessível.
2. `GET /api/token/status` com a chave → `200` — chave válida e token Tesla presente.
3. `GET /api/vehicles` → `200` — ligação à Tesla OK.

---

### `GET /api/health`
```json
{ "ok": true }
```

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

Unidades da Tesla: distâncias e `odometer` em **milhas** (× 1,609 para km), `speed` em **mph**,
temperaturas em **°C**, `charging_state` em `Disconnected`, `Stopped`, `Charging`, `Complete`, `NoPower`.
Latitude/longitude só aparecem com `endpoints=location_data` e o scope de localização na aplicação Tesla.

**Carro a dormir:** responde `408` (não é erro do cliente):
```json
{ "erro": "veículo a dormir ou offline", "estado": "asleep" }
```

### `POST /api/vehicles/{vin}/wake`
Acorda o carro. Demora 10–60 s até `state` passar a `online` (consultar `GET /api/vehicles/{vin}`).
Custa mais e gasta bateria — não usar em ciclos automáticos.

### `GET /api/fleet/{caminho}`
Leitura direta de outros endpoints GET da Fleet API, por exemplo `/api/fleet/vehicles/{vin}/nearby_charging_sites`.
A resposta é a da Tesla, sem alterações.

## Códigos de resposta

| Código | Quando | O que o cliente deve fazer |
|---|---|---|
| 200 | OK | — |
| 401 | `X-API-Key` em falta, errada ou cliente desativado | Verificar a chave; não repetir em ciclo |
| 404 | Caminho inexistente | Rever o URL (prefixo `/api`, sem duplicar) |
| 405 | Método errado (ex.: `GET .../wake`) | Usar o método da tabela |
| 408 | Carro a dormir ou offline | Mostrar o último valor conhecido; tentar mais tarde |
| 429 | Limite de pedidos da Tesla | Esperar e reduzir a frequência |
| 502 | Falha na Tesla ou na renovação do token | Tentar mais tarde; ver logs no portal admin |
| 503 | Gateway sem tokens (falta o `tesla-setup token`) | Avisar o administrador |

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
