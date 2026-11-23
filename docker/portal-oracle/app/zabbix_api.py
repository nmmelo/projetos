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


def resumo_cliente(c: dict) -> dict:
    """Dados do anfitriao do Zabbix para este cliente."""
    nome = (c.get("zabbix_host") or c["nome"]).strip()
    t0 = time.time()
    hosts = chamar("host.get", {"output": ["hostid", "host", "name", "status", "maintenance_status"],
                                "filter": {"host": [nome]}, "selectInterfaces": ["ip", "dns", "port", "type", "available"]})
    if not hosts:
        hosts = chamar("host.get", {"output": ["hostid", "host", "name", "status", "maintenance_status"],
                                    "search": {"name": nome}, "limit": 1,
                                    "selectInterfaces": ["ip", "dns", "port", "type", "available"]})
    if not hosts:
        return {"encontrado": False, "nome": nome}
    h = hosts[0]
    probs = chamar("problem.get", {"output": ["eventid", "name", "severity", "clock", "acknowledged", "suppressed"],
                                   "hostids": [h["hostid"]], "recent": False, "sortfield": ["eventid"],
                                   "sortorder": "DESC", "limit": 50}) or []
    itens = chamar("item.get", {"output": ["itemid", "name", "key_", "lastvalue", "units", "lastclock", "value_type"],
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
            sel.append({"nome": i["name"], "chave": k, "valor": v, "unidade": u,
                        "quando": int(i.get("lastclock") or 0)})
    disp = {"0": "desconhecido", "1": "disponível", "2": "indisponível"}
    return {"encontrado": True, "nome": nome, "hostid": h["hostid"], "host": h["host"], "visivel": h["name"],
            "ativo": h["status"] == "0", "manutencao": h.get("maintenance_status") == "1",
            "interfaces": [{"end": i.get("ip") or i.get("dns"), "porta": i.get("port"),
                            "tipo": {"1": "agente", "2": "SNMP", "3": "IPMI", "4": "JMX"}.get(i.get("type"), i.get("type")),
                            "estado": disp.get(str(i.get("available")), "")} for i in h.get("interfaces") or []],
            "problemas": [{"nome": p["name"], "sev": int(p["severity"]), "sev_txt": SEVERIDADES.get(int(p["severity"]), ("?", ""))[0],
                           "cor": SEVERIDADES.get(int(p["severity"]), ("", "muted"))[1], "desde": int(p["clock"]),
                           "reconhecido": p.get("acknowledged") == "1"} for p in probs],
            "itens": sel[:80], "ms": int((time.time() - t0) * 1000), "url": (store.get_setting("zabbix_url") or "").rstrip("/")}
