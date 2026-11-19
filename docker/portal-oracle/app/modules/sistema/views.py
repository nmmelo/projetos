"""Rotas do modulo Sistema."""
import re

from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for

from ... import agente_info, auth, settings, sistema_info, store
from . import MODULO, bp, metricas_guardadas

FRESCO = 15        # segundos: metricas do agente mais antigas do que isto -> leitura direta


def _abas():
    return [(e, r) for e, r, p in MODULO.abas if not p or g.user.can(p)]


@bp.app_context_processor
def _ctx():
    return {"sistema_abas": _abas} if g.get("user") else {}


def _units() -> list[str]:
    return sistema_info.servicos_configurados(store.get_setting("sistema_servicos"))


@bp.route("/")
@auth.requires(MODULO.acesso)
def index():
    return redirect(url_for("sistema.painel"))


@bp.route("/painel")
@auth.requires(MODULO.acesso)
def painel():
    from ... import layouts
    return render_template("sistema/painel.html", pode_servicos=g.user.can("sistema.servicos"),
                           layout=layouts.obter("sistema", str(g.user.id)))


@bp.route("/api/estado")
@auth.requires(MODULO.acesso)
def api_estado():
    ag = agente_info.estado()
    m = metricas_guardadas()
    import time
    if not ag["ativo"] or time.time() - (m.get("ts") or 0) > FRESCO:
        # agente parado: leitura direta pelo portal (sem historico nem CPU por servico)
        m, _ = sistema_info.amostra(None, [settings.DATA_DIR])
        servs = sistema_info.estado_servicos(_units())
        for s in servs:
            s["papel"] = sistema_info.papel(s["unit"])
        m.update(historico=[], servicos=servs, backend=sistema_info.servicos_backend(), origem="portal")
    acoes = store.list_actions(15)
    return jsonify(ok=True, metricas=m, agente=ag, acoes=acoes,
                   pode_servicos=g.user.can("sistema.servicos") and ag["ativo"])


@bp.route("/servicos/acao", methods=["POST"])
@auth.requires("sistema.servicos")
def acao():
    dados = request.get_json(silent=True) or request.form
    unit, acao_ = dados.get("unit", ""), dados.get("acao", "")
    if unit not in _units() or acao_ not in sistema_info.ACOES:
        return jsonify(ok=False, error="Serviço ou ação inválida."), 400
    if not agente_info.ativo():
        return jsonify(ok=False, error="O agente do portal está parado: as ações sobre serviços são feitas por ele."), 409
    aid = store.add_action(unit, acao_, g.user.username)
    return jsonify(ok=True, id=aid)


@bp.route("/definicoes", methods=["GET", "POST"])
@auth.requires("sistema.definicoes")
def definicoes():
    if request.method == "POST":
        texto = request.form.get("servicos", "")
        lista = [u for u in re.split(r"[\s,]+", texto) if u]
        maus = [u for u in lista if not sistema_info.UNIT_RE.match(u)]
        if maus:
            flash(f"Nomes inválidos: {', '.join(maus)}", "error")
        else:
            store.save_settings({"sistema_servicos": " ".join(dict.fromkeys(lista))})
            flash("Serviços guardados." if lista else "Lista vazia: são usados os serviços por omissão.", "ok")
            return redirect(url_for("sistema.definicoes"))
    atual = store.get_setting("sistema_servicos") or ""
    return render_template("sistema/definicoes.html", atual=atual, padrao=sistema_info.servicos_padrao(),
                           backend=sistema_info.servicos_backend(), instancia=sistema_info.instancia(),
                           units=_units())
