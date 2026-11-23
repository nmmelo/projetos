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
    import time
    from ... import settings, sistema_info
    ag = agente_info.estado()
    m = metricas_guardadas()
    if not ag["ativo"] or time.time() - (m.get("ts") or 0) > 30:
        # agente parado: leitura direta (sem CPU nem historico)
        m, _ = sistema_info.amostra(None, [settings.DATA_DIR])
        units = sistema_info.servicos_configurados(store.get_setting("sistema_servicos"))
        m["servicos"] = sistema_info.estado_servicos(units)
        m["origem"] = "portal"
    raiz = next((d for d in m.get("discos", []) if d["ponto"] == "/"), (m.get("discos") or [None])[0])
    servs = m.get("servicos", [])
    pressao = [n for n, v in (("CPU", m.get("cpu")), ("memória", (m.get("mem") or {}).get("pct")),
                              ("disco", (raiz or {}).get("pct"))) if v is not None and v >= 90]
    parados = [s["unit"] for s in servs if s.get("estado") != "active"]
    return {"agente": ag, "m": m, "raiz": raiz, "servicos": servs, "pressao": pressao, "parados": parados,
            "servicos_ok": len(servs) - len(parados), "servicos_total": len(servs)}


MODULO = modulos.registar(modulos.Modulo(
    chave="sistema",
    nome="Sistema",
    descricao="Servidor onde o portal corre: CPU, RAM, discos e serviços (Nginx, portal, agente), "
              "com iniciar, parar e reiniciar. Atualizado a cada 2 s pelo agente do portal.",
    acoes={
        "sistema.servicos": "Iniciar, parar e reiniciar serviços",
        "sistema.definicoes": "Definições: serviços mostrados no módulo",
        "sistema.agentes": "Agentes: alterar parâmetros (execuções em simultâneo, paralelismo, ciclos, prioridade)",
    },
    abas=[
        ("sistema.painel", "Painel", None),
        ("sistema.agentes", "Agentes", None),
        ("sistema.analise", "Análise", None),
        ("sistema.definicoes", "Definições", "sistema.definicoes"),
    ],
    cartoes={
        "sistema.recursos": {"titulo": "Monitor de recursos", "icone": "📈", "template": "sistema/c_recursos.html",
                             "link": "sistema.painel", "link_txt": "Ver o sistema"},
        "sistema.servidor": {"titulo": "Servidor", "icone": "🖥️", "template": "sistema/c_servidor.html",
                             "link": "sistema.painel", "link_txt": "Ver o painel"},
        "sistema.servicos": {"titulo": "Serviços", "icone": "⚙️", "template": "sistema/c_servicos.html",
                             "link": "sistema.painel", "link_txt": "Gerir serviços"},
        "sistema.discos": {"titulo": "Sistemas de ficheiros", "icone": "💾", "template": "sistema/c_discos.html",
                           "link": "sistema.painel", "link_txt": "Ver o sistema", "oculto": True},
    },
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
