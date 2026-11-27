"""Leitura da API do Zabbix (JSON-RPC) para mostrar no cliente: anfitriao, problemas e itens principais.
Configuracao em Clientes -> Gerir clientes -> Zabbix (endereco e token da API); o cliente liga-se ao anfitriao
pelo campo "Anfitriao no Zabbix" (ou, sem ele, pelo nome do cliente)."""
import json
import ssl
import time
import urllib.error
import urllib.request

from . import crypto, store

SEVERIDADES = {0: ("Não classificado", "muted"), 1: ("Informação", "info"), 2: ("Aviso", "warn"), 3: ("Médio", "warn"),
               4: ("Alto", "bad"), 5: ("Desastre", "bad")}
# itens mostrados (chave exata ou prefixo terminado em '[')
CHAVES = ("system.cpu.util", "system.cpu.load[all,avg1]", "system.cpu.load[all,avg5]", "vm.memory.utilization",
          "vm.memory.size[pavailable]", "system.swap.size[,pfree]", "system.uptime", "proc.num", "proc.num[]",
          "system.users.num", "agent.version", "system.sw.os", "system.hostname", "kernel.maxproc",
          "vfs.fs.dependent.size[", "vfs.fs.size[", "net.if.in[", "net.if.out[", "system.sw.packages.get",
          "zabbix[host,agent,available]", "agent.ping")


class ErroZabbix(Exception):
    pass


def configurado() -> bool:
    return bool(store.get_setting("zabbix_url") and store.get_setting("zabbix_api_token"))


def _url() -> str:
    u = (store.get_setting("zabbix_url") or "").rstrip("/")
    return u if u.endswith("api_jsonrpc.php") else u + "/api_jsonrpc.php"


