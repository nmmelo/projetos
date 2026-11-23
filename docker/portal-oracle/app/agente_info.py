"""Estado dos agentes do portal (lido pelo portal). Os agentes estao em app/agente.py (agente do portal) e
app/agente_oracle.py (agente Oracle); a lista e os parametros em app/agentes.py."""
import json
import time

from . import store

HEARTBEAT_MAX = 15          # segundos sem sinal -> agente considerado parado


def estado(nome: str = "agente") -> dict:
    """{"ativo": bool, ...dados publicados pelo agente: pid, host, versao, inicio, heartbeat, execucoes}"""
    try:
        raw = store.get_setting(f"{nome}_estado")
        e = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001 - BD indisponivel ou valor invalido
        e = {}
    hb = e.get("heartbeat") or 0
    e["ativo"] = bool(hb) and time.time() - hb < HEARTBEAT_MAX
    e["ha"] = int(time.time() - hb) if hb else None
    return e


def ativo(nome: str = "agente") -> bool:
    return estado(nome)["ativo"]


def oracle_ativo() -> bool:
    """Ha quem corra execucoes Oracle em fila: o agente Oracle ou, sem ele, o agente do portal."""
    return ativo("agente_oracle") or ativo("agente")
