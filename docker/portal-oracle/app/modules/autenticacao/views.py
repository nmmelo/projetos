"""Rotas do modulo Autenticacao: LDAP (configurar, testar) e Acessos (registo dos logins)."""
from flask import flash, g, jsonify, redirect, render_template, request, url_for

from ... import auth, crypto, ldap_auth, store
from . import bp, MODULO


@bp.route("/")
@auth.login_required
def index():
    return redirect(url_for("autenticacao.ldap"))


def contagens() -> dict:
    with store.tx() as t:
        rows = t.all("SELECT COALESCE(auth_tipo, 'local') AS tipo, active, COUNT(*) AS n FROM users "
                     "GROUP BY COALESCE(auth_tipo, 'local'), active")
    c = {"local": 0, "ldap": 0, "local_inativos": 0, "ldap_inativos": 0}
    for r in rows:
        tipo = r["tipo"] if r["tipo"] in ("local", "ldap") else "local"
        c[tipo if r["active"] else f"{tipo}_inativos"] += int(r["n"])
    return c


def guardar_form(form) -> None:
    """Valida e guarda a configuracao LDAP do formulario."""
    drv = form.get("ldap_driver", "openldap")
    if drv not in ldap_auth.DRIVERS:
        raise ValueError("Driver inválido.")
    seg = form.get("ldap_seguranca", "starttls")
    if seg not in ldap_auth.SEGURANCA:
        raise ValueError("Segurança inválida.")
    try:
        porta = int(form.get("ldap_porta") or (636 if seg == "ldaps" else 389))
        timeout = int(form.get("ldap_timeout") or 10)
    except ValueError:
        raise ValueError("Porta ou timeout inválidos.") from None
    if not 1 <= porta <= 65535 or not 2 <= timeout <= 120:
        raise ValueError("Porta (1-65535) ou timeout (2-120 s) fora dos limites.")
    filtro = form.get("ldap_filtro", "").strip()
    if filtro and "{u}" not in filtro:
        raise ValueError("O filtro tem de ter {u} no lugar do nome do utilizador.")
    hosts = form.get("ldap_hosts", "").strip()
    ativo = form.get("ldap_ativo") == "1"
    if ativo and (not hosts or not form.get("ldap_base_dn", "").strip()):
        raise ValueError("Para ligar o LDAP indique o servidor e a base de procura.")
    v = {"ldap_driver": drv, "ldap_seguranca": seg, "ldap_porta": str(porta), "ldap_timeout": str(timeout),
         "ldap_hosts": hosts[:500], "ldap_bind_dn": form.get("ldap_bind_dn", "").strip()[:500],
         "ldap_base_dn": form.get("ldap_base_dn", "").strip()[:500], "ldap_filtro": filtro[:500],
         "ldap_attr_nome": form.get("ldap_attr_nome", "").strip()[:60],
         "ldap_attr_email": form.get("ldap_attr_email", "").strip()[:60],
         "ldap_grupo": form.get("ldap_grupo", "").strip()[:500],
         "ldap_verificar": "1" if form.get("ldap_verificar") else "0",
         "ldap_auto_criar": "1" if form.get("ldap_auto_criar") else "0",
         "ldap_atualizar_dados": "1" if form.get("ldap_atualizar_dados") else "0",
         "ldap_perfil": form.get("ldap_perfil", ""), "ldap_ativo": "1" if ativo else "0"}
    pw = form.get("ldap_bind_password", "")
    if pw:
        v["ldap_bind_password_enc"] = crypto.encrypt(pw)
    elif form.get("ldap_bind_password_limpar"):
        v["ldap_bind_password_enc"] = ""
    ca = form.get("ldap_ca_pem", "").strip()
    if ca:
        ldap_auth.validar_ca(ca)
        v["ldap_ca_pem"] = ca
    elif form.get("ldap_ca_remover"):
        v["ldap_ca_pem"] = ""
    store.save_settings(v)


def _form_para_config(form) -> dict:
    """Configuracao do formulario (para testar antes de guardar); campos vazios usam os guardados."""
    s = store.get_settings()
    d = {k: form.get(k, s.get(k, "")) for k in ldap_auth.CAMPOS}
    d["ldap_verificar"] = "1" if form.get("ldap_verificar") else "0"
    d["ldap_ca_pem"] = form.get("ldap_ca_pem", "").strip() or ("" if form.get("ldap_ca_remover") else s.get("ldap_ca_pem", ""))
    d["ldap_bind_password_enc"] = crypto.encrypt(form["ldap_bind_password"]) if form.get("ldap_bind_password") \
        else s.get("ldap_bind_password_enc", "")
    return d


def contexto() -> dict:
    s = store.get_settings()
    ok, falta = ldap_auth.disponivel()
    ca = s.get("ldap_ca_pem") or ""
    try:
        ca_info = ldap_auth.validar_ca(ca) if ca else ""
    except ValueError as exc:
        ca_info = str(exc)
    return {"s": s, "c": ldap_auth.config(s), "drivers": ldap_auth.DRIVERS, "seguranca": ldap_auth.SEGURANCA,
            "padrao": ldap_auth.PADRAO, "perfis": store.list_profiles(), "lib_ok": ok, "lib_falta": falta,
            "tem_pw": bool(s.get("ldap_bind_password_enc")), "ca_info": ca_info, "cont": contagens()}


@bp.route("/ldap", methods=["GET", "POST"])
@auth.requires(MODULO.acesso)
def ldap():
    pode = g.user.can("autenticacao.configurar")
    if request.method == "POST":
        if not pode:
            return redirect(url_for("autenticacao.ldap"))
        try:
            guardar_form(request.form)
            flash("Configuração LDAP guardada.", "ok")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(request.form.get("voltar") or url_for("autenticacao.ldap"))
    return render_template("autenticacao/ldap.html", pode=pode, **contexto())


@bp.route("/ldap/testar", methods=["POST"])
@auth.requires("autenticacao.configurar")
def testar():
    r = ldap_auth.testar(_form_para_config(request.form), request.form.get("teste_utilizador", "").strip(),
                         request.form.get("teste_password", ""))
    ldap_auth.registar_acesso(request.form.get("teste_utilizador", "").strip() or "(teste)", "teste", r["ok"],
                              r["msg"], request.remote_addr or "")
    return jsonify(r)


@bp.route("/acessos")
@auth.requires("autenticacao.acessos")
def acessos():
    return render_template("autenticacao/acessos.html")


@bp.route("/acessos/api")
@auth.requires("autenticacao.acessos")
def acessos_api():
    q = request.args.get("q", "").strip().lower()
    r = request.args.get("r", "")
    sql, par = "SELECT * FROM auth_log WHERE 1 = 1", []
    if q:
        sql += " AND (LOWER(utilizador) LIKE ? OR LOWER(detalhe) LIKE ? OR ip LIKE ?)"
        par += [f"%{q}%"] * 3
    if r in ("OK", "FALHOU"):
        sql += " AND resultado = ?"
        par.append(r)
    with store.tx() as t:
        linhas = t.all(sql + " ORDER BY id DESC LIMIT 500", par)
        cont = {x["resultado"]: int(x["n"]) for x in t.all("SELECT resultado, COUNT(*) AS n FROM auth_log GROUP BY resultado")}
    return jsonify(linhas=linhas, cont=cont)
