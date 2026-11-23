"""Resumo do setup do portal (Configuracao -> Setup): identidade, ficheiros e chaves,
permissoes, armazenamento, servicos e runtime. So leitura; sugere comandos de correcao."""
import grp
import os
import platform
import pwd
import stat
import sys
import time

from . import settings, sistema_info, store

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ESTADO_DIR = "/etc/portal-oracle"

# (rotulo, nome em DATA_DIR, usado por, modo esperado, e diretorio?)
FICHEIROS = [
    ("Pasta de dados", "", "portal e agentes", 0o700, True),
    ("Administrador local", "admin.json", "início de sessão do administrador", 0o600, False),
    ("Armazenamento", "portal.json", "ligação à base de dados do portal", 0o600, False),
    ("Chave de cifra", "fernet.key", "passwords cifradas (BDs, LDAP, SMTP, clientes)", 0o600, False),
    ("Chave das sessões", "flask_secret.key", "cookies de sessão", 0o600, False),
    ("Chaves SSH dos clientes", "clientes_ssh", "módulo Clientes", 0o700, True),
    ("Chave SSH privada", "clientes_ssh/id_ed25519", "módulo Clientes", 0o600, False),
    ("Base de dados SQLite", "portal.sqlite3", "armazenamento SQLite", 0o600, False),
    ("Backups", "backups", "Configuração → Backups", 0o700, True),
]


def _dono(st) -> str:
    try:
        u = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        u = str(st.st_uid)
    try:
        g = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        g = str(st.st_gid)
    return f"{u}:{g}"


def _utilizador() -> str:
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return str(os.geteuid())


def ficheiros() -> list[dict]:
    eu = _utilizador()
    res = []
    for rot, nome, uso, modo, e_dir in FICHEIROS:
        p = settings.path(nome) if nome else settings.DATA_DIR
        d = {"rotulo": rot, "caminho": p, "uso": uso, "esperado": f"{modo:o}", "dono": "", "modo": "",
             "estado": "ok", "nota": "", "corrigir": ""}
        try:
            st = os.stat(p)
        except FileNotFoundError:
            opcional = nome in ("portal.sqlite3", "clientes_ssh", "clientes_ssh/id_ed25519", "backups", "portal.json")
            d.update(estado="info" if opcional else "erro",
                     nota="ainda não existe (criado quando for preciso)" if opcional else "em falta")
            res.append(d)
            continue
        except PermissionError:
            d.update(estado="erro", nota="sem permissão de leitura")
            res.append(d)
            continue
        m = stat.S_IMODE(st.st_mode)
        d.update(dono=_dono(st), modo=f"{m:o}")
        if nome == "portal.sqlite3" and settings.storage_config().get("type") != "sqlite":
            d.update(estado="info", nota="não usado (armazenamento externo)")
        problemas = []
        if m & 0o077 and m != modo:
            problemas.append(f"modo {m:o} (esperado {modo:o})")
        if d["dono"].split(":")[0] != eu:
            problemas.append(f"dono diferente do utilizador de serviço ({eu})")
        if e_dir != stat.S_ISDIR(st.st_mode):
            problemas.append("tipo inesperado")
        if problemas:
            d.update(estado="aviso", nota="; ".join(problemas),
                     corrigir=f"sudo chown {eu}: '{p}' && sudo chmod {modo:o} '{p}'")
        res.append(d)
    return res


def estado_instalador(nome: str) -> dict:
    """Snapshot gravado pelo instalar.sh (/etc/portal-oracle/<nome>.conf, 600 root)."""
    p = os.path.join(ESTADO_DIR, f"{nome or 'portal-oracle'}.conf")
    info = {"caminho": p, "legivel": False, "existe": os.path.exists(p), "valores": [], "alterado": ""}
    try:
        with open(p) as fh:
            linhas = fh.read().splitlines()
        info["legivel"] = True
        info["alterado"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.stat(p).st_mtime))
        for l in linhas:
            if "=" in l and not l.lstrip().startswith("#"):
                k, v = l.split("=", 1)
                v = v.strip().strip("'\"")
                if any(s in k.upper() for s in ("PASS", "SENHA", "SEGREDO", "TOKEN", "HASH")):
                    v = "••••••" if v else ""
                info["valores"].append((k.strip(), v))
    except (PermissionError, FileNotFoundError, IsADirectoryError):
        pass
    return info


