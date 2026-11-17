"""
Gestão do gateway pela linha de comandos (chamado pelo `tesla-setup`).

  admin                          cria/altera um administrador do portal (interativo)
  utilizador-list                utilizadores do portal
  utilizador-add NOME [admin|leitura]   cria utilizador (pede a password)
  utilizador-perfil NOME admin|leitura  muda o perfil
  utilizador-password NOME       muda a password
  utilizador-on|off|rm NOME      ativa, desativa, apaga
  auditoria [N] [TIPO]           últimos N registos de auditoria (login, login_falhado, bloqueado, acao...)
  cliente-add NOME [DESCRIÇÃO] [--perm LISTA]   cria cliente e mostra a chave
  cliente-list                   lista clientes e permissões
  cliente-on|off|rm|chave NOME   ativa, desativa, apaga, gera nova chave
  cliente-perm NOME [set|add|rm LISTA]   mostra ou altera as permissões
  permissoes                     lista as permissões e os presets disponíveis
  status-api on|off|token|info   gere a Status API
  definicao [CHAVE [VALOR]]      lê/altera tempos de cache, secções por omissão, preços, retenção
  funcionalidades [set LISTA]    funcionalidades ativas no gateway (para todos os clientes)
  consumo [AAAA-MM]              estimativa de consumo da Fleet API no mês
  config                         tesla.env (segredos mascarados) e scopes do token
  recolha [on|off]               estado da recolha de viagens (ou ativar/desativar)
  viagens [PERIODO VALOR]        viagens registadas (ex.: viagens mes 2026-11; viagens ano 2026)
  carregamentos [PERIODO VALOR]  carregamentos registados (ex.: carregamentos mes 2026-11)
  importar-supercharger          importa já o histórico de Superchargers da Tesla
  importar-tessie [--sem-percursos]
                                 importa o histórico da Tessie (pede o token; não o guarda)
  bateria                        saúde da bateria (últimas medições e degradação)
  viagens-limpar [--sim]         apaga as viagens sem distância (--sim: só conta)
  rotas [ID | --todas]           percurso pelas estradas: de uma viagem, das que não o têm, ou de todas
  backup                         cria um backup do gateway.db
  backups                        lista os backups
  restaurar NOME                 restaura um backup (faz antes um backup "antes-restauro")
  exportar TIPO FORMATO [FICH] [PERIODO VALOR]
                                 TIPO viagens|pontos|carregamentos|bateria|paragens|consumo|logs; FORMATO csv|json|gpx|geojson
  logs [N] [NIVEL]               últimos N registos (INFO|WARNING|ERROR)
  logs-limpar                    apaga logs mais antigos que a retenção
  resumo                         estado do gateway (JSON)
"""
import getpass
import json
import os
import sys

import common as c

