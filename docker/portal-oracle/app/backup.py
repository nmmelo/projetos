"""Backup / import da configuracao do portal.

O backup e um ficheiro JSON em DATA_DIR/backups com: definicoes, bases de dados Oracle
(passwords cifradas), perfis e utilizadores (hashes). Nao inclui o historico de execucoes,
o administrador local (admin.json) nem a configuracao de armazenamento (portal.json).
"""
import hashlib
import json
import os
import re
import secrets
from datetime import datetime

from . import crypto, settings, store

FORMAT = "oracle-shrink-portal-backup"
BACKUP_DIR = settings.path("backups")
_FNAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.json$")


def _key_fingerprint() -> str:
    return hashlib.sha256(crypto.key_bytes()).hexdigest()[:16]


def export_data(user: str) -> dict:
    dbs = store.list_dbs()
    db_name = {d["id"]: d["name"] for d in dbs}
    profiles = store.list_profiles()
    prof_name = {p["id"]: p["name"] for p in profiles}
    with store.tx() as t:
        users = t.all("SELECT * FROM users ORDER BY username")
        sett = t.all("SELECT k, v FROM settings ORDER BY k")
    return {
        "format": FORMAT, "version": 1,
        "created_at": store.now(), "created_by": user,
        "key_fingerprint": _key_fingerprint(),
        "settings": {r["k"]: r["v"] for r in sett},
        "oracle_dbs": [{k: d[k] for k in (*store.DB_FIELDS, "password_enc")} for d in dbs],
        "profiles": [{"name": p["name"], "description": p["description"], "all_dbs": p["all_dbs"],
                      "permissions": p["permissions"],
                      "dbs": [db_name[i] for i in p["db_ids"] if i in db_name]} for p in profiles],
        "users": [{"username": u["username"], "name": u["name"], "email": u["email"],
                   "password_hash": u["password_hash"], "active": bool(u["active"]),
                   "profile": prof_name.get(u["profile_id"])} for u in users],
    }


def _register(path: str, user: str, origin: str, note: str) -> int:
    return store.add_backup({"filename": os.path.basename(path), "created_at": store.now(),
                             "created_by": user, "size_bytes": os.path.getsize(path),
                             "origin": origin, "note": (note or "")[:255]})


def create(user: str, origin: str = "manual", note: str = "") -> int:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    fname = f"backup_{datetime.now():%Y%m%d_%H%M%S}_{origin}_{secrets.token_hex(2)}.json"
    path = os.path.join(BACKUP_DIR, fname)
    with open(path, "w") as fh:
        json.dump(export_data(user), fh, indent=2, ensure_ascii=False)
    os.chmod(path, 0o600)
    return _register(path, user, origin, note)


def file_path(row: dict) -> str:
    if not _FNAME_RE.match(row["filename"]):
        raise ValueError("Nome de ficheiro inválido.")
    return os.path.join(BACKUP_DIR, row["filename"])


def delete(bid: int) -> None:
    """Remove o ficheiro e o registo."""
    row = store.get_backup(bid)
    if not row:
        raise ValueError("Backup não encontrado.")
    try:
        os.remove(file_path(row))
    except FileNotFoundError:
        pass
    store.delete_backup_row(bid)


def prune(keep: int, origin: str = "cron") -> int:
    """Mantem os 'keep' backups mais recentes de uma origem (para o cron)."""
    rows = [r for r in store.list_backups() if r["origin"] == origin]
    for r in rows[keep:]:
        delete(r["id"])
    return max(len(rows) - keep, 0)


def save_upload(raw: bytes, user: str, original_name: str) -> int:
    data = parse(raw)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    fname = f"backup_{datetime.now():%Y%m%d_%H%M%S}_importado_{secrets.token_hex(2)}.json"
    path = os.path.join(BACKUP_DIR, fname)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.chmod(path, 0o600)
    return _register(path, user, "importado", f"Ficheiro carregado: {original_name}"[:255])


def parse(raw: bytes) -> dict:
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("O ficheiro não é um JSON válido.") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("O ficheiro não é um backup deste portal.")
    return data


def load_file(bid: int) -> dict:
    row = store.get_backup(bid)
    if not row:
        raise ValueError("Backup não encontrado.")
    with open(file_path(row), "rb") as fh:
        return parse(fh.read())


def import_data(data: dict, mode: str = "juntar") -> dict:
    """mode='juntar'   : cria/atualiza por nome (BDs, perfis) e username; mantem o resto.
       mode='substituir': apaga BDs, perfis e utilizadores atuais e carrega os do backup."""
    if mode not in ("juntar", "substituir"):
        raise ValueError("Modo de importação inválido.")
    stats = {"bds": 0, "perfis": 0, "utilizadores": 0,
             "chave_diferente": data.get("key_fingerprint") != _key_fingerprint()}
    with store.tx() as t:
        if mode == "substituir":
            for tb in ("users", "profiles", "oracle_dbs"):
                t.run(f"DELETE FROM {tb}")
        for k, v in (data.get("settings") or {}).items():
            if t.run("UPDATE settings SET v = ? WHERE k = ?", (v, k)) == 0:
                t.run("INSERT INTO settings (k, v) VALUES (?, ?)", (k, v))

        for d in data.get("oracle_dbs", []):
            row = {k: d.get(k) for k in (*store.DB_FIELDS, "password_enc")}
            cur = t.one("SELECT id FROM oracle_dbs WHERE name = ?", (row["name"],))
            if cur:
                t.update("oracle_dbs", cur["id"], {**row, "updated_at": store.now()})
            else:
                t.insert("oracle_dbs", {**row, "created_at": store.now(), "updated_at": store.now()})
            stats["bds"] += 1
        db_ids = {r["name"]: r["id"] for r in t.all("SELECT id, name FROM oracle_dbs")}

        for p in data.get("profiles", []):
            row = {"name": p["name"], "description": p.get("description") or "",
                   "all_dbs": 1 if p.get("all_dbs") else 0,
                   "db_ids": json.dumps(sorted(db_ids[n] for n in p.get("dbs", []) if n in db_ids)),
                   "permissions": json.dumps(sorted(p.get("permissions", []))), "updated_at": store.now()}
            cur = t.one("SELECT id FROM profiles WHERE name = ?", (row["name"],))
            if cur:
                t.update("profiles", cur["id"], row)
            else:
                t.insert("profiles", {**row, "created_at": store.now()})
            stats["perfis"] += 1
        prof_ids = {r["name"]: r["id"] for r in t.all("SELECT id, name FROM profiles")}

        for u in data.get("users", []):
            row = {"username": u["username"], "name": u.get("name") or "", "email": u.get("email") or "",
                   "password_hash": u["password_hash"], "active": 1 if u.get("active", True) else 0,
                   "profile_id": prof_ids.get(u.get("profile")), "updated_at": store.now()}
            cur = t.one("SELECT id FROM users WHERE username = ?", (row["username"],))
            if cur:
                t.update("users", cur["id"], row)
            else:
                t.insert("users", {**row, "created_at": store.now()})
            stats["utilizadores"] += 1
    return stats
