"""Utilitarios do portal (correr dentro do contentor, como utilizador 'portal').

  python manage.py set-admin-password [--user admin]   define o administrador local (data/admin.json)
  python manage.py backup [--note TEXTO] [--prune]      backup da configuracao (para o cron)
  python manage.py gen-fernet-key                       gera uma chave para PORTAL_FERNET_KEY
  python manage.py hash-password                        gera um hash (para PORTAL_ADMIN_PASSWORD_HASH)
  python manage.py set-storage --type T [--host H ...]  define a BD do portal (testa e cria as tabelas)
                                                        password em PORTAL_DB_PASSWORD (ou pedida)
  python manage.py show-storage                         mostra a BD do portal em uso
"""
import argparse
import getpass
import os
import sys


def _ask_password() -> str:
    from app.auth import MIN_PASSWORD
    p1 = getpass.getpass("Nova password: ")
    p2 = getpass.getpass("Repetir: ")
    if p1 != p2:
        sys.exit("As passwords nao coincidem.")
    if len(p1) < MIN_PASSWORD:
        sys.exit(f"A password tem de ter pelo menos {MIN_PASSWORD} caracteres.")
    return p1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("set-admin-password")
    a.add_argument("--user", default=None, help="nome do administrador (default: o atual ou 'admin')")
    b = sub.add_parser("backup")
    b.add_argument("--note", default="")
    b.add_argument("--prune", action="store_true", help="mantem so os N ultimos backups do cron (Configuracao > Portal > Geral)")
    sub.add_parser("gen-fernet-key")
    sub.add_parser("hash-password")
    s = sub.add_parser("set-storage", help="define a base de dados do portal")
    s.add_argument("--type", required=True, choices=["sqlite", "mariadb", "postgresql"])
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--database", default="portal")
    s.add_argument("--user", default="portal")
    s.add_argument("--test-only", action="store_true", help="so testa a ligacao e cria as tabelas, nao grava")
    sub.add_parser("show-storage")
    args = ap.parse_args()

    if args.cmd in ("set-storage", "show-storage"):
        from app import crypto, settings, store
        os.makedirs(settings.DATA_DIR, exist_ok=True)
        crypto.init(settings.DATA_DIR)
        if args.cmd == "show-storage":
            print(store.Store(settings.storage_config()).describe())
            return
        if args.type == "sqlite":
            cfg = dict(settings.DEFAULT_STORAGE)
        else:
            pwd = os.environ.get("PORTAL_DB_PASSWORD")
            if pwd is None:
                pwd = getpass.getpass(f"Password de {args.user}@{args.host}: ")
            cfg = {"type": args.type, "host": args.host,
                   "port": args.port or settings.DEFAULT_PORTS[args.type],
                   "database": args.database, "user": args.user,
                   "password_enc": crypto.encrypt(pwd)}
        target = store.Store(cfg)
        try:
            target.init_schema()                 # testa a ligacao e cria as tabelas
        except ImportError as exc:
            sys.exit(f"Driver Python em falta: {exc}")
        except Exception as exc:  # noqa: BLE001
            sys.exit(f"Falha na ligacao a {target.describe()}: {exc}")
        print(f"Ligacao OK, tabelas criadas/verificadas: {target.describe()}")
        if not args.test_only:
            settings.save_storage_config(cfg)
            print(f"Gravado em {settings.path('portal.json')}")
        return

    if args.cmd == "gen-fernet-key":
        from cryptography.fernet import Fernet
        print("PORTAL_FERNET_KEY=" + Fernet.generate_key().decode())
        return

    if args.cmd == "hash-password":
        from werkzeug.security import generate_password_hash
        h = generate_password_hash(_ask_password())
        print("\ndocker-compose v1 / sem Docker:  PORTAL_ADMIN_PASSWORD_HASH=" + h)
        print("docker compose v2:               PORTAL_ADMIN_PASSWORD_HASH=" + h.replace("$", "$$"))
        return

    if args.cmd == "set-admin-password":
        from werkzeug.security import generate_password_hash
        from app import settings
        cur = settings.admin() or {"username": "admin", "name": "Administrador", "email": ""}
        if args.user:
            cur["username"] = args.user
        print(f"Administrador: {cur['username']}")
        cur["password_hash"] = generate_password_hash(_ask_password())
        settings.save_admin(cur)
        print(f"Gravado em {settings.path('admin.json')}")
        return

    if args.cmd == "backup":
        from app import backup, create_app, store
        create_app(mark_interrupted=False)
        bid = backup.create("cron", "cron", args.note)
        print(f"Backup #{bid} criado: {store.get_backup(bid)['filename']}")
        if args.prune:
            keep = int(store.get_settings().get("backup_keep", 30))
            print(f"Backups do cron removidos: {backup.prune(keep, 'cron')} (mantidos {keep})")


if __name__ == "__main__":
    main()
