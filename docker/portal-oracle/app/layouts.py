"""Disposicao dos paineis de cartoes (Home, Sistema, resumo do cliente), por utilizador: ordem e cartoes escondidos."""
import json

from . import store

PAINEIS = ("home", "sistema", "cliente")


def _chave(painel: str, user_key: str) -> str:
    return f"layout.{painel}.{user_key}"


def obter(painel: str, user_key: str) -> dict:
    try:
        v = json.loads(store.get_setting(_chave(painel, user_key)) or "{}")
    except ValueError:
        v = {}
    return {"ordem": [str(x) for x in v.get("ordem") or []][:200],
            "ocultos": [str(x) for x in v["ocultos"]][:200] if isinstance(v.get("ocultos"), list) else None}


def guardar(painel: str, user_key: str, dados: dict) -> None:
    if painel not in PAINEIS:
        raise ValueError("painel desconhecido")
    lim = lambda xs: [str(x)[:80] for x in (xs or []) if x][:200]  # noqa: E731
    store.save_settings({_chave(painel, user_key): json.dumps({"ordem": lim(dados.get("ordem")),
                                                                "ocultos": lim(dados.get("ocultos"))})})
