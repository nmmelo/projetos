"""Rotas do modulo Seguranca: Auditoria, Acesso por IP, Autenticacao (2FA, fator humano, tentativas) e Registo."""
import json

from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for

from ... import auth, firewall, ldap_auth, seguranca, sistema_info, store
from . import bp, MODULO


def _quem() -> str:
    return g.user.username


@bp.route("/")
@auth.requires(MODULO.acesso)
def index():
    for ep, _r, perm in MODULO.abas:
        if not perm or g.user.can(perm):
            return redirect(url_for(ep))
    abort(403)


# ============================================================================ Auditoria
@bp.route("/auditoria")
@auth.requires("seguranca.auditoria")
def auditoria():
    return render_template("seguranca/auditoria.html", eventos=seguranca.EVENTOS, r=seguranca.resumo(),
                           ip_atual=seguranca.ip_cliente(request))


@bp.route("/auditoria/api")
@auth.requires("seguranca.auditoria")
def auditoria_api():
    q = request.args.get("q", "").strip().lower()[:100]
    r = request.args.get("r", "")
    e = request.args.get("e", "")
    sql, par = "SELECT id, quando, ip, utilizador, evento, resultado, detalhe, caminho, agente FROM seg_auditoria WHERE 1 = 1", []
    if q:
        sql += " AND (LOWER(utilizador) LIKE ? OR LOWER(detalhe) LIKE ? OR ip LIKE ? OR LOWER(caminho) LIKE ?)"
        par += [f"%{q}%"] * 4
    if e in seguranca.EVENTOS:
        sql += " AND evento = ?"
        par.append(e)
    filtro_r = sql, list(par)
    if r in ("OK", "FALHOU", "BLOQUEADO", "INFO"):
        sql += " AND resultado = ?"
        par.append(r)
    with store.tx() as t:
        linhas = t.all(sql + " ORDER BY id DESC LIMIT 500", par)
        cont = {x["resultado"]: int(x["n"]) for x in
                t.all(filtro_r[0].replace("SELECT id, quando, ip, utilizador, evento, resultado, detalhe, caminho, agente",
                                          "SELECT resultado, COUNT(*) AS n", 1) + " GROUP BY resultado", filtro_r[1])}
    return jsonify(linhas=linhas, cont=cont)


# ============================================================================ Acesso por IP
def _ctx_ip() -> dict:
    c = seguranca.cfg()
    ok_fw, msg_fw = seguranca.fw_disponivel() if c["seg_ip_modo"] == "firewalld" or request.args.get("fw") else (None, "")
    est = firewall.estado() if firewall.backend() else {}
    try:
        n_redes = len(seguranca.redes(c["seg_ip_redes"]))
    except ValueError:
        n_redes = 0
    return {"c": c, "ip_atual": seguranca.ip_cliente(request), "fw_ok": ok_fw, "fw_msg": msg_fw,
            "fw": seguranca.fw_estado(), "fw_backend": firewall.backend(), "zonas": est.get("zonas") or [],
            "zona_padrao": est.get("zona_padrao") or "", "agentes": seguranca.ips_agentes(), "n_redes": n_redes,
            "ipset": seguranca.IPSET, "instancia": sistema_info.instancia() or "<instância>",
            "pode": g.user.can("seguranca.configurar")}


@bp.route("/acesso-ip", methods=["GET", "POST"])
@auth.requires("seguranca.configurar")
def acesso_ip():
    if request.method == "POST":
        acao = request.form.get("acao", "guardar")
        try:
            if acao == "verificar":
                ip = request.form.get("ip", "").strip()
                _t, lista = seguranca.validar_redes(request.form.get("seg_ip_redes", seguranca.cfg()["seg_ip_redes"]))
                if not lista:
                    flash("A lista está vazia.", "warn")
                else:
                    flash(f"{ip}: {'tem acesso' if seguranca.permitido(ip, lista) else 'SEM acesso'}.",
                          "ok" if seguranca.permitido(ip, lista) else "error")
                return redirect(url_for("seguranca.acesso_ip"))
            if acao == "fw_remover":
                rel = seguranca.fw_remover(_quem())
                flash("Firewall: " + ("; ".join(rel) or "nada a desfazer"), "ok")
                return redirect(url_for("seguranca.acesso_ip"))
            if acao == "fw_aplicar":
                rel = seguranca.fw_aplicar(_quem())
                flash("Firewall: " + "; ".join(rel), "ok")
                return redirect(url_for("seguranca.acesso_ip"))
            _guardar_ip(request.form)
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("seguranca.acesso_ip"))
    return render_template("seguranca/acesso_ip.html", **_ctx_ip())


