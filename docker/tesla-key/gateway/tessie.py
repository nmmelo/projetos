"""Importação única do histórico guardado na Tessie (viagens com percurso, carregamentos, saúde da bateria
e períodos parado) para a base de dados do gateway.

O token da Tessie só é usado durante a importação: não é guardado nem escrito nos logs.
Os registos já existentes (recolhidos pelo gateway) são completados em vez de duplicados.
"""
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

import common as c

API = "https://api.tessie.com"
_LOCK = threading.Lock()


def _iso(ts):
    if ts is None:
        return None
    if ts > 10**11:                       # milissegundos
        ts /= 1000
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _num(v, casas=1):
    try:
        return round(float(v), casas) if v is not None else None
    except (TypeError, ValueError):
        return None


def _estado(**kw):
    atual = json.loads(c.get_def("tessie_estado") or "{}")
    atual.update(kw, atualizado=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    c.set_def("tessie_estado", json.dumps(atual, ensure_ascii=False))
    return atual


def estado():
    return json.loads(c.get_def("tessie_estado") or "{}")


class Tessie:
    def __init__(self, token):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token.strip()}"
        self.pedidos = 0

    def get(self, caminho, **params):
        for tentativa in range(5):
            r = self.s.get(f"{API}/{caminho.lstrip('/')}", params=params, timeout=60)
            self.pedidos += 1
            if r.status_code == 429:                  # limite de pedidos: espera e repete
                time.sleep(10 * (tentativa + 1))
                continue
            if r.status_code in (401, 403):
                raise c.ErroGateway(r.status_code, "Tessie recusou o token (inválido, expirado ou subscrição inativa)")
            if r.status_code >= 400:
                raise c.ErroGateway(502, f"Tessie respondeu {r.status_code} em /{caminho.split('?')[0]}: {r.text[:200]}")
            return r.json()
        raise c.ErroGateway(429, "Tessie: demasiados pedidos, tente mais tarde")

    def resultados(self, caminho, **params):
        d = self.get(caminho, **params)
        if isinstance(d, list):
            return d
        return d.get("results") or d.get("result") or []

    def vins(self):
        d = self.get("vehicles", only_active="false")
        lista = d.get("results", d) if isinstance(d, dict) else d
        return [v.get("vin") for v in lista if isinstance(v, dict) and v.get("vin")]


