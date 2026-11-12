"""
Exemplo de cliente para usar no outro projeto (portal), na mesma rede Docker.

Variáveis no portal:
  TESLA_GATEWAY_URL=http://tesla-key:8000
  TESLA_GATEWAY_API_KEY=tk_...   (chave do cliente, criada com `tesla-setup cliente add portal`)
"""
import os

import requests

URL = os.environ.get("TESLA_GATEWAY_URL", "http://tesla-key:8000")
HEADERS = {"X-API-Key": os.environ.get("TESLA_GATEWAY_API_KEY", "")}


def veiculos():
    r = requests.get(f"{URL}/api/vehicles", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()["response"]


def estado(vin):
    """online / asleep / offline (não acorda o carro)."""
    r = requests.get(f"{URL}/api/vehicles/{vin}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()["response"]["state"]


def dados(vin, endpoints="charge_state;climate_state;drive_state;vehicle_state", fresh=False):
    """Devolve vehicle_data ou None se o carro estiver a dormir."""
    params = {"endpoints": endpoints}
    if fresh:
        params["fresh"] = "1"
    r = requests.get(f"{URL}/api/vehicles/{vin}/data", headers=HEADERS, params=params, timeout=30)
    if r.status_code == 408:
        return None
    r.raise_for_status()
    return r.json()["response"]


if __name__ == "__main__":
    for v in veiculos():
        print(v["display_name"], v["vin"], v["state"])
        if v["state"] == "online":
            d = dados(v["vin"])
            print("  bateria:", d["charge_state"]["battery_level"], "%",
                  "| autonomia:", round(d["charge_state"]["battery_range"] * 1.609), "km")