def _guardar_ip(f) -> None:
    texto, lista = seguranca.validar_redes(f.get("seg_ip_redes", ""))
    ativo = f.get("seg_ip_ativo") == "1"
    modo = f.get("seg_ip_modo", "codigo")
    if modo not in ("codigo", "firewalld"):
        raise ValueError("Modo inválido.")
    meu = seguranca.ip_cliente(request)
    if ativo and not lista:
        raise ValueError("Para ativar indique pelo menos um IP ou rede.")
    if ativo and not seguranca.permitido(meu, lista):
        raise ValueError(f"O seu IP ({meu}) não está na lista: ficaria sem acesso ao portal. Acrescente-o (ou a rede) primeiro.")
    msg = (f.get("seg_ip_msg") or "").strip()[:500] or seguranca.PADRAO["seg_ip_msg"]
    portas = f.get("seg_fw_portas", "80,443").strip()[:100]
    zona = f.get("seg_fw_zona", "").strip()
    if zona and not firewall.ZONA_RE.match(zona):
        raise ValueError("Zona inválida.")
    seguranca.portas({"seg_fw_portas": portas})
    ant = seguranca.cfg()
    store.save_settings({"seg_ip_ativo": "1" if ativo else "0", "seg_ip_modo": modo, "seg_ip_redes": texto,
                         "seg_ip_agentes": "1" if f.get("seg_ip_agentes") else "0", "seg_ip_msg": msg,
                         "seg_fw_portas": portas, "seg_fw_zona": zona})
    seguranca.auditar("config", "OK", ip=meu, utilizador=_quem(),
                      detalhe=f"acesso por IP {'ligado' if ativo else 'desligado'} ({modo}), {len(lista)} rede(s)")
    rel = []
    if ativo and modo == "firewalld":
        try:
            rel = seguranca.fw_aplicar(_quem())
        except ValueError as exc:
            flash(f"Guardado, mas a firewall não foi alterada: {exc} (o filtro no portal continua a aplicar-se).", "warn")
            return
    elif seguranca.fw_estado() and (not ativo or modo != "firewalld" or ant["seg_ip_modo"] != modo):
        rel = seguranca.fw_remover(_quem())
    flash("Acesso por IP guardado." + (" Firewall: " + "; ".join(rel) if rel else ""), "ok")


# ============================================================================ Autenticacao
@bp.route("/autenticacao", methods=["GET", "POST"])
@auth.requires("seguranca.configurar")
def autenticacao():
    if request.method == "POST":
        acao = request.form.get("acao", "guardar")
        try:
            if acao == "repor_2fa":
                uid = request.form.get("uid", "")
                uid = "admin" if uid == "admin" else int(uid)
                seguranca.guardar_2fa(uid, remover=True)
                seguranca.auditar("2fa", "INFO", ip=seguranca.ip_cliente(request), utilizador=_quem(),
                                  detalhe=f"dois fatores repostos de {request.form.get('nome', uid)}")
                flash("Dois fatores repostos: no próximo login o utilizador configura de novo (se obrigatório).", "ok")
            elif acao == "libertar":
                ip = request.form.get("ip", "").strip()[:64]
                lib = json.loads(store.get_setting("seg_libertar") or "{}")
                lib[ip] = store.now()
                store.save_settings({"seg_libertar": json.dumps(dict(list(lib.items())[-200:]))})
                seguranca.auditar("tentativas", "INFO", ip=ip, utilizador=_quem(), detalhe="desbloqueado manualmente")
                flash(f"{ip} desbloqueado.", "ok")
            else:
                v = {}
                if request.form.get("seg_2fa") not in seguranca.MODOS_2FA or \
                        request.form.get("seg_captcha") not in seguranca.MODOS_CAPTCHA:
                    raise ValueError("Opção inválida.")
                v["seg_2fa"] = request.form["seg_2fa"]
                v["seg_captcha"] = request.form["seg_captcha"]
                for k, lo, hi in (("seg_captcha_falhas", 1, 100), ("seg_bloqueio_falhas", 0, 1000),
                                  ("seg_bloqueio_min", 1, 1440), ("seg_auditoria_dias", 1, 3650)):
                    try:
                        n = int(request.form.get(k, ""))
                    except ValueError:
                        raise ValueError("Valores numéricos inválidos.") from None
                    if not lo <= n <= hi:
                        raise ValueError(f"Valor fora dos limites ({lo}-{hi}).")
                    v[k] = str(n)
                store.save_settings(v)
                seguranca.auditar("config", "OK", ip=seguranca.ip_cliente(request), utilizador=_quem(),
                                  detalhe=f"2FA {v['seg_2fa']}, fator humano {v['seg_captcha']}, "
                                          f"bloqueio {v['seg_bloqueio_falhas']}/{v['seg_bloqueio_min']} min")
                flash("Configuração guardada.", "ok")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("seguranca.autenticacao"))
    from ... import settings
    adm = settings.admin() or {}
    contas = [{"uid": "admin", "username": adm.get("username", "admin"), "name": adm.get("name") or "",
               "perfil": "Administrador (local)", "tem_2fa": seguranca.tem_2fa("admin"), "active": True}]
    contas += [{"uid": u["id"], "username": u["username"], "name": u.get("name") or "", "perfil": u.get("profile_name") or "",
                "tem_2fa": bool(u.get("tem_2fa")), "active": bool(u.get("active"))}
               for u in store.list_users() if (u.get("estado") or "") == ""]
    return render_template("seguranca/autenticacao.html", c=seguranca.cfg(), modos_2fa=seguranca.MODOS_2FA,
                           modos_captcha=seguranca.MODOS_CAPTCHA, contas=contas, bloqueados=seguranca.ips_bloqueados())