def _janelas(desde_ano=2012, meses=3):
    """Intervalos de N meses até hoje (evita limites de resultados por pedido)."""
    ini = datetime(desde_ano, 1, 1, tzinfo=timezone.utc)
    fim = datetime.now(timezone.utc) + timedelta(days=1)
    while ini < fim:
        m = ini.month - 1 + meses
        prox = ini.replace(year=ini.year + m // 12, month=m % 12 + 1)
        yield int(ini.timestamp()), int(min(prox, fim).timestamp())
        ini = prox


def _por_janelas(t, caminho, **params):
    vistos, todos = set(), []
    for a, b in _janelas():
        for x in t.resultados(caminho, **params, **{"from": a, "to": b}):
            chave = x.get("id") or (x.get("started_at"), x.get("timestamp"))
            if chave not in vistos:
                vistos.add(chave)
                todos.append(x)
    return todos


# ---------------------------------------------------------------------------
# Viagens
# ---------------------------------------------------------------------------

def _viagens(t, vin, percursos, st):
    drives = _por_janelas(t, f"{vin}/drives", distance_format="km", temperature_format="c", timezone="UTC")
    st["viagens_recebidas"] += len(drives)
    _estado(fase=f"viagens …{vin[-6:]}", **st)
    for i, d in enumerate(sorted(drives, key=lambda x: x.get("started_at") or 0)):
        tid, ini, fim = d.get("id"), _iso(d.get("started_at")), _iso(d.get("ended_at"))
        if not tid or not ini:
            continue
        km = _num(d.get("odometer_distance"))
        vals = dict(fim=fim, lat_inicio=d.get("starting_latitude"), lon_inicio=d.get("starting_longitude"),
                    lat_fim=d.get("ending_latitude"), lon_fim=d.get("ending_longitude"), km=km,
                    bateria_inicio=d.get("starting_battery"), bateria_fim=d.get("ending_battery"),
                    velocidade_max_kmh=_num(d.get("max_speed"), 0), local_inicio=d.get("starting_location"),
                    local_fim=d.get("ending_location"), energia_kwh=_num(d.get("energy_used"), 2),
                    velocidade_media_kmh=_num(d.get("average_speed")), temp_exterior=_num(d.get("average_outside_temperature")),
                    temp_interior=_num(d.get("average_inside_temperature")), autopilot_km=_num(d.get("autopilot_distance")))
        with c.db() as con:
            if con.execute("SELECT 1 FROM viagens WHERE tessie_id=?", (tid,)).fetchone():
                st["viagens_existentes"] += 1
                continue
            a = (datetime.fromisoformat(ini) - timedelta(minutes=10)).isoformat(timespec="seconds")
            b = (datetime.fromisoformat(ini) + timedelta(minutes=10)).isoformat(timespec="seconds")
            par = con.execute("SELECT id, pontos FROM viagens WHERE vin=? AND tessie_id IS NULL AND origem='leituras'"
                              " AND inicio BETWEEN ? AND ? ORDER BY inicio LIMIT 1", (vin, a, b)).fetchone()
            if par:
                # completa a viagem já registada; mantém o que o gateway mediu e acrescenta o resto
                sets = ", ".join(f"{k}=COALESCE({k}, ?)" for k in vals)
                con.execute(f"UPDATE viagens SET tessie_id=?, {sets} WHERE id=?", (tid, *vals.values(), par[0]))
                vid, tem_pontos = par[0], par[1] > 3
                st["viagens_associadas"] += 1
            else:
                cols = ", ".join(vals)
                cur = con.execute(f"INSERT INTO viagens(vin, inicio, estado, origem, tessie_id, odo_inicio_km, odo_fim_km, {cols})"
                                  f" VALUES (?,?,?,?,?,?,?,{','.join('?' * len(vals))})",
                                  (vin, ini, "fechada", "tessie", tid, None, None, *vals.values()))
                vid, tem_pontos = cur.lastrowid, False
                st["viagens_novas"] += 1
            # viagens "inferidas" pelo odómetro que a Tessie cobre com detalhe passam a ser redundantes
            if fim:
                apagadas = con.execute("DELETE FROM viagens WHERE vin=? AND origem='inferida' AND inicio <= ? AND COALESCE(fim, inicio) >= ?",
                                       (vin, fim, ini)).rowcount
                st["inferidas_substituidas"] += apagadas
        if percursos and not tem_pontos and fim:
            try:
                _percurso(t, vin, vid, d)
            except c.ErroGateway as e:
                st["avisos"].append(f"percurso da viagem {tid}: {e.mensagem}"[:200])
        if i % 20 == 0:
            _estado(fase=f"viagens …{vin[-6:]} ({i + 1}/{len(drives)})", **st)


def _percurso(t, vin, vid, d):
    pts = t.resultados(f"{vin}/path", **{"from": int(d["started_at"]), "to": int(d["ended_at"]) + 1},
                       details="true", simplify="true", distance_format="km")
    linhas = []
    for p in pts:
        if isinstance(p, str) and "," in p:                  # formato "lat,lon"
            la, lo = p.split(",")[:2]
            linhas.append((vin, vid, _iso(d["started_at"]), float(la), float(lo), None, None, None, None))
        elif isinstance(p, dict) and p.get("latitude") is not None:
            linhas.append((vin, vid, _iso(p.get("timestamp")) or _iso(d["started_at"]), p["latitude"], p["longitude"],
                           _num(p.get("speed"), 0), p.get("heading"), p.get("battery_level"), _num(p.get("odometer"))))
    if not linhas:
        return
    with c.db() as con:
        con.execute("DELETE FROM pontos WHERE viagem_id=?", (vid,))
        con.executemany("INSERT INTO pontos(vin, viagem_id, ts, lat, lon, velocidade_kmh, direcao, bateria, odo_km)"
                        " VALUES (?,?,?,?,?,?,?,?,?)", linhas)
        con.execute("UPDATE viagens SET pontos=? WHERE id=?", (len(linhas), vid))


# ---------------------------------------------------------------------------
# Carregamentos
# ---------------------------------------------------------------------------

def _carregamentos(t, vin, st):
    lista = _por_janelas(t, f"{vin}/charges", distance_format="km", timezone="UTC")
    st["carregamentos_recebidos"] += len(lista)
    _estado(fase=f"carregamentos …{vin[-6:]}", **st)
    for x in lista:
        tid, ini, fim = x.get("id"), _iso(x.get("started_at")), _iso(x.get("ended_at"))
        if not tid or not ini:
            continue
        vals = dict(fim=fim, lat=x.get("latitude"), lon=x.get("longitude"), local=x.get("location"),
                    bateria_inicio=x.get("starting_battery"), bateria_fim=x.get("ending_battery"),
                    energia_kwh=_num(x.get("energy_added"), 2), custo=_num(x.get("cost"), 2),
                    odo_km=_num(x.get("odometer")), autonomia_km=_num(x.get("miles_added")),
                    tipo="DC" if x.get("is_supercharger") else None)
        with c.db() as con:
            if con.execute("SELECT 1 FROM carregamentos WHERE tessie_id=?", (tid,)).fetchone():
                st["carregamentos_existentes"] += 1
                continue
            a = (datetime.fromisoformat(ini) - timedelta(minutes=30)).isoformat(timespec="seconds")
            b = (datetime.fromisoformat(ini) + timedelta(minutes=30)).isoformat(timespec="seconds")
            par = con.execute("SELECT id FROM carregamentos WHERE vin=? AND tessie_id IS NULL AND origem IN ('leituras','supercharger')"
                              " AND inicio BETWEEN ? AND ? ORDER BY inicio LIMIT 1", (vin, a, b)).fetchone()
            if par:
                # mantém os valores da Tesla (custo/kWh faturados, curva) e completa o que falta
                sets = ", ".join(f"{k}=COALESCE({k}, ?)" for k in vals)
                con.execute(f"UPDATE carregamentos SET tessie_id=?, {sets} WHERE id=?", (tid, *vals.values(), par[0]))
                st["carregamentos_associados"] += 1
            else:
                cols = ", ".join(vals)
                con.execute(f"INSERT INTO carregamentos(vin, inicio, estado, origem, tessie_id, {cols})"
                            f" VALUES (?,?,?,?,?,{','.join('?' * len(vals))})",
                            (vin, ini, "terminado", "tessie", tid, *vals.values()))
                st["carregamentos_novos"] += 1
            if fim:
                st["inferidos_substituidos"] += con.execute(
                    "DELETE FROM carregamentos WHERE vin=? AND origem='inferido' AND inicio <= ? AND COALESCE(fim, inicio) >= ?",
                    (vin, fim, ini)).rowcount


# ---------------------------------------------------------------------------
# Saúde da bateria e paragens
# ---------------------------------------------------------------------------

def _bateria(t, vin, st):
    d = t.get(f"{vin}/battery_health", distance_format="km", **{"from": 0, "to": int(time.time())})
    lista = (d.get("results") if isinstance(d, dict) else d) or []
    with c.db() as con:
        for m in lista:
            if not isinstance(m, dict) or not m.get("timestamp"):
                continue
            cur = con.execute("INSERT OR IGNORE INTO bateria_saude(vin, ts, odo_km, autonomia_max_km, autonomia_ideal_km,"
                              " capacidade_kwh, origem) VALUES (?,?,?,?,?,?,'tessie')",
                              (vin, _iso(m["timestamp"]), _num(m.get("odometer")), _num(m.get("max_range")),
                               _num(m.get("max_ideal_range")), _num(m.get("capacity"), 2)))
            st["bateria_medicoes"] += cur.rowcount


def _paragens(t, vin, st):
    lista = _por_janelas(t, f"{vin}/idles", distance_format="km", timezone="UTC")
    with c.db() as con:
        for x in lista:
            if not x.get("id") or not x.get("started_at"):
                continue
            cur = con.execute("""INSERT OR IGNORE INTO paragens(vin, inicio, fim, local, lat, lon, bateria_inicio, bateria_fim,
                                 energia_kwh, autonomia_km, clima_pct, sentinela_pct, origem, tessie_id)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'tessie',?)""",
                              (vin, _iso(x["started_at"]), _iso(x.get("ended_at")), x.get("location"), x.get("latitude"),
                               x.get("longitude"), x.get("starting_battery"), x.get("ending_battery"),
                               _num(x.get("energy_used"), 2), _num(x.get("rated_range_used")),
                               _num((x.get("climate_fraction") or 0) * 100), _num((x.get("sentry_fraction") or 0) * 100),
                               x["id"]))
            st["paragens"] += cur.rowcount


# ---------------------------------------------------------------------------

PARTES = ("viagens", "carregamentos", "bateria", "paragens")


def importar(token, partes=PARTES, percursos=True):
    """Importa tudo o que a Tessie tem. Faz um backup antes. Devolve as contagens."""
    if not _LOCK.acquire(blocking=False):
        raise c.ErroGateway(409, "já está a decorrer uma importação da Tessie")
    st = {k: 0 for k in ("viagens_recebidas", "viagens_novas", "viagens_associadas", "viagens_existentes",
                         "inferidas_substituidas", "carregamentos_recebidos", "carregamentos_novos",
                         "carregamentos_associados", "carregamentos_existentes", "inferidos_substituidos",
                         "bateria_medicoes", "paragens")}
    st["avisos"] = []
    inicio = time.time()
    try:
        _estado(a_decorrer=True, fase="a preparar", erro=None, inicio=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                fim=None, **st)
        c.criar_backup("antes-tessie")
        t = Tessie(token)
        vins = t.vins()
        if not vins:
            raise c.ErroGateway(404, "a Tessie não devolveu veículos para este token")
        st["veiculos"] = len(vins)
        for vin in vins:
            if "viagens" in partes:
                _viagens(t, vin, percursos, st)
            if "carregamentos" in partes:
                _carregamentos(t, vin, st)
            if "bateria" in partes:
                _estado(fase=f"saúde da bateria …{vin[-6:]}", **st)
                _bateria(t, vin, st)
            if "paragens" in partes:
                _estado(fase=f"paragens …{vin[-6:]}", **st)
                _paragens(t, vin, st)
        st["pedidos"] = t.pedidos
        st["minutos"] = round((time.time() - inicio) / 60, 1)
        resumo = (f"importação Tessie: {st['viagens_novas']} viagens novas, {st['viagens_associadas']} completadas; "
                  f"{st['carregamentos_novos']} carregamentos novos, {st['carregamentos_associados']} completados; "
                  f"{st['bateria_medicoes']} medições de bateria; {st['paragens']} paragens ({st['pedidos']} pedidos)")
        c.log("INFO", "tessie", resumo)
        _estado(a_decorrer=False, fase="concluída", fim=datetime.now(timezone.utc).isoformat(timespec="seconds"), **st)
        return st
    except c.ErroGateway as e:
        c.log("WARNING", "tessie", f"importação Tessie falhou: {e.mensagem}")
        _estado(a_decorrer=False, fase="falhou", erro=e.mensagem, **st)
        raise
    except Exception as e:
        c.log("ERROR", "tessie", f"importação Tessie falhou: {type(e).__name__}: {e}")
        _estado(a_decorrer=False, fase="falhou", erro=f"{type(e).__name__}: {e}", **st)
        raise
    finally:
        _LOCK.release()


def importar_em_fundo(token, partes=PARTES, percursos=True):
    def correr():
        try:
            importar(token, partes, percursos)
        except Exception:
            pass                                       # já ficou no estado e nos logs
    threading.Thread(target=correr, daemon=True, name="tessie").start()
