"""Núcleo do portal: base de dados SQLite, Docker (socket), HTTP API do Watchtower e sincronização."""
import http.client
import json
import logging
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

DATA = os.environ.get("DATA_PATH", "/data")
DB_DIR = os.path.join(DATA, "db")
LOG_DIR = os.path.join(DATA, "logs")
DB_PATH = os.path.join(DB_DIR, "watchtower-ui.db")
DOCKER_SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")

WT_HOST = os.environ.get("WATCHTOWER_HOST", "watchtower")
WT_PORT = os.environ.get("WATCHTOWER_PORT", "8080")
WT_TOKEN = os.environ.get("WATCHTOWER_HTTP_API_TOKEN", "")
WT_CONTAINER = os.environ.get("WATCHTOWER_CONTAINER", WT_HOST)
WEBHOOK_TOKEN = os.environ.get("WEBHOOK_TOKEN", "")
RETENTION_DAYS = int(os.environ.get("LOG_RETENTION_DAYS", "90") or 90)
SYNC_INTERVAL = max(15, int(os.environ.get("SYNC_INTERVAL", "60") or 60))
MANUAL_WINDOW = 90  # segundos depois do fim de um update manual em que as mudanças contam como manuais

log = logging.getLogger("portal")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'leitura',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, username TEXT, ip TEXT, action TEXT NOT NULL, detail TEXT
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit(ts);
CREATE TABLE IF NOT EXISTS login_attempts (ip TEXT NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS login_attempts_ip ON login_attempts(ip, ts);
CREATE TABLE IF NOT EXISTS containers (
    name TEXT PRIMARY KEY,
    image TEXT, version TEXT, image_id TEXT, digest TEXT,
    state TEXT, status TEXT, first_seen TEXT, last_seen TEXT, last_update TEXT,
    wt_state TEXT, wt_checked TEXT
);
CREATE TABLE IF NOT EXISTS updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    container TEXT NOT NULL, ts TEXT NOT NULL,
    from_version TEXT, from_image_id TEXT, to_version TEXT, to_image_id TEXT,
    source TEXT, result TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS updates_container ON updates(container, ts);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, scanned INTEGER, updated INTEGER, failed INTEGER, raw TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS wt_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, level TEXT, msg TEXT
);
CREATE INDEX IF NOT EXISTS wt_logs_ts ON wt_logs(ts);
"""


# ---------------------------------------------------------------- utilitários
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        fh = RotatingFileHandler(os.path.join(LOG_DIR, "portal.log"), maxBytes=5_000_000, backupCount=3)
        fh.setFormatter(fmt)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(fh)
        root.addHandler(sh)
        root.setLevel(logging.INFO)


def db():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    return con


def q(sql, args=(), one=False):
    con = db()
    try:
        rows = con.execute(sql, args).fetchall()
        con.commit()
        return (rows[0] if rows else None) if one else rows
    finally:
        con.close()


def ex(sql, args=()):
    con = db()
    try:
        cur = con.execute(sql, args)
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def meta_get(key, default=None):
    r = q("SELECT value FROM meta WHERE key=?", (key,), one=True)
    return r["value"] if r else default


def meta_set(key, value):
    ex("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def secret_key():
    path = os.path.join(DB_DIR, ".secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(path, 0o600)
    with open(path) as f:
        return f.read().strip()


def audit(user, ip, action, detail=""):
    ex("INSERT INTO audit(ts,username,ip,action,detail) VALUES(?,?,?,?,?)", (now(), user, ip, action, detail))
    log.info("auditoria: %s | %s | %s %s", user or "-", ip or "-", action, detail)


def init_db(hash_fn):
    os.makedirs(DB_DIR, exist_ok=True)
    con = db()
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        cols = {r[1] for r in con.execute("PRAGMA table_info(containers)")}
        for col in ("wt_state", "wt_checked"):
            if col not in cols:
                con.execute(f"ALTER TABLE containers ADD COLUMN {col} TEXT")
        con.commit()
    finally:
        con.close()
    if not q("SELECT 1 FROM users LIMIT 1"):
        user = os.environ.get("ADMIN_USER", "admin").strip() or "admin"
        pwd = os.environ.get("ADMIN_PASSWORD", "")
        if not pwd:
            pwd = secrets.token_urlsafe(12)
            log.warning("ADMIN_PASSWORD não definido: password inicial de '%s' = %s (mudar no portal)", user, pwd)
        ex("INSERT INTO users(username,password_hash,role,active,created_at) VALUES(?,?,?,?,?)",
           (user, hash_fn(pwd), "admin", 1, now()))
        audit("sistema", "", "Utilizador criado", f"{user} (admin inicial)")


# ---------------------------------------------------------------- Docker (socket)
class _UnixConn(http.client.HTTPConnection):
    def __init__(self, sock_path, timeout=10):
        super().__init__("localhost", timeout=timeout)
        self.sock_path = sock_path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self.sock_path)
        self.sock = s


def docker_get(path, raw=False, timeout=10):
    c = _UnixConn(DOCKER_SOCK, timeout)
    try:
        c.request("GET", path)
        r = c.getresponse()
        data = r.read()
        if r.status >= 400:
            raise RuntimeError(f"Docker API {r.status}: {data[:200].decode(errors='replace')}")
        return data if raw else json.loads(data)
    finally:
        c.close()


def docker_ok():
    return os.path.exists(DOCKER_SOCK)


def _demux(data):
    """Separa o stream multiplexado dos logs do Docker (cabeçalho de 8 bytes por bloco)."""
    if len(data) < 8 or data[0] not in (0, 1, 2) or data[1:4] != b"\x00\x00\x00":
        return data.decode(errors="replace")
    out, i = [], 0
    while i + 8 <= len(data):
        size = int.from_bytes(data[i + 4:i + 8], "big")
        out.append(data[i + 8:i + 8 + size])
        i += 8 + size
    return b"".join(out).decode(errors="replace")


def docker_logs(name, tail=300):
    data = docker_get(f"/containers/{name}/logs?stdout=1&stderr=1&timestamps=1&tail={int(tail)}", raw=True)
    return _demux(data).splitlines()


def _tag(ref):
    if ref.startswith("sha256:"):
        return ""
    last = ref.rsplit("/", 1)[-1]
    return last.split(":", 1)[1] if ":" in last else "latest"


def short_id(image_id):
    return (image_id or "").split(":")[-1][:12]


# ---------------------------------------------------------------- sincronização
_sync_lock = threading.Lock()


def manual_source():
    try:
        m = json.loads(meta_get("manual", "") or "{}")
    except ValueError:
        return None
    if m and time.time() <= m.get("until", 0):
        return f"Manual ({m.get('user', '?')})"
    return None


def sync_containers():
    """Lê os contentores no Docker e regista as mudanças de imagem (= atualizações). Sem socket devolve None."""
    if not docker_ok():
        return None
    with _sync_lock:
        lista = docker_get("/containers/json?all=1")
        imgs, vistos, ts = {}, set(), now()
        for c in lista:
            name = (c.get("Names") or ["/?"])[0].lstrip("/")
            ref, image_id = c.get("Image", ""), c.get("ImageID", "")
            if image_id not in imgs:
                try:
                    imgs[image_id] = docker_get(f"/images/{image_id}/json")
                except Exception:
                    imgs[image_id] = {}
            img = imgs[image_id]
            labels = (img.get("Config") or {}).get("Labels") or {}
            if ref.startswith("sha256:"):
                ref = ((img.get("RepoTags") or [""])[0]) or ref
            version = labels.get("org.opencontainers.image.version") or _tag(ref) or short_id(image_id)
            digest = ((img.get("RepoDigests") or [""])[0]).split("@")[-1]
            vistos.add(name)
            old = q("SELECT * FROM containers WHERE name=?", (name,), one=True)
            if old is None:
                ex("""INSERT INTO containers(name,image,version,image_id,digest,state,status,first_seen,last_seen,last_update)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   (name, ref, version, image_id, digest, c.get("State"), c.get("Status"), ts, ts, None))
                ex("""INSERT INTO updates(container,ts,to_version,to_image_id,source,result)
                      VALUES(?,?,?,?,?,?)""", (name, ts, version, image_id, "Registo inicial", "Registado"))
                continue
            if old["image_id"] != image_id:
                src = manual_source() or "Watchtower"
                ex("""INSERT INTO updates(container,ts,from_version,from_image_id,to_version,to_image_id,source,result)
                      VALUES(?,?,?,?,?,?,?,?)""",
                   (name, ts, old["version"], old["image_id"], version, image_id, src, "OK"))
                log.info("atualização detetada: %s %s -> %s (%s)", name, old["version"], version, src)
                ex("UPDATE containers SET last_update=? WHERE name=?", (ts, name))
            ex("""UPDATE containers SET image=?,version=?,image_id=?,digest=?,state=?,status=?,last_seen=?
                  WHERE name=?""", (ref, version, image_id, digest, c.get("State"), c.get("Status"), ts, name))
        for r in q("SELECT name FROM containers WHERE state != 'removido'"):
            if r["name"] not in vistos:
                ex("UPDATE containers SET state='removido', status='Não encontrado no Docker' WHERE name=?", (r["name"],))
        meta_set("last_sync", ts)
        return len(vistos)


