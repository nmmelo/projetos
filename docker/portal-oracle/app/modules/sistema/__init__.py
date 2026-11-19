"""Modulo Sistema: servidor onde o portal corre (CPU, RAM, discos) e servicos (Nginx, portal, agente,
...) com start/stop/restart. Os dados sao publicados pelo agente do portal a cada 2 s."""
import json

from flask import Blueprint, abort, g

from ... import agente_info, modulos, store

bp = Blueprint("sistema", __name__, url_prefix="/sistema", template_folder="templates")


def metricas_guardadas() -> dict:
    try:
        return json.loads(store.get_setting("sistema_metricas") or "{}")
    except (ValueError, TypeError):
        return {}


def _home_context(user) -> dict:
    ag = agente_info.estado()
    m = metricas_guardadas() if ag["ativo"] else {}
    raiz = next((d for d in m.get("discos", []) if d["ponto"] == "/"), (m.get("discos") or [None])[0])
    servs = m.get("servicos", [])
    return {"agente": ag, "m": m, "raiz": raiz,
            "servicos_ok": sum(1 for s in servs if s.get("estado") == "active"), "servicos_total": len(servs)}


MODULO = modulos.registar(modulos.Modulo(
    chave="sistema",
    nome="Sistema",
    descricao="Servidor onde o portal corre: CPU, RAM, discos e serviços (Nginx, portal, agente), "
              "com iniciar, parar e reiniciar. Atualizado a cada 2 s pelo agente do portal.",
    acoes={
        "sistema.servicos": "Iniciar, parar e reiniciar serviços",
        "sistema.definicoes": "Definições: serviços mostrados no módulo",
    },
    abas=[
        ("sistema.painel", "Painel", None),
        ("sistema.definicoes", "Definições", "sistema.definicoes"),
    ],
    home_template="sistema/_home.html",
    eventos={"sistema.servico": {"nome": "Serviço (detetado pelo agente)",
                                 "situacoes": {"parado": "Parou", "falhou": "Falhou", "iniciado": "Voltou a correr"}},
             "sistema.disco": {"nome": "Disco acima do limiar", "situacoes": {"acima": "Acima do limiar"},
                               "parametro": "Limiar (%)"}},
    home_context=_home_context,
))


@bp.before_request
def _guard():
    if MODULO.chave not in modulos.ativos():
        abort(404)
    if g.get("user") and not g.user.can(MODULO.acesso):
        abort(403)


from . import views  # noqa: E402,F401
