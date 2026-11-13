"""
Gestão do gateway pela linha de comandos (chamado pelo `tesla-setup`).

  admin                          define utilizador/password do portal (interativo)
  cliente-add NOME [DESCRIÇÃO] [--perm LISTA]   cria cliente e mostra a chave
  cliente-list                   lista clientes e permissões
  cliente-on|off|rm|chave NOME   ativa, desativa, apaga, gera nova chave
  cliente-perm NOME [set|add|rm LISTA]   mostra ou altera as permissões
  permissoes                     lista as permissões e os presets disponíveis
  status-api on|off|token|info   gere a Status API
  definicao [CHAVE [VALOR]]      lê/altera cache_segundos, retencao_logs_dias
  logs [N] [NIVEL]               últimos N registos (INFO|WARNING|ERROR)
  logs-limpar                    apaga logs mais antigos que a retenção
  resumo                         estado do gateway (JSON)
"""
import getpass
import json
import sys

import common as c

EDITAVEIS = ("cache_segundos", "retencao_logs_dias")


def tabela(linhas, colunas):
    if not linhas:
        print("(vazio)")
        return
    larg = {k: max(len(k), *(len(str(l.get(k) if l.get(k) is not None else "")) for l in linhas)) for k in colunas}
    print("  ".join(k.upper().ljust(larg[k]) for k in colunas))
    for l in linhas:
        print("  ".join(str(l.get(k) if l.get(k) is not None else "").ljust(larg[k]) for k in colunas))


def mostrar_chave(titulo, chave):
    print(f"\n{titulo} (copie agora, não volta a ser mostrada):\n\n  {chave}\n")


def main(argv):
    c.init_db()
    if not argv:
        print(__doc__)
        return 1
    cmd, args = argv[0], argv[1:]

    if cmd == "admin":
        atual = c.get_def("admin_user")
        user = input(f"Utilizador do portal [{atual or 'admin'}]: ").strip() or atual or "admin"
        p1 = getpass.getpass("Password (mín. 8): ")
        p2 = getpass.getpass("Confirmar: ")
        if p1 != p2:
            print("As passwords não coincidem")
            return 1
        c.definir_admin(user, p1)
        print(f"Login do portal definido para '{user}'")

    elif cmd == "cliente-add":
        perm = None
        if "--perm" in args:
            i = args.index("--perm")
            if i + 1 >= len(args):
                print("uso: cliente-add NOME [DESCRIÇÃO] [--perm LISTA]")
                return 1
            perm = args[i + 1]
            args = args[:i] + args[i + 2:]
        if not args:
            print("uso: cliente-add NOME [DESCRIÇÃO] [--perm LISTA]")
            return 1
        mostrar_chave(f"Chave do cliente '{args[0]}'", c.criar_cliente(args[0], " ".join(args[1:]), perm))
        print("Permissões:", ", ".join(c.permissoes_cliente(args[0])) or "(nenhuma)")
    elif cmd == "cliente-list":
        tabela(c.listar_clientes(), ["nome", "prefixo", "ativo", "ultimo_uso", "pedidos", "permissoes", "descricao"])
    elif cmd == "cliente-perm":
        if not args:
            print("uso: cliente-perm NOME [set|add|rm LISTA]")
            return 1
        nome = args[0]
        if len(args) >= 3 and args[1] in ("set", "add", "rm"):
            if args[1] == "set":
                c.definir_permissoes(nome, args[2])
            elif args[1] == "add":
                c.alterar_permissoes(nome, adicionar=[args[2]])
            else:
                c.alterar_permissoes(nome, remover=[args[2]])
        elif len(args) != 1:
            print("uso: cliente-perm NOME [set|add|rm LISTA]")
            return 1
        atuais = c.permissoes_cliente(nome)
        for p, (desc, eps) in c.PERMISSOES.items():
            print(f"  [{'x' if p in atuais else ' '}] {p:<12} {desc}")
    elif cmd == "permissoes":
        print("Permissões:")
        for p, (desc, eps) in c.PERMISSOES.items():
            print(f"  {p:<12} {desc}  ({eps})")
        print("\nPresets:")
        for n, lst in c.PRESETS.items():
            print(f"  {n:<12} {', '.join(lst) or '(nenhuma)'}")
        print(f"\nNovos clientes sem --perm: {c.PERMISSAO_OMISSAO}")
    elif cmd in ("cliente-on", "cliente-off", "cliente-rm", "cliente-chave"):
        if not args:
            print(f"uso: {cmd} NOME")
            return 1
        if cmd == "cliente-on":
            c.ativar_cliente(args[0], True)
        elif cmd == "cliente-off":
            c.ativar_cliente(args[0], False)
        elif cmd == "cliente-rm":
            if input(f"Apagar '{args[0]}'? [s/N] ").lower() != "s":
                return 1
            c.apagar_cliente(args[0])
        else:
            mostrar_chave(f"Nova chave do cliente '{args[0]}'", c.regenerar_cliente(args[0]))
            return 0
        print("ok")

    elif cmd == "status-api":
        sub = args[0] if args else "info"
        if sub == "on":
            if not c.get_def("status_api_token_hash"):
                mostrar_chave("Token da Status API", c.gerar_token_status())
            c.set_def("status_api_ativa", "1")
            c.log("WARNING", "admin", "Status API ativada (cli)")
            print("Status API ativada")
        elif sub == "off":
            c.set_def("status_api_ativa", "0")
            c.log("WARNING", "admin", "Status API desativada (cli)")
            print("Status API desativada")
        elif sub == "token":
            mostrar_chave("Novo token da Status API", c.gerar_token_status())
        else:
            print(f"ativa : {'sim' if c.get_def('status_api_ativa') == '1' else 'não'}")
            print(f"token : {(c.get_def('status_api_token_prefixo') + '…') if c.get_def('status_api_token_prefixo') else '(nenhum)'}")

    elif cmd == "definicao":
        if not args:
            for k in EDITAVEIS:
                print(f"{k} = {c.get_def(k)}")
        elif len(args) == 1:
            print(c.get_def(args[0]))
        else:
            if args[0] not in EDITAVEIS:
                print(f"só é possível alterar: {', '.join(EDITAVEIS)}")
                return 1
            c.set_def(args[0], int(args[1]))
            c.log("INFO", "admin", f"definição {args[0]}={args[1]} (cli)")
            print("ok")

    elif cmd == "logs":
        n = int(args[0]) if args else 50
        nivel = args[1].upper() if len(args) > 1 else None
        linhas = list(reversed(c.ler_logs(nivel=nivel, limite=n)))
        for l in linhas:
            l["ts"] = l["ts"][:19].replace("T", " ")
        tabela(linhas, ["ts", "nivel", "origem", "cliente", "metodo", "caminho", "status", "ms", "mensagem"])
    elif cmd == "logs-limpar":
        print(f"{c.limpar_logs()} registos apagados")
    elif cmd == "resumo":
        print(json.dumps(c.resumo(), indent=2, ensure_ascii=False))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except c.ErroGateway as e:
        print(f"ERRO: {e.mensagem}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