# ---------------------------------------------------------------- Watchtower HTTP API
def wt_request(path, method="GET", timeout=10):
    req = urllib.request.Request(f"http://{WT_HOST}:{WT_PORT}{path}", method=method,
                                 headers={"Authorization": f"Bearer {WT_TOKEN}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode(errors="replace")


def wt_metrics():
    """Devolve (dict de métricas, erro)."""
    try:
        _, text = wt_request("/v1/metrics", timeout=5)
    except urllib.error.HTTPError as e:
        return {}, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return {}, str(getattr(e, "reason", e))
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([a-zA-Z_:][\w:]*)(\{[^}]*\})?\s+(\S+)", line)
        if m and not line.startswith("#"):
            try:
                out[m.group(1)] = int(float(m.group(3)))
            except ValueError:
                pass
    return out, None


_update_lock = threading.Lock()
update_state = {"running": False, "since": None, "user": None, "images": ""}


def wt_update_async(images, user, ip):
    """Pede um update ao Watchtower numa thread. Devolve False se já houver um a decorrer."""
    if not _update_lock.acquire(blocking=False):
        return False
    update_state.update(running=True, since=now(), user=user, images=images)
    meta_set("manual", json.dumps({"user": user, "until": time.time() + 1800}))

    def run():
        try:
            path = "/v1/update" + (f"?image={urllib.request.quote(images, safe=',')}" if images else "")
            t0 = time.time()
            status, _ = wt_request(path, method="POST", timeout=900)
            audit(user, ip, "Update concluído", f"{images or 'todos'} · HTTP {status} · {time.time() - t0:.0f} s")
        except urllib.error.HTTPError as e:
            audit(user, ip, "Update falhou", f"{images or 'todos'} · HTTP {e.code}")
        except Exception as e:  # noqa: BLE001
            audit(user, ip, "Update falhou", f"{images or 'todos'} · {getattr(e, 'reason', e)}")
        finally:
            meta_set("manual", json.dumps({"user": user, "until": time.time() + MANUAL_WINDOW}))
            try:
                sync_containers()
            except Exception as e:  # noqa: BLE001
                log.warning("sincronização após update falhou: %s", e)
            update_state.update(running=False)
            _update_lock.release()

    threading.Thread(target=run, daemon=True).start()
    return True


# ---------------------------------------------------------------- webhook (relatório do Watchtower)
WT_STATES = {"updated": "Atualizado", "failed": "Falhou", "fresh": "Sem alterações", "skipped": "Ignorado",
             "stale": "Desatualizado", "scanned": "Verificado"}


def _record_seen(name, image, image_id, ts):
    """Garante o registo do contentor; devolve a linha anterior (ou None se é novo)."""
    old = q("SELECT * FROM containers WHERE name=?", (name,), one=True)
    if old is None:
        version = _tag(image) or short_id(image_id)
        ex("""INSERT INTO containers(name,image,version,image_id,first_seen,last_seen)
              VALUES(?,?,?,?,?,?)""", (name, image, version, image_id, ts, ts))
        ex("""INSERT INTO updates(container,ts,to_version,to_image_id,source,result)
              VALUES(?,?,?,?,?,?)""", (name, ts, version, image_id, "Registo inicial", "Registado"))
    return old


def handle_report(body):
    """Relatório JSON do Watchtower (ver watchtower-exemplo.yml): todos os contentores verificados + logs da sessão."""
    text = body.decode(errors="replace").strip()
    data = None
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "message" in data and "scanned" not in data:
            data = json.loads(data["message"])  # template=json do shoutrrr
    except (ValueError, TypeError):
        data = None
    ts = now()
    if not isinstance(data, dict):
        ex("INSERT INTO reports(ts,raw) VALUES(?,?)", (ts, text[:4000]))
        log.info("webhook: mensagem não estruturada recebida (%d bytes)", len(text))
        return
    items = [c for c in data.get("containers", []) if c]
    # formato antigo: só listas updated/failed
    items += [dict(c, state="updated") for c in data.get("updated", []) if c]
    items += [dict(c, state="failed") for c in data.get("failed", []) if c]
    n_upd = n_fail = 0
    for c in items:
        name = (c.get("name") or "?").lstrip("/")
        image = c.get("image") or ""
        state = (c.get("state") or "").lower()
        cur_id, new_id = c.get("from") or "", c.get("to") or ""
        old = _record_seen(name, image, cur_id or new_id, ts)
        if old is None:
            old = q("SELECT * FROM containers WHERE name=?", (name,), one=True)
        version_new = _tag(image) or short_id(new_id)
        src = manual_source() or "Watchtower"
        if state == "updated":
            n_upd += 1
            if new_id and old["image_id"] != new_id:  # pode já ter sido registado pela leitura do Docker
                ex("""INSERT INTO updates(container,ts,from_version,from_image_id,to_version,to_image_id,source,result)
                      VALUES(?,?,?,?,?,?,?,?)""",
                   (name, ts, old["version"], old["image_id"], version_new, new_id, src, "OK"))
                ex("UPDATE containers SET image_id=?, version=?, last_update=? WHERE name=?",
                   (new_id, version_new if not docker_ok() else old["version"], ts, name))
        elif state == "failed":
            n_fail += 1
            ex("""INSERT INTO updates(container,ts,from_version,from_image_id,source,result,detail)
                  VALUES(?,?,?,?,?,?,?)""",
               (name, ts, old["version"], old["image_id"], src, "Falhou", (c.get("error") or "")[:1000]))
        elif cur_id and old["image_id"] and old["image_id"] != cur_id and not docker_ok():
            # imagem mudou fora do Watchtower (ex. docker compose pull manual)
            ex("""INSERT INTO updates(container,ts,from_version,from_image_id,to_version,to_image_id,source,result)
                  VALUES(?,?,?,?,?,?,?,?)""",
               (name, ts, old["version"], old["image_id"], _tag(image) or short_id(cur_id), cur_id,
                "Fora do Watchtower", "OK"))
            ex("UPDATE containers SET image_id=?, version=?, last_update=? WHERE name=?",
               (cur_id, _tag(image) or short_id(cur_id), ts, name))
        ex("UPDATE containers SET wt_state=?, wt_checked=?, last_seen=?, image=? WHERE name=?",
           (WT_STATES.get(state, state or "—"), ts, ts, image or old["image"], name))
    for e in data.get("entries", []) or []:
        if e and e.get("msg"):
            ex("INSERT INTO wt_logs(ts,level,msg) VALUES(?,?,?)",
               (e.get("time") or ts, (e.get("level") or "").lower(), e["msg"][:2000]))
    ex("INSERT INTO reports(ts,scanned,updated,failed,raw) VALUES(?,?,?,?,?)",
       (ts, data.get("scanned"), n_upd, n_fail, text[:4000]))
    meta_set("last_report", ts)
    log.info("webhook: verificados=%s atualizados=%d falhados=%d", data.get("scanned"), n_upd, n_fail)
    if docker_ok():
        threading.Thread(target=_safe_sync, daemon=True).start()


def wt_log_lines(n):
    rows = q("SELECT ts,level,msg FROM wt_logs ORDER BY id DESC LIMIT ?", (int(n),))
    return [f"{r['ts']} {r['level'].upper():5} {r['msg']}" for r in reversed(rows)]


def _safe_sync():
    time.sleep(5)
    try:
        sync_containers()
    except Exception as e:  # noqa: BLE001
        log.warning("sincronização falhou: %s", e)


# ---------------------------------------------------------------- tarefas de fundo
def purge():
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    ex("DELETE FROM audit WHERE ts < ?", (cutoff,))
    ex("DELETE FROM reports WHERE ts < ?", (cutoff,))
    ex("DELETE FROM wt_logs WHERE ts < ?", (cutoff,))
    ex("DELETE FROM login_attempts WHERE ts < ?", (time.time() - 86400,))


_started = False


def start_background():
    global _started
    if _started:
        return
    _started = True

    def loop():
        last_purge = 0
        while True:
            try:
                sync_containers()
            except Exception as e:  # noqa: BLE001
                log.warning("sincronização falhou: %s", e)
            if time.time() - last_purge > 3600:
                try:
                    purge()
                except Exception as e:  # noqa: BLE001
                    log.warning("limpeza falhou: %s", e)
                last_purge = time.time()
            time.sleep(SYNC_INTERVAL)

    threading.Thread(target=loop, daemon=True, name="sync").start()
