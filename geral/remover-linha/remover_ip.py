#!/usr/bin/env python3
"""
remover_ip.py - Remove de um ficheiro todas as linhas que contenham um IP.

Uso:
  python3 remover_ip.py                                  # pergunta o ficheiro e o IP
  python3 remover_ip.py -f defaults/main.yml -i 10.1.2.4
  python3 remover_ip.py -f defaults/main.yml -i 10.1.2.4 -y        # sem confirmação
  python3 remover_ip.py -f defaults/main.yml -i 10.1.2.4 --dry-run # só mostra

- Aceita o IP com ou sem aspas ("10.1.2.4").
- Só apaga o IP exato: 10.1.2.4 NÃO apaga linhas com 10.1.2.40 ou 110.1.2.4.
- Faz backup antes de alterar (ficheiro.AAAAMMDD_HHMMSS.bak) e mantém só o último.
"""
import argparse
import ipaddress
import os
import re
import shutil
import sys
from datetime import datetime

ASPAS = "\"'“”‘’"


def pedir(texto, valor=None):
    if valor:
        return valor
    try:
        return input(texto).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelado.")
        sys.exit(1)


def validar_ip(valor):
    v = valor.strip().strip(ASPAS).strip()
    try:
        return str(ipaddress.ip_address(v))
    except ValueError:
        return None


def regex_ip(ip):
    return re.compile(r"(?<![\d.])" + re.escape(ip) + r"(?!\.?\d)")


def limpar_backups_antigos(ficheiro, manter):
    pasta, base = os.path.split(os.path.abspath(ficheiro))
    padrao = re.compile(re.escape(base) + r"\.\d{8}_\d{6}\.bak$")
    backups = sorted(f for f in os.listdir(pasta) if padrao.match(f))
    for f in backups[:-manter]:
        os.remove(os.path.join(pasta, f))
        print(f"Backup antigo apagado: {f}")


def main():
    ap = argparse.ArgumentParser(description="Remove de um ficheiro as linhas que contenham um IP.")
    ap.add_argument("-f", "--ficheiro", help="Ficheiro a alterar (se omitido, é perguntado)")
    ap.add_argument("-i", "--ip", help="IP a remover (se omitido, é perguntado)")
    ap.add_argument("-y", "--yes", action="store_true", help="Não pedir confirmação")
    ap.add_argument("--dry-run", action="store_true", help="Só mostra as linhas, não altera")
    ap.add_argument("--sem-backup", action="store_true", help="Não fazer backup")
    ap.add_argument("--manter-backups", type=int, default=1, help="Backups a manter (por omissão 1)")
    args = ap.parse_args()

    # 1) Ficheiro
    ficheiro = os.path.expanduser(pedir("Ficheiro: ", args.ficheiro).strip(ASPAS))
    while not os.path.isfile(ficheiro):
        print(f"  Ficheiro não encontrado: {os.path.abspath(ficheiro)}")
        if args.ficheiro:
            return 1
        ficheiro = os.path.expanduser(pedir("Ficheiro: ").strip(ASPAS))
    ficheiro = os.path.abspath(ficheiro)

    # 2) IP
    ip = validar_ip(pedir("IP a remover: ", args.ip))
    while not ip:
        print("  IP inválido. Exemplo: 10.1.2.4")
        if args.ip:
            return 1
        ip = validar_ip(pedir("IP a remover: "))

    # 3) Procurar
    with open(ficheiro, encoding="utf-8") as f:
        linhas = f.readlines()
    p = regex_ip(ip)
    a_remover = [(n, l) for n, l in enumerate(linhas, 1) if p.search(l)]

    if not a_remover:
        print(f"\nNenhuma linha com {ip} em {ficheiro}. Nada a fazer.")
        return 0

    print(f"\nLinhas com {ip} em {ficheiro}:")
    for n, l in a_remover:
        print(f"  {n:>4}: {l.rstrip()}")

    if args.dry_run:
        print(f"\n(dry-run) Seriam removidas {len(a_remover)} linha(s). Nada foi alterado.")
        return 0

    # 4) Confirmar
    if not args.yes:
        resp = pedir(f"\nRemover {len(a_remover)} linha(s)? [s/N] ").lower()
        if resp not in ("s", "sim", "y", "yes"):
            print("Cancelado. Nada foi alterado.")
            return 1

    # 5) Backup + remoção
    if not args.sem_backup:
        copia = f"{ficheiro}.{datetime.now():%Y%m%d_%H%M%S}.bak"
        shutil.copy2(ficheiro, copia)
        print(f"Backup: {copia}")
        limpar_backups_antigos(ficheiro, max(1, args.manter_backups))

    apagar = {n for n, _ in a_remover}
    tmp = ficheiro + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(l for n, l in enumerate(linhas, 1) if n not in apagar)
    shutil.copymode(ficheiro, tmp)
    os.replace(tmp, ficheiro)

    print(f"Removidas {len(a_remover)} linha(s) com {ip}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
