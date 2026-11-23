"""Rotas do modulo Sistema."""
import re

from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for

from ... import agente_info, agentes, auth, settings, sistema_info, store
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
    aut = sistema_info.autorizados()
    for s in m.get("servicos", []):
        s["autorizado"] = aut is None or s["unit"] in aut
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
    aut = sistema_info.autorizados()
    if aut is not None and unit not in aut:
        nome = sistema_info.instancia() or "<nome>"
        return jsonify(ok=False, error=f"O agente não tem permissão para gerir '{unit}' (sudoers). No servidor: "
                                       f"sudo instalar.sh --servicos {nome} +{unit}"), 403
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
    aut = sistema_info.autorizados()
    return render_template("sistema/definicoes.html", atual=atual, padrao=sistema_info.servicos_padrao(),
                           backend=sistema_info.servicos_backend(), instancia=sistema_info.instancia(),
                           units=_units(), instalados=sistema_info.servicos_instalados(), aut=aut)


# ============================================================ Agentes e Analise (recursos do portal)
def _periodo() -> tuple[int, int, str]:
    """?r=1h|5h|24h|7d|30d ou ?desde=AAAA-MM-DDTHH:MM&dur=1h..30d -> (desde, ate, rotulo)."""
    import time
    from datetime import datetime
    r = request.args.get("r") or "24h"
    dur = agentes.INTERVALOS.get(request.args.get("dur") or r, 86400)
    if request.args.get("desde"):
        try:
            ini = int(datetime.strptime(request.args["desde"][:16], "%Y-%m-%dT%H:%M").timestamp())
            return ini, min(ini + dur, int(time.time())), "data"
        except ValueError:
            pass
    agora = int(time.time())
    return agora - dur, agora, r


@bp.route("/agentes", endpoint="agentes")
@auth.requires(MODULO.acesso)
def agentes_pag():
    a = request.args.get("a", "agente_oracle")
    if a not in agentes.AGENTES:
        abort(404)
    est = agente_info.estado(a)
    procs = [p for p in agentes.processos_atuais().get("processos", []) if p["comp"] == a]
    params = [{**p, "valor": agentes.param(a, p["chave"])} for p in agentes.AGENTES[a]["params"]]
    em_curso = []
    if est.get("execucoes"):
        with store.tx() as t:
            ids = [int(x) for x in est["execucoes"]][:50]
            if ids:
                em_curso = t.all(f"SELECT id, db_name, params, progress, current_action, started_at FROM jobs WHERE id IN "
                                 f"({','.join('?' * len(ids))})", tuple(ids))
        import json as _j
        for j in em_curso:
            j["params"] = _j.loads(j["params"])
    return render_template("sistema/agentes.html", a=a, ag=agentes.AGENTES[a], todos=agentes.AGENTES, est=est,
                           procs=procs, params=params, recursos=agentes.recursos_servico(a), em_curso=em_curso,
                           instancia=sistema_info.instancia(), unit=agentes.unit(a),
                           pode=g.user.can("sistema.agentes"), comps=agentes.COMPONENTES,
                           estados={k: agente_info.estado(k)["ativo"] for k in agentes.AGENTES})


@bp.route("/agentes/<a>/parametros", methods=["POST"])
@auth.requires("sistema.agentes")
def agentes_parametros(a):
    if a not in agentes.AGENTES:
        abort(404)
    erros = agentes.guardar_params(a, request.form)
    if erros:
        flash(" ".join(erros), "error")
    else:
        flash("Parâmetros guardados. Os de aplicação imediata contam no próximo ciclo; a prioridade, ao reiniciar o agente.", "ok")
    return redirect(url_for("sistema.agentes", a=a))


@bp.route("/analise")
@auth.requires(MODULO.acesso)
def analise():
    return render_template("sistema/analise.html", comps=agentes.COMPONENTES, agentes=agentes.AGENTES,
                           estados={k: agente_info.estado(k) for k in agentes.AGENTES})


@bp.route("/api/processos")
@auth.requires(MODULO.acesso)
def api_processos():
    import time
    p = agentes.processos_atuais()
    return jsonify(ok=True, idade=int(time.time() - (p.get("ts") or 0)) if p.get("ts") else None, **p,
                   nomes=agentes.COMPONENTES, agente=agente_info.ativo())


@bp.route("/api/metricas")
@auth.requires(MODULO.acesso)
def api_metricas():
    comps = [c for c in (request.args.get("comp") or "").split(",") if c in agentes.COMPONENTES]
    desde, ate, rot = _periodo()
    return jsonify(ok=True, **agentes.serie(comps or list(agentes.COMPONENTES), desde, ate), rotulo=rot,
                   nomes=agentes.COMPONENTES)