EDITAVEIS = ("cache_segundos", "cache_localizacao_segundos", "cache_veiculos_segundos", "imagem_cache_dias",
             "data_endpoints_omissao", "retencao_logs_dias",
             "preco_dados", "preco_wake", "preco_comando", "credito_mensal")


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
        admins = [u["nome"] for u in c.listar_utilizadores() if u["perfil"] == "admin"]
        atual = admins[0] if admins else "admin"
        user = input(f"Administrador do portal [{atual}]: ").strip() or atual
        p1 = getpass.getpass("Password (mín. 8): ")
        p2 = getpass.getpass("Confirmar: ")
        if p1 != p2:
            print("As passwords não coincidem")
            return 1
        c.definir_admin(user, p1)
        print(f"Login do portal definido para '{user}'")

    elif cmd == "utilizador-list":
        tabela(c.listar_utilizadores(), ["nome", "perfil", "ativo", "ultimo_login", "criado_em"])
    elif cmd in ("utilizador-add", "utilizador-password"):
        if not args:
            print(f"uso: {cmd} NOME" + (" [admin|leitura]" if cmd == "utilizador-add" else ""))
            return 1
        p1 = getpass.getpass("Password (mín. 8): ")
        if p1 != getpass.getpass("Confirmar: "):
            print("As passwords não coincidem")
            return 1
        if cmd == "utilizador-add":
            c.definir_utilizador(args[0], p1, args[1] if len(args) > 1 else "leitura")
        else:
            c.definir_utilizador(args[0], password=p1, criar=False)
        print("ok")
    elif cmd == "utilizador-perfil":
        if len(args) < 2:
            print("uso: utilizador-perfil NOME admin|leitura")
            return 1
        c.definir_utilizador(args[0], perfil=args[1], criar=False)
        print("ok")
    elif cmd in ("utilizador-on", "utilizador-off", "utilizador-rm"):
        if not args:
            print(f"uso: {cmd} NOME")
            return 1
        if cmd == "utilizador-rm":
            if input(f"Apagar '{args[0]}'? [s/N] ").lower() != "s":
                return 1
            c.apagar_utilizador(args[0])
        else:
            c.definir_utilizador(args[0], ativo=cmd == "utilizador-on", criar=False)
        print("ok")
    elif cmd == "auditoria":
        n = int(args[0]) if args else 50
        linhas = list(reversed(c.ler_auditoria(tipo=args[1] if len(args) > 1 else None, limite=n)))
        for l in linhas:
            l["ts"] = l["ts"][:19].replace("T", " ")
        tabela(linhas, ["ts", "tipo", "utilizador", "ip", "detalhe"])
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
            valor = args[1]
            if args[0] == "data_endpoints_omissao":
                secs = [x for x in valor.replace(",", ";").split(";") if x]
                desconhecidas = [x for x in secs if x not in c.SECCOES]
                if not secs or desconhecidas:
                    print(f"secções válidas: {', '.join(c.SECCOES)}")
                    return 1
                valor = ";".join(secs)
            elif args[0].startswith("preco_") or args[0] == "credito_mensal":
                valor = float(valor.replace(",", "."))
            else:
                valor = int(valor)
            c.set_def(args[0], valor)
            c.log("INFO", "admin", f"definição {args[0]}={args[1]} (cli)")
            print("ok")

    elif cmd == "funcionalidades":
        if len(args) >= 2 and args[0] == "set":
            c.definir_funcionalidades(args[1])
        ativas = c.funcionalidades_ativas()
        for p, (desc, eps) in c.PERMISSOES.items():
            print(f"  [{'x' if p in ativas else ' '}] {p:<12} {desc}")
    elif cmd == "consumo":
        m = c.consumo_mes(args[0] if args else None)
        nomes = {"dados": "Dados", "wake": "Wakes", "comando": "Comandos"}
        print(f"Mês {m['mes']}: {m['total_usd']:.2f} $ de {m['credito_usd']:.2f} $ "
              f"({m['credito_usado_pct'] or 0} % do crédito)\n")
        tabela([{"tipo": nomes[k], "pedidos": v["pedidos"], "preco": v["preco"], "custo": f"{v['custo']:.3f}",
                 "hoje": v["hoje"], "limite_dia": v["limite_dia"]} for k, v in m["categorias"].items()],
               ["tipo", "pedidos", "preco", "custo", "hoje", "limite_dia"])
        if m["por_cliente"]:
            print()
            tabela(m["por_cliente"], ["cliente", "categoria", "n", "custo"])
        print(f"\n{m['nota']}")
    elif cmd == "config":
        for k, v in c.ler_env_bruto():
            print(f"  {k:<24} {c.mascarar(k, v)}")
        print(f"\n  scopes no token: {' '.join(c.scopes_token()) or '(sem token)'}")
    elif cmd == "recolha":
        if args and args[0] in ("on", "off"):
            c.set_def("recolha_ativa", "1" if args[0] == "on" else "0")
            c.log("WARNING", "admin", f"recolha de viagens {'ativa' if args[0] == 'on' else 'desativada'} (cli)")
        print(f"recolha: {'ativa' if c.get_def('recolha_ativa') == '1' else 'desativada'}")
        for k in ("recolha_intervalo_conduzir", "recolha_intervalo_parado", "recolha_minutos_parado",
                  "recolha_intervalo_dormir", "recolha_limite_diario"):
            print(f"  {k:<28} {c.get_def(k)}")
        with c.db() as con:
            est = [dict(r) for r in con.execute("SELECT vin, estado, ts, bateria FROM estado_veiculo")]
        if est:
            print()
            tabela([{**e, "vin": "…" + e["vin"][-6:]} for e in est], ["vin", "estado", "ts", "bateria"])
    elif cmd == "viagens":
        de, ate = c.intervalo_periodo(args[0], args[1] if len(args) > 1 else None) if args else c.intervalo_periodo("dias", 30)
        r = c.resumo_viagens(None, de, ate)
        print(f"{r['n']} viagens, {r['km']} km, {r['bateria']} % de bateria (de {de[:10]} a {ate[:10]})\n")
        tabela([{k: v[k] for k in ("id", "inicio", "fim", "km", "bateria_inicio", "bateria_fim", "pontos", "estado")}
                for v in c.listar_viagens(None, de, ate, 100)],
               ["id", "inicio", "fim", "km", "bateria_inicio", "bateria_fim", "pontos", "estado"])
    elif cmd == "carregamentos":
        de, ate = c.intervalo_periodo(args[0], args[1] if len(args) > 1 else None) if args else c.intervalo_periodo("dias", 30)
        r = c.resumo_carregamentos(None, de, ate)
        print(f"{r['n']} carregamentos, {r['kwh']} kWh, {r['custo']} {r['moeda'] or ''} (de {de[:10]} a {ate[:10]})\n")
        tabela([{k: x[k] for k in ("id", "inicio", "tipo", "bateria_inicio", "bateria_fim", "energia_kwh", "custo", "origem")}
                for x in c.listar_carregamentos(None, de, ate, 100)],
               ["id", "inicio", "tipo", "bateria_inicio", "bateria_fim", "energia_kwh", "custo", "origem"])
    elif cmd == "importar-supercharger":
        import recolha
        r = recolha.importar_supercharger()
        print(f"{r['recebidas']} recebidas, {r['novos']} novas, {r['associados']} associadas, "
              f"{r['existentes']} já existentes, {r['ignoradas']} ignoradas" + (f"\nERRO: {r['erro']}" if r["erro"] else ""))
    elif cmd == "importar-tessie":
        import getpass
        import tessie
        token = os.environ.get("TESSIE_TOKEN") or getpass.getpass("Token da Tessie: ")
        print("A importar (pode demorar vários minutos)...", flush=True)
        r = tessie.importar(token, percursos="--sem-percursos" not in args)
        tabela([{"dados": k, "n": v} for k, v in r.items() if k != "avisos"], ["dados", "n"])
        for a in r["avisos"][:20]:
            print("aviso:", a)
    elif cmd == "viagens-limpar":
        r = c.viagens_vazias()
        if "--sim" in args or not r["n"]:
            print(f"{r['n']} viagens sem distância (< {r['km_minimo']} km ou 0 min)"
                  + (f", de {r['primeira'][:10]} a {r['ultima'][:10]}" if r["n"] else ""))
        else:
            print(f"{c.apagar_viagens_vazias()} viagens apagadas (backup criado antes)")
    elif cmd == "rotas":
        import rotas
        if args and args[0].isdigit():
            print(rotas.calcular(int(args[0]), forcar=True) or "não calculado (ver tesla-setup logs)")
        else:
            print(rotas.calcular_todas(forcar="--todas" in args))
    elif cmd == "bateria":
        sb = c.saude_bateria()
        print(json.dumps(sb["resumo"], ensure_ascii=False, indent=1))
        tabela(sb["medicoes"][-20:], ["ts", "odo_km", "autonomia_max_km", "capacidade_kwh", "origem"])
    elif cmd == "backup":
        print("backup criado:", c.criar_backup("manual"))
    elif cmd == "backups":
        tabela(c.listar_backups(), ["nome", "data", "tamanho_kb"])
    elif cmd == "restaurar":
        if not args:
            print("uso: restaurar NOME (ver: backups)")
            return 1
        if input(f"Restaurar {args[0]}? Os dados atuais são substituídos. [s/N] ").lower() != "s":
            return 1
        c.restaurar_backup(args[0])
        print("restaurado")
    elif cmd == "exportar":
        if len(args) < 2:
            print("uso: exportar TIPO FORMATO [FICHEIRO] [PERIODO VALOR]")
            return 1
        tipo, formato = args[0], args[1]
        fich = args[2] if len(args) > 2 and args[2] not in ("ano", "mes", "semana", "dias", "tudo") else None
        resto = args[3:] if fich else args[2:]
        de, ate = c.intervalo_periodo(resto[0], resto[1] if len(resto) > 1 else None) if resto else ("0000", "9999")
        corpo, _, nome = c.exportar(tipo, formato, None, de, ate)
        fich = fich or os.path.join(c.BASE, "exportacoes", nome)
        os.makedirs(os.path.dirname(fich) or ".", exist_ok=True)
        with open(fich, "w", encoding="utf-8") as f:
            f.write(corpo)
        print("exportado:", fich)
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
