"""Modulo Oracle: tablespaces, shrink, execucoes e definicoes (bases de dados Oracle)."""
from flask import Blueprint, abort, g

from ... import modulos, store

bp = Blueprint("oracle", __name__, url_prefix="/oracle", template_folder="templates")


def _home_context(user) -> dict:
    dbs = user.visible_dbs(store.list_dbs())
    visible = {d["id"] for d in dbs}
    jobs = [j for j in store.list_jobs(500) if j["db_id"] in visible]
    since = store.ago(hours=24)
    stats = {
        "dbs": len(dbs),
        "running": sum(1 for j in jobs if j["status"] == "EM CURSO"),
        "last24": sum(1 for j in jobs if j["started_at"] >= since),
        "errors24": sum(1 for j in jobs if j["started_at"] >= since and j["status"] in ("ERRO", "INTERROMPIDO")),
    }
    return {"dbs": dbs, "stats": stats, "recent": jobs[:8]}


MODULO = modulos.registar(modulos.Modulo(
    chave="oracle",
    nome="Oracle",
    descricao="Bases de dados Oracle: relatório de tablespaces, shrink de datafiles e histórico de execuções.",
    acoes={
        "oracle.tablespaces": "Ver tablespaces (tamanho, usado, livre)",
        "oracle.shrink.analise": "Shrink — modo ANÁLISE",
        "oracle.shrink.executar": "Shrink — modo EXECUTAR (e cancelar execuções)",
        "oracle.execucoes.apagar": "Apagar execuções terminadas (do histórico)",
        "oracle.definicoes": "Definições: gerir as bases de dados Oracle",
    },
    abas=[
        ("oracle.report", "Tablespaces", "oracle.tablespaces"),
        ("oracle.shrink", "Shrink", ("oracle.shrink.analise", "oracle.shrink.executar")),
        ("oracle.jobs_list", "Execuções", None),
        ("oracle.cfg_dbs", "Definições", "oracle.definicoes"),
    ],
    home_template="oracle/_home.html",
    home_context=_home_context,
))


@bp.before_request
def _guard():
    """Modulo desativado -> 404; sem acesso ao modulo -> 403 (o login e tratado nas rotas)."""
    if MODULO.chave not in modulos.ativos():
        abort(404)
    if g.get("user") and not g.user.can(MODULO.acesso):
        abort(403)


from . import views  # noqa: E402,F401  (regista as rotas)