# ============================================================================ Registo
@bp.route("/registo", methods=["GET", "POST"])
@auth.requires("seguranca.pedidos")
def registo():
    if request.method == "POST":
        acao = request.form.get("acao", "")
        try:
            if acao == "config":
                if not g.user.can("seguranca.configurar"):
                    abort(403)
                n = request.form.get("seg_registo_notificar", "admins")
                if n not in seguranca.NOTIFICAR:
                    raise ValueError("Opção inválida.")
                ligar = request.form.get("seg_registo") == "1"
                if ligar and not auth.ldap_ativo():
                    raise ValueError("O registo precisa do LDAP / Active Directory ligado (módulo Autenticação).")
                store.save_settings({"seg_registo": "1" if ligar else "0", "seg_registo_notificar": n})
                seguranca.auditar("config", "OK", ip=seguranca.ip_cliente(request), utilizador=_quem(),
                                  detalhe=f"registo {'ligado' if ligar else 'desligado'}, notificar {n}")
                flash("Configuração do registo guardada.", "ok")
            elif acao in ("aprovar", "recusar", "apagar"):
                u = store.get_user(uid=int(request.form.get("uid", "0")))
                if not u or (u.get("estado") or "") not in ("pendente", "recusado"):
                    raise ValueError("Pedido inexistente.")
                ip = seguranca.ip_cliente(request)
                if acao == "aprovar":
                    pid = request.form.get("profile_id", "")
                    perfil = store.get_profile(int(pid)) if pid.isdigit() else None
                    if not perfil:
                        raise ValueError("Escolha o perfil a atribuir.")
                    if perfil["administrador"] and not g.user.total:
                        raise ValueError("Só um administrador pode atribuir um perfil de administrador.")
                    store.update_user_fields(u["id"], active=1, estado="", profile_id=perfil["id"])
                    seguranca.notificar_aprovado(u, perfil["name"])
                    seguranca.auditar("validacao", "OK", ip=ip, utilizador=_quem(),
                                      detalhe=f"{u['username']} aprovado com o perfil {perfil['name']}")
                    flash(f"{u['username']} aprovado (perfil {perfil['name']})"
                          + (": foi enviado um email a informar." if u.get("email") else " (sem email no diretório)."), "ok")
                elif acao == "recusar":
                    store.update_user_fields(u["id"], active=0, estado="recusado")
                    seguranca.auditar("validacao", "INFO", ip=ip, utilizador=_quem(), detalhe=f"{u['username']} recusado")
                    flash(f"Pedido de {u['username']} recusado.", "ok")
                else:
                    store.delete_user(u["id"])
                    seguranca.auditar("validacao", "INFO", ip=ip, utilizador=_quem(),
                                      detalhe=f"pedido de {u['username']} apagado (pode pedir de novo)")
                    flash(f"Pedido de {u['username']} apagado.", "ok")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("seguranca.registo"))
    todos = store.list_users()
    perfis = [p for p in store.list_profiles() if g.user.total or not p["administrador"]]
    return render_template("seguranca/registo.html", c=seguranca.cfg(), notificar=seguranca.NOTIFICAR,
                           pendentes=[u for u in todos if (u.get("estado") or "") == "pendente"],
                           recusados=[u for u in todos if (u.get("estado") or "") == "recusado"],
                           perfis=perfis, ldap_on=auth.ldap_ativo(), auto_criar=ldap_auth.config()["auto_criar"],
                           gestores=seguranca.perfis_gestor(), pode_cfg=g.user.can("seguranca.configurar"),
                           emails=_emails_notificacao())


def _emails_notificacao() -> list[str]:
    from ... import eventos
    try:
        return eventos._emails(seguranca._destino_notificacao(), "seguranca.registo", None)
    except Exception:  # noqa: BLE001
        return []
