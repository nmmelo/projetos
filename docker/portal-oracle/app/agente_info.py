"""Estado do agente do portal (lido pelo portal). O agente esta em app/agente.py."""
import json
import time

from . import store

HEARTBEAT_MAX = 15          # segundos sem sinal -> agente considerado parado


def estado() -> dict:
    """{"ativo": bool, ...dados publicados pelo agente: pid, host, versao, inicio, heartbeat, jobs}"""
    try:
        raw = store.get_setting("agente_estado")
        e = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001 - BD indisponivel ou valor invalido
        e = {}
    hb = e.get("heartbeat") or 0
    e["ativo"] = bool(hb) and time.time() - hb < HEARTBEAT_MAX
    e["ha"] = int(time.time() - hb) if hb else None
    return e


def ativo() -> bool:
    return estado()["ativo"]