def chamar(metodo: str, params, timeout: float = 15):
    try:
        token = crypto.decrypt(store.get_setting("zabbix_api_token") or "")
    except Exception as exc:  # noqa: BLE001
        raise ErroZabbix(f"token da API ilegível: {exc}") from None
    corpo = {"jsonrpc": "2.0", "method": metodo, "params": params, "id": 1}
    ctx = ssl.create_default_context()
    if store.get_setting("clientes_tls_inseguro") == "1":
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    for modo in ("header", "auth"):                 # 6.4+: Bearer; anteriores: campo "auth"
        c = dict(corpo, auth=token) if modo == "auth" else corpo
        req = urllib.request.Request(_url(), json.dumps(c).encode(), {"Content-Type": "application/json-rpc",
                                                                       **({"Authorization": f"Bearer {token}"} if modo == "header" else {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                d = json.loads(r.read().decode())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ErroZabbix(f"sem ligação ao Zabbix ({_url()}): {exc}") from None
        if "error" in d:
            msg = f"{d['error'].get('message', '')} {d['error'].get('data', '')}".strip()
            if modo == "header" and ("auth" in msg.lower() or "session" in msg.lower() or "not authori" in msg.lower()):
                continue
            raise ErroZabbix(msg)
        return d.get("result")
    raise ErroZabbix("autenticação recusada pelo Zabbix (token?)")


_versao: dict = {}


def versao() -> str:
    """Versao da API do Zabbix (apiinfo.version: sem autenticacao). Cache 10 min."""
    if _versao.get("url") == _url() and time.time() - _versao.get("t", 0) < 600:
        return _versao["v"]
    ctx = ssl.create_default_context()
    if store.get_setting("clientes_tls_inseguro") == "1":
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    req = urllib.request.Request(_url(), json.dumps({"jsonrpc": "2.0", "method": "apiinfo.version", "params": [], "id": 1}).encode(),
                                 {"Content-Type": "application/json-rpc"})
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            v = str(json.loads(r.read().decode()).get("result") or "")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ErroZabbix(f"sem ligação ao Zabbix ({_url()}): {exc}") from None
    _versao.update(url=_url(), t=time.time(), v=v)
    return v


def _v(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split(".")[:2])
    except ValueError:
        return (0, 0)


RECENTE = 900          # dados com menos de 15 min: o Zabbix esta a receber do anfitriao


def resumo_cliente(c: dict) -> dict:
    """Dados do anfitriao do Zabbix para este cliente."""
    nome = (c.get("zabbix_host") or c["nome"]).strip()
    t0 = time.time()
    ver = versao()
    t_api = int((time.time() - t0) * 1000)
    out_h = ["hostid", "host", "name", "status", "maintenance_status"]
    if _v(ver) >= (7, 0):
        out_h.append("active_available")        # agente em modo ativo (Zabbix 7)
    if _v(ver) >= (5, 4):
        sel_if = ["ip", "dns", "port", "type", "main", "available", "error", "errors_from", "disable_until"]
    else:                                        # ate 5.2 a disponibilidade era do anfitriao
        out_h += ["available", "error", "errors_from"]
        sel_if = ["ip", "dns", "port", "type", "main"]
    hosts = chamar("host.get", {"output": out_h, "filter": {"host": [nome]}, "selectInterfaces": sel_if,
                                "selectParentTemplates": ["name"]})
    if not hosts:
        hosts = chamar("host.get", {"output": out_h, "search": {"name": nome}, "limit": 1, "selectInterfaces": sel_if,
                                    "selectParentTemplates": ["name"]})
    if not hosts:
        return {"encontrado": False, "nome": nome}
    h = hosts[0]
    probs = chamar("problem.get", {"output": ["eventid", "name", "severity", "clock", "acknowledged", "suppressed"],
                                   "hostids": [h["hostid"]], "recent": False, "sortfield": ["eventid"],
                                   "sortorder": "DESC", "limit": 50}) or []
    itens = chamar("item.get", {"output": ["itemid", "name", "key_", "lastvalue", "units", "lastclock", "value_type", "type", "state", "error"],
                                "hostids": [h["hostid"]], "filter": {"status": 0}, "sortfield": "name"}) or []
    sel = []
    for i in itens:
        k = i["key_"]
        if any(k == x or (x.endswith("[") and k.startswith(x)) for x in CHAVES) and len(str(i.get("lastvalue") or "")) < 300:
            v, u = i.get("lastvalue"), i.get("units") or ""
            if u in ("uptime", "s") and str(v or "").replace(".", "", 1).isdigit():
                seg = int(float(v))
                v, u = (f"{seg // 86400} d {seg % 86400 // 3600} h" if seg >= 86400 else f"{seg // 3600} h {seg % 3600 // 60} min"), ""
            elif u == "B" and str(v or "").isdigit():
                n = float(v)
                for un in ("B", "KB", "MB", "GB", "TB"):
                    if n < 1024 or un == "TB":
                        v, u = f"{n:.1f}", un
                        break
                    n /= 1024
            sel.append({"nome": i["name"], "chave": k, "valor": v, "unidade": u, "itemid": i["itemid"],
                        "numerico": str(i.get("value_type")) in ("0", "3"), "quando": int(i.get("lastclock") or 0)})
    disp = {"0": "desconhecido", "1": "disponível", "2": "indisponível"}
    if _v(ver) < (5, 4):                         # disponibilidade no anfitriao (ate 5.2)
        for i in h.get("interfaces") or []:
            if str(i.get("type")) == "1":
                i.update(available=h.get("available"), error=h.get("error"), errors_from=h.get("errors_from"))
    interfaces = [{"end": i.get("ip") or i.get("dns"), "porta": i.get("port"),
                   "tipo": {"1": "agente", "2": "SNMP", "3": "IPMI", "4": "JMX"}.get(str(i.get("type")), i.get("type")),
                   "principal": str(i.get("main")) == "1", "valor": str(i.get("available")),
                   "estado": disp.get(str(i.get("available")), ""), "erro": i.get("error") or "",
                   "erros_desde": int(i.get("errors_from") or 0)} for i in h.get("interfaces") or []]
    ultimo = _ultimo_dado(itens)
    recente = bool(ultimo) and time.time() - ultimo < RECENTE
    # itens do agente: passivos (type 0, o servidor pergunta) e ativos (type 7, o agente envia)
    passivos = sum(1 for i in itens if str(i.get("type")) == "0")
    ativos = sum(1 for i in itens if str(i.get("type")) == "7")
    nao_supp = [{"nome": i["name"], "erro": i.get("error") or ""} for i in itens if str(i.get("state")) == "1"][:10]
    ativo_disp = disp.get(str(h.get("active_available")), "") if "active_available" in h else ""
    agente = [x for x in interfaces if x["tipo"] == "agente"]
    if recente:
        geral = "disponível"
    elif agente and all(x["valor"] == "2" for x in agente) or h.get("active_available") == "2":
        geral = "indisponível"
    elif not ultimo:
        geral = "sem dados"
    else:
        geral = "sem dados recentes"
    return {"encontrado": True, "nome": nome, "hostid": h["hostid"], "host": h["host"], "visivel": h["name"],
            "ativo": h["status"] == "0", "manutencao": h.get("maintenance_status") == "1",
            "interfaces": interfaces, "geral": geral, "recente": recente, "ativo_disp": ativo_disp,
            "passivos": passivos, "ativos": ativos, "nao_suportados": nao_supp,
            "templates": [t["name"] for t in h.get("parentTemplates") or []],
            "versao": ver, "api_ms": t_api, "n_itens": len(itens),
            "ultimo_dado": _ultimo_dado(itens),
            "problemas": [{"nome": p["name"], "sev": int(p["severity"]), "sev_txt": SEVERIDADES.get(int(p["severity"]), ("?", ""))[0],
                           "cor": SEVERIDADES.get(int(p["severity"]), ("", "muted"))[1], "desde": int(p["clock"]),
                           "reconhecido": p.get("acknowledged") == "1"} for p in probs],
            "itens": sel[:80], "ms": int((time.time() - t0) * 1000), "url": (store.get_setting("zabbix_url") or "").rstrip("/")}


# ============================================================================ historico e graficos
PERIODOS = {"1h": 3600, "3h": 3 * 3600, "6h": 6 * 3600, "12h": 12 * 3600, "1d": 86400, "2d": 2 * 86400,
            "7d": 7 * 86400, "30d": 30 * 86400}
CORES = ("#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#17becf", "#8c564b", "#e377c2")
_hosts: dict = {}


def host(c: dict) -> dict:
    """{hostid, host, name} do anfitriao do cliente (cache 10 min)."""
    nome = (c.get("zabbix_host") or c["nome"]).strip()
    h = _hosts.get(nome)
    if h and time.time() - h["_t"] < 600:
        return h
    hs = chamar("host.get", {"output": ["hostid", "host", "name"], "filter": {"host": [nome]}}) or \
        chamar("host.get", {"output": ["hostid", "host", "name"], "search": {"name": nome}, "limit": 1})
    if not hs:
        raise ErroZabbix(f"anfitrião {nome} não encontrado no Zabbix")
    h = {**hs[0], "_t": time.time()}
    _hosts[nome] = h
    return h


def _itens(hostid: str) -> list[dict]:
    return chamar("item.get", {"output": ["itemid", "name", "key_", "units", "value_type", "lastclock", "lastvalue", "history", "trends"],
                               "hostids": [hostid], "filter": {"status": 0}}) or []


def _serie(it: dict, desde: int, ate: int, pontos: int = 500) -> tuple[list, str, int]:
    """[[ts, valor]] (media por intervalo), a fonte ('historico' ou 'tendencias') e o passo em segundos.
    Periodos longos ou sem historico (retencao curta): tendencias horarias (media)."""
    vt = str(it.get("value_type"))
    if vt not in ("0", "3"):
        return [], "", 60
    span = ate - desde
    fonte, linhas = "historico", []
    if span <= 2 * 86400:
        linhas = chamar("history.get", {"output": ["clock", "value"], "history": int(vt), "itemids": [it["itemid"]],
                                        "time_from": desde, "time_till": ate, "sortfield": "clock", "sortorder": "ASC",
                                        "limit": 20000}, timeout=30) or []
    if not linhas:
        fonte = "tendencias"
        tr = chamar("trend.get", {"output": ["clock", "value_avg", "value_max"], "itemids": [it["itemid"]],
                                  "time_from": desde, "time_till": ate, "limit": 5000}, timeout=30) or []
        linhas = [{"clock": t["clock"], "value": t["value_avg"]} for t in sorted(tr, key=lambda t: int(t["clock"]))]
    if not linhas:
        return [], fonte, 60
    passo = max(60 if fonte == "historico" else 3600, span // pontos)
    baldes: dict = {}
    for ln in linhas:
        try:
            ts, v = int(ln["clock"]), float(ln["value"])
        except (KeyError, TypeError, ValueError):
            continue
        b = baldes.setdefault(ts - ts % passo, [0.0, 0])
        b[0] += v
        b[1] += 1
    # o passo real das recolhas (para cortar a linha nas falhas)
    if fonte == "historico" and len(linhas) > 2:
        intervalo = max(1, (int(linhas[-1]["clock"]) - int(linhas[0]["clock"])) // (len(linhas) - 1))
        passo = max(passo, intervalo)
    return [[k + passo // 2, round(s / n, 3)] for k, (s, n) in sorted(baldes.items())], fonte, passo


def _escolher(itens: list[dict]) -> list[dict]:
    """Graficos ao estilo do Zabbix (template Linux/Windows by Zabbix agent): CPU, carga, memoria, swap, rede, discos."""
    por = {i["key_"]: i for i in itens}
    g = []

    def add(gid, titulo, unidade, series, area=False, maximo=None):
        series = [s for s in series if s[1]]
        if series:
            g.append({"id": gid, "titulo": titulo, "unidade": unidade, "area": area, "max": maximo,
                      "itens": [{"nome": n, "item": i, "inverter": inv} for n, i, inv in series]})

    cpu_tipos = [(n, por.get(f"system.cpu.util[,{k}]"), False) for k, n in
                 (("user", "user"), ("system", "system"), ("iowait", "iowait"), ("steal", "steal"), ("nice", "nice"),
                  ("softirq", "softirq"), ("interrupt", "interrupt"))]
    total = por.get("system.cpu.util") or por.get("system.cpu.util[,,avg1]") or por.get('perf_counter_en["\\Processor Information(_total)\\% Processor Time"]')
    add("cpu", "CPU — utilização", "%", [("Total", total, False)] + [x for x in cpu_tipos if x[1]][:5], area=True, maximo=100)
    add("load", "CPU — carga (load average)", "",
        [(n, por.get(f"system.cpu.load[all,{k}]") or por.get(f"system.cpu.load[percpu,{k}]"), False)
         for k, n in (("avg1", "1 min"), ("avg5", "5 min"), ("avg15", "15 min"))])
    mem = por.get("vm.memory.utilization")
    pdisp = por.get("vm.memory.size[pavailable]")
    if mem:
        add("mem", "Memória — utilização", "%", [("Memória usada", mem, False)], area=True, maximo=100)
    elif pdisp:
        add("mem", "Memória — utilização", "%", [("Memória usada", pdisp, True)], area=True, maximo=100)
    add("memb", "Memória — bytes", "bytes", [("Disponível", por.get("vm.memory.size[available]"), False),
                                              ("Total", por.get("vm.memory.size[total]"), False)])
    swap = por.get("system.swap.size[,pfree]")
    add("swap", "Swap — utilização", "%", [("Swap usada", swap, True)], area=True, maximo=100)
    # rede: as interfaces com mais trafego (ultimo valor)
    ifs = {}
    for k, i in por.items():
        for pre, d in (("net.if.in[", "in"), ("net.if.out[", "out")):
            if k.startswith(pre) and k.count(",") <= 1 and not k.endswith(",dropped]") and not k.endswith(",errors]"):
                nome = k[len(pre):].split(",")[0].strip('"]')
                ifs.setdefault(nome, {})[d] = i
    def ultimo(i):
        try:
            return float(i.get("lastvalue") or 0)
        except ValueError:
            return 0.0
    top = sorted(ifs.items(), key=lambda x: -sum(ultimo(i) for i in x[1].values()))[:2]
    for n, d in top:
        add(f"net-{n}", f"Rede — {n}", "bps", [("Entrada", d.get("in"), False), ("Saída", d.get("out"), False)], area=True)
    # discos: % usado por sistema de ficheiros
    fs = []
    for k, i in por.items():
        for pre in ("vfs.fs.dependent.size[", "vfs.fs.size["):
            if k.startswith(pre) and k.endswith(",pused]"):
                fs.append((k[len(pre):-len(",pused]")].strip('"'), i))
    fs = sorted(dict(fs).items(), key=lambda x: -ultimo(x[1]))[:6]
    add("fs", "Discos — espaço usado", "%", [(n, i, False) for n, i in fs], maximo=100)
    return g


def _ultimo_dado(itens: list[dict]) -> int:
    """Ultimo valor recebido do agente (os itens internos zabbix[...] sao calculados pelo servidor)."""
    return max((int(i.get("lastclock") or 0) for i in itens if not i["key_"].startswith("zabbix[")), default=0)


def graficos(c: dict, periodo: str = "1d", ate: int | None = None) -> dict:
    """Graficos (CPU, carga, memoria, swap, rede, discos) e a disponibilidade do agente no periodo."""
    t0 = time.time()
    span = PERIODOS.get(periodo, 86400)
    ate = int(ate or time.time())
    desde = ate - span
    h = host(c)
    itens = _itens(h["hostid"])
    out, fontes = [], set()
    for gr in _escolher(itens):
        series = []
        passo = 60
        for n, it in enumerate(gr["itens"]):
            pts, fonte, p = _serie(it["item"], desde, ate)
            if it["inverter"]:
                pts = [[t, round(100 - v, 3)] for t, v in pts]
            if pts:
                fontes.add(fonte)
            passo = max(passo, p)
            series.append({"nome": it["nome"], "cor": CORES[n % len(CORES)], "pontos": pts,
                           "area": gr["area"] and n == 0, "largura": 2 if n == 0 else 1.5,
                           "ultimo": pts[-1][1] if pts else None})
        out.append({"id": gr["id"], "titulo": gr["titulo"], "unidade": gr["unidade"], "max": gr["max"],
                    "passo": passo, "series": series})
    # disponibilidade do agente (1 = disponivel, 0 = nao): item interno do Zabbix
    por = {i["key_"]: i for i in itens}
    disp_it = por.get("zabbix[host,agent,available]") or por.get("agent.ping")
    disp = []
    if disp_it:   # zabbix[host,agent,available]: 1 disponivel, 2 indisponivel, 0 desconhecido; agent.ping: 1
        interno = disp_it["key_"].startswith("zabbix[")
        for ts, v in _serie(disp_it, desde, ate, 300)[0]:
            if interno:
                disp.append([ts, None if v < 0.5 else (1 if v <= 1.5 else 0)])
            else:
                disp.append([ts, 1 if v >= 0.5 else 0])
    ult = _ultimo_dado(itens)
    return {"desde": desde, "ate": ate, "periodo": periodo, "graficos": out, "disponibilidade": disp,
            "fonte": "tendências (média horária)" if fontes == {"tendencias"} else ("histórico" if fontes else "sem dados"),
            "ultimo_dado": ult, "ms": int((time.time() - t0) * 1000)}


def grafico_item(c: dict, itemid: str, periodo: str = "1d") -> dict:
    """Historico de um item (Itens -> clicar), como o 'Graph' dos Dados recentes do Zabbix."""
    span = PERIODOS.get(periodo, 86400)
    ate = int(time.time())
    h = host(c)
    its = chamar("item.get", {"output": ["itemid", "name", "key_", "units", "value_type"], "hostids": [h["hostid"]],
                              "itemids": [str(itemid)]}) or []
    if not its:
        raise ErroZabbix("item não encontrado neste anfitrião")
    it = its[0]
    if str(it["value_type"]) not in ("0", "3"):
        raise ErroZabbix("este item não é numérico (não tem gráfico)")
    pts, fonte, passo = _serie(it, ate - span, ate)
    u = it.get("units") or ""
    un = "bytes" if u == "B" else ("bps" if u == "bps" else ("%" if u == "%" else (" " + u if u else "")))
    return {"desde": ate - span, "ate": ate, "titulo": it["name"], "chave": it["key_"], "unidade": un, "passo": passo,
            "fonte": fonte, "series": [{"nome": it["name"], "cor": CORES[0], "pontos": pts, "area": True}]}