def armazenamento() -> dict:
    s = store.get()
    d = {"descricao": s.describe(), "tipo": settings.STORAGE_TYPES.get(s.kind, s.kind), "ok": False, "erro": "",
         "registos": None, "ms": None}
    t0 = time.time()
    try:
        with s.tx() as t:
            d["registos"] = sum(t.val(f"SELECT COUNT(*) FROM {tb}") for tb in store.TABLES)
        d["ok"] = True
    except Exception as exc:   # noqa: BLE001
        d["erro"] = str(exc)
    d["ms"] = int((time.time() - t0) * 1000)
    return d


def _versao(mod: str) -> str:
    try:
        from importlib.metadata import version
        return version(mod)
    except Exception:   # noqa: BLE001
        return "—"


def runtime() -> list[tuple[str, str]]:
    return [
        ("Python", f"{platform.python_version()} ({sys.executable})"),
        ("Flask", _versao("flask")), ("Gunicorn", _versao("gunicorn")), ("oracledb", _versao("oracledb")),
        ("psutil", _versao("psutil")), ("ldap3", _versao("ldap3")), ("cryptography", _versao("cryptography")),
        ("Sistema", f"{platform.system()} {platform.release()} ({platform.machine()})"),
        ("Servidor", platform.node()),
        ("Fuso horário", f"{time.strftime('%Z %z')} — {os.environ.get('TZ') or 'do sistema'}"),
        ("Hora do servidor", time.strftime("%Y-%m-%d %H:%M:%S")),
    ]


def identidade() -> list[tuple[str, str]]:
    nome = sistema_info.instancia()
    docker = os.path.exists("/.dockerenv") or sistema_info.servicos_backend() == "supervisor"
    return [
        ("Instância", nome or "— (sem PORTAL_INSTANCIA)"),
        ("Modo", "Docker (supervisord)" if docker else
         ("systemd" if sistema_info.servicos_backend() == "systemd" else "manual")),
        ("Pasta do portal", BASE),
        ("Pasta de dados", settings.DATA_DIR),
        ("Utilizador de serviço", f"{_utilizador()} (uid {os.geteuid()})"),
        ("Processo", f"pid {os.getpid()}"),
    ]


def servicos() -> list[dict]:
    nome = sistema_info.instancia() or "portal-oracle"
    if sistema_info.servicos_backend() == "supervisor":
        units = ["nginx", "gunicorn", "agente", "agente-oracle"]
    else:
        units = [nome, f"{nome}-agente", f"{nome}-agente-oracle", "nginx"]
    return sistema_info.estado_servicos(units)


def sudoers(nome: str) -> list[dict]:
    res = []
    for f in (f"/etc/sudoers.d/{nome}-agente", f"/etc/sudoers.d/{nome}-firewall"):
        res.append({"caminho": f, "existe": os.path.exists(f)})
    return res


def comandos(nome: str) -> list[tuple[str, str]]:
    inst = f"{BASE}/instalar.sh"
    return [
        ("Configuração guardada", f"sudo {inst} --mostrar {nome}"),
        ("Atualizar (git pull + dependências + serviços)", f"sudo {inst} --atualizar {nome}"),
        ("Verificar a instalação", f"sudo {inst} --verificar {nome}"),
        ("Reaplicar configuração (sudoers, units, nginx)", f"sudo {inst} --reaplicar {nome}"),
        ("Listar instâncias", f"sudo {inst} --lista"),
        ("Recursos do agente Oracle", f"sudo {inst} --agente-recursos {nome} agente-oracle cpu=200% mem=2G nice=5"),
        ("Registo do portal", f"sudo journalctl -u {nome} -n 100 --no-pager"),
        ("Registo do agente Oracle", f"sudo journalctl -u {nome}-agente-oracle -n 100 --no-pager"),
        ("Corrigir permissões da pasta de dados",
         f"sudo chown -R {_utilizador()}: '{settings.DATA_DIR}' && sudo chmod 700 '{settings.DATA_DIR}'"),
    ]


def resumo() -> dict:
    nome = sistema_info.instancia() or "portal-oracle"
    fic = ficheiros()
    return {"nome": nome, "identidade": identidade(), "ficheiros": fic,
            "problemas": sum(1 for f in fic if f["estado"] in ("aviso", "erro")),
            "instalador": estado_instalador(nome), "armazenamento": armazenamento(), "runtime": runtime(),
            "servicos": servicos(), "sudoers": sudoers(nome), "comandos": comandos(nome),
            "chave_env": bool(os.environ.get("PORTAL_FERNET_KEY"))}
