"""
tesla-key — recolha de viagens por leituras periódicas (corre em segundo plano no contentor).

Em cada leitura pede à Tesla drive_state, location_data, charge_state e vehicle_state:
  - mudança D/R/N ou velocidade > 0  → a conduzir: abre/continua a viagem e guarda um ponto
  - P (ou sem mudança)               → parado: fecha a viagem aberta
  - 408 (a dormir/offline)           → fecha a viagem aberta e espera mais tempo

Intervalos e limite diário em Definições (recolha_*). Cada leitura conta como um pedido de dados
(cliente "recolha" na página Consumo). Também faz o backup diário do gateway.db.
"""
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

import common as c

MILHA = 1.609344
ENDPOINTS = "drive_state;location_data;charge_state;vehicle_state"
_parar = False


def _sair(*_):
    global _parar
    _parar = True


def agora():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


def defi(chave, omissao):
    try:
        return int(float(c.get_def(chave) or omissao))
    except ValueError:
        return omissao


def estado(vin):
    with c.db() as con:
        r = con.execute("SELECT * FROM estado_veiculo WHERE vin=?", (vin,)).fetchone()
    return dict(r) if r else {"vin": vin, "proxima_leitura": 0}


def guardar_estado(vin, **campos):
    campos["vin"] = vin
    cols = ", ".join(campos)
    with c.db() as con:
        con.execute(f"INSERT INTO estado_veiculo({cols}) VALUES ({', '.join('?' for _ in campos)})"
                    f" ON CONFLICT(vin) DO UPDATE SET " + ", ".join(f"{k}=excluded.{k}" for k in campos if k != "vin"),
                    list(campos.values()))


def viagem_aberta(vin):
    with c.db() as con:
        r = con.execute("SELECT * FROM viagens WHERE vin=? AND estado='aberta' ORDER BY id DESC LIMIT 1",
                        (vin,)).fetchone()
    return dict(r) if r else None


def fechar_viagem(v, leitura=None):
    """Fecha a viagem com a última leitura (ou com o último ponto, se não houver leitura)."""
    with c.db() as con:
        if leitura is None or leitura.get("lat") is None:
            ult = con.execute("SELECT * FROM pontos WHERE viagem_id=? ORDER BY ts DESC LIMIT 1", (v["id"],)).fetchone()
            leitura = dict(ult) if ult else {}
            leitura.setdefault("ts", v["inicio"])
            leitura["bateria"] = leitura.get("bateria")
        odo_fim = leitura.get("odo_km") or v.get("odo_fim_km") or v["odo_inicio_km"]
        km = round(odo_fim - v["odo_inicio_km"], 1) if (odo_fim and v["odo_inicio_km"]) else None
        vmax = con.execute("SELECT MAX(velocidade_kmh) FROM pontos WHERE viagem_id=?", (v["id"],)).fetchone()[0]
        npts = con.execute("SELECT COUNT(*) FROM pontos WHERE viagem_id=?", (v["id"],)).fetchone()[0]
        con.execute("""UPDATE viagens SET fim=?, lat_fim=?, lon_fim=?, odo_fim_km=?, km=?, bateria_fim=?,
                       velocidade_max_kmh=?, pontos=?, estado='fechada' WHERE id=?""",
                    (leitura.get("ts"), leitura.get("lat"), leitura.get("lon"), odo_fim, km,
                     leitura.get("bateria"), vmax, npts, v["id"]))
    c.log("INFO", "recolha", f"viagem {v['id']} fechada: {km if km is not None else '?'} km")


def ler(vin):
    """Pede os dados à Tesla. Devolve dict com a leitura, ou None se o carro estiver a dormir/offline."""
    r = c.tesla("GET", f"vehicles/{vin}/vehicle_data", params={"endpoints": ENDPOINTS}, cliente_consumo="recolha")
    if r.status_code == 408:
        return None
    if r.status_code != 200:
        c.log("WARNING", "recolha", f"{vin[-6:]}: resposta {r.status_code} {r.text[:150]}")
        return {"erro": r.status_code}
    d = r.json().get("response") or {}
    ds, cs, vs = d.get("drive_state") or {}, d.get("charge_state") or {}, d.get("vehicle_state") or {}
    vel = ds.get("speed")
    odo = vs.get("odometer")
    ts = ds.get("gps_as_of") or ds.get("timestamp")
    if ts and ts > 10**11:
        ts //= 1000
    return {
        "ts": iso(datetime.fromtimestamp(ts, timezone.utc)) if ts else iso(agora()),
        "mudanca": ds.get("shift_state"),
        "velocidade_kmh": round(vel * MILHA) if vel is not None else None,
        "direcao": ds.get("heading"),
        "lat": ds.get("latitude"), "lon": ds.get("longitude"),
        "bateria": cs.get("battery_level"),
        "odo_km": round(odo * MILHA, 1) if odo is not None else None,
        "carga_estado": cs.get("charging_state"),
        "carga_kwh": cs.get("charge_energy_added"),
        "carga_kw": cs.get("charger_power"),
        "carga_dc": bool(cs.get("fast_charger_present")),
        "carga_dc_tipo": cs.get("fast_charger_type"),
    }


def guardar_ponto(vin, viagem_id, l):
    if l.get("lat") is None or l.get("lon") is None:
        return
    with c.db() as con:
        con.execute("""INSERT INTO pontos(vin, viagem_id, ts, lat, lon, velocidade_kmh, direcao, bateria, odo_km)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (vin, viagem_id, l["ts"], l["lat"], l["lon"], l["velocidade_kmh"], l["direcao"],
                     l["bateria"], l["odo_km"]))


def leituras_hoje(vin):
    hoje = agora().strftime("%Y-%m-%d")
    with c.db() as con:
        return con.execute("SELECT COUNT(*) FROM consumo WHERE dia=? AND cliente='recolha' AND caminho LIKE ?",
                           (hoje, f"vehicles/{vin}/%")).fetchone()[0]


def processar(vin):
    e = estado(vin)
    t = time.time()
    if t < (e.get("proxima_leitura") or 0):
        return
    if leituras_hoje(vin) >= defi("recolha_limite_diario", 150):
        amanha = (agora() + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        c.log("WARNING", "recolha", f"{vin[-6:]}: limite diário de leituras atingido; retoma às 00:00 UTC")
        guardar_estado(vin, proxima_leitura=amanha.timestamp())
        return

    l = ler(vin)
    v = viagem_aberta(vin)

    if l is None or "erro" in l:                       # a dormir, offline ou erro
        if v and l is None:
            fechar_viagem(v)
        cg = carregamento_aberto(vin)
        if cg and l is None:
            fechar_carregamento(cg)
        # "ts" fica com a hora da última leitura com dados (usada como início das viagens/carregamentos inferidos)
        guardar_estado(vin, estado="asleep" if l is None else "erro",
                       proxima_leitura=t + defi("recolha_intervalo_dormir", 1800))
        return

    a_conduzir = l["mudanca"] in ("D", "R", "N") or (l["velocidade_kmh"] or 0) > 0
    a_carregar = l.get("carga_estado") in ("Charging", "Starting")
    tratar_carregamento(vin, e, l, a_carregar, a_conduzir)

    # viagem que aconteceu toda entre duas leituras (o carro estava a dormir): deteta-se pelo odómetro
    if not a_conduzir and not v and e.get("odo_km") and l.get("odo_km") and l["odo_km"] - e["odo_km"] >= 0.5:
        criar_viagem_inferida(vin, e, l)

    if a_conduzir:
        if not v:
            # início: usa a última posição/odómetro conhecidos (o arranque pode ter sido entre leituras)
            ini_lat, ini_lon = e.get("lat") or l["lat"], e.get("lon") or l["lon"]
            ini_odo, ini_bat = e.get("odo_km") or l["odo_km"], e.get("bateria") or l["bateria"]
            ini_ts = l["ts"]
            with c.db() as con:
                cur = con.execute("""INSERT INTO viagens(vin, inicio, lat_inicio, lon_inicio, odo_inicio_km, bateria_inicio)
                                     VALUES (?,?,?,?,?,?)""", (vin, ini_ts, ini_lat, ini_lon, ini_odo, ini_bat))
                vid = cur.lastrowid
            c.log("INFO", "recolha", f"viagem {vid} iniciada")
            if e.get("lat") is not None:
                guardar_ponto(vin, vid, {"ts": e.get("ts") or ini_ts, "lat": ini_lat, "lon": ini_lon, "velocidade_kmh": 0,
                                         "direcao": None, "bateria": ini_bat, "odo_km": ini_odo})
        else:
            vid = v["id"]
        guardar_ponto(vin, vid, l)
        guardar_estado(vin, ts=l["ts"], estado="conduzir", mudanca=l["mudanca"], lat=l["lat"], lon=l["lon"],
                       odo_km=l["odo_km"], bateria=l["bateria"], parado_desde=None,
                       proxima_leitura=t + defi("recolha_intervalo_conduzir", 120))
        return

    # parado
    if v:
        guardar_ponto(vin, v["id"], l)
        fechar_viagem(v, l)
    parado_desde = e.get("parado_desde") if e.get("estado") == "parado" else None
    parado_desde = parado_desde or l["ts"]
    try:
        minutos = (agora() - datetime.fromisoformat(parado_desde)).total_seconds() / 60
    except ValueError:
        minutos = 0
    intervalo = (defi("recolha_intervalo_dormir", 1800) if minutos >= defi("recolha_minutos_parado", 30)
                 else defi("recolha_intervalo_parado", 600))
    if a_carregar:
        intervalo = min(intervalo, defi("recolha_intervalo_carregar", 300))
    guardar_estado(vin, ts=l["ts"], estado="parado", mudanca=l["mudanca"], lat=l["lat"], lon=l["lon"],
                   odo_km=l["odo_km"], bateria=l["bateria"], parado_desde=parado_desde,
                   proxima_leitura=t + intervalo)


def criar_viagem_inferida(vin, e, l):
    km = round(l["odo_km"] - e["odo_km"], 1)
    with c.db() as con:
        con.execute("""INSERT INTO viagens(vin, inicio, fim, lat_inicio, lon_inicio, lat_fim, lon_fim, odo_inicio_km,
                       odo_fim_km, km, bateria_inicio, bateria_fim, pontos, estado, origem)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,'fechada','inferida')""",
                    (vin, e.get("ts") or l["ts"], l["ts"], e.get("lat"), e.get("lon"), l["lat"], l["lon"],
                     e["odo_km"], l["odo_km"], km, e.get("bateria"), l["bateria"]))
    c.log("INFO", "recolha", f"viagem inferida pelo odómetro: {km} km (entre leituras, sem percurso)")


def carregamento_aberto(vin):
    with c.db() as con:
        r = con.execute("SELECT * FROM carregamentos WHERE vin=? AND estado='a_carregar' ORDER BY id DESC LIMIT 1",
                        (vin,)).fetchone()
    return dict(r) if r else None


def fechar_carregamento(cg, l=None):
    with c.db() as con:
        ult = con.execute("SELECT * FROM carregamento_pontos WHERE carregamento_id=? ORDER BY ts DESC LIMIT 1",
                          (cg["id"],)).fetchone()
        ult = dict(ult) if ult else {}
        n = con.execute("SELECT COUNT(*) FROM carregamento_pontos WHERE carregamento_id=?", (cg["id"],)).fetchone()[0]
        energia = (l or {}).get("carga_kwh") or ult.get("energia_kwh") or cg.get("energia_kwh")
        bat = (l or {}).get("bateria") or ult.get("bateria") or cg.get("bateria_fim")
        con.execute("UPDATE carregamentos SET fim=?, estado='terminado', bateria_fim=?, energia_kwh=?, pontos=? WHERE id=?",
                    ((l or {}).get("ts") or ult.get("ts") or cg["inicio"], bat, energia, n, cg["id"]))
    c.log("INFO", "recolha", f"carregamento {cg['id']} terminado: {energia if energia is not None else '?'} kWh")


def tratar_carregamento(vin, e, l, a_carregar, a_conduzir):
    cg = carregamento_aberto(vin)
    if a_carregar:
        if not cg:
            with c.db() as con:
                cur = con.execute("""INSERT INTO carregamentos(vin, inicio, tipo, lat, lon, local, bateria_inicio, bateria_fim,
                                     energia_kwh, potencia_max_kw, odo_km, origem) VALUES (?,?,?,?,?,?,?,?,?,?,?,'leituras')""",
                                  (vin, l["ts"], "DC" if l["carga_dc"] else "AC", l["lat"], l["lon"],
                                   l.get("carga_dc_tipo") if l["carga_dc"] else None, l["bateria"], l["bateria"],
                                   l["carga_kwh"], l["carga_kw"], l["odo_km"]))
                cg = {"id": cur.lastrowid, "potencia_max_kw": l["carga_kw"]}
            c.log("INFO", "recolha", f"carregamento {cg['id']} iniciado ({'DC' if l['carga_dc'] else 'AC'})")
        with c.db() as con:
            con.execute("INSERT INTO carregamento_pontos(carregamento_id, ts, bateria, potencia_kw, energia_kwh) VALUES (?,?,?,?,?)",
                        (cg["id"], l["ts"], l["bateria"], l["carga_kw"], l["carga_kwh"]))
            con.execute("""UPDATE carregamentos SET bateria_fim=?, energia_kwh=?,
                           potencia_max_kw=MAX(COALESCE(potencia_max_kw,0), COALESCE(?,0)) WHERE id=?""",
                        (l["bateria"], l["carga_kwh"], l["carga_kw"], cg["id"]))
        return
    if cg:
        fechar_carregamento(cg, l)
        return
    # carregamento que aconteceu todo entre duas leituras: deteta-se pela subida da bateria (>= 3 %)
    if (not a_conduzir and e.get("bateria") is not None and l.get("bateria") is not None
            and l["bateria"] - e["bateria"] >= 3):
        with c.db() as con:
            con.execute("""INSERT INTO carregamentos(vin, inicio, fim, estado, origem, lat, lon, bateria_inicio, bateria_fim,
                           energia_kwh, odo_km) VALUES (?,?,?,'terminado','inferido',?,?,?,?,?,?)""",
                        (vin, e.get("ts") or l["ts"], l["ts"], l["lat"], l["lon"], e["bateria"], l["bateria"],
                         l.get("carga_kwh") if l.get("carga_estado") in ("Complete", "Stopped") else None, l["odo_km"]))
        c.log("INFO", "recolha", f"carregamento inferido pela bateria: {e['bateria']} → {l['bateria']} %")


def importar_supercharger(vin=None):
    """Importa o histórico de carregamentos pagos (Superchargers) da Tesla; junta-o aos carregamentos registados
    pelas leituras quando as horas coincidem. Requer o scope vehicle_charging_cmds. Devolve (novos, atualizados)."""
    novos = atualizados = 0
    pagina = 1                       # a Tesla numera as páginas a partir de 1 (pageNo=0 dá 400 "Invalid page no")
    while pagina <= 20:
        params = {"pageNo": pagina, "pageSize": 50}
        if vin:
            params["vin"] = vin
        r = c.tesla("GET", "dx/charging/history", params=params, cliente_consumo="recolha")
        if r.status_code != 200:
            c.log("WARNING", "recolha", f"histórico de Superchargers: Tesla respondeu {r.status_code} {r.text[:150]}")
            break
        resp = r.json().get("response") or {}
        lote = resp.get("data") or []
        for s in lote:
            sid = str(s.get("sessionId") or "")
            ini, fim = s.get("chargeStartDateTime"), s.get("chargeStopDateTime") or s.get("unlatchDateTime")
            if not sid or not ini:
                continue
            fees = [f for f in (s.get("fees") or []) if isinstance(f, dict)]
            custo = round(sum(float(f.get("totalDue") or 0) for f in fees), 2) if fees else None
            moeda = next((f.get("currencyCode") or f.get("currency") for f in fees if f.get("currencyCode") or f.get("currency")), None)
            kwh = sum(float(f.get("usageBase") or 0) for f in fees
                      if (f.get("feeType") or "").upper() == "CHARGING" and (f.get("uom") or "kwh").lower() == "kwh") or None
            v = s.get("vin") or vin
            with c.db() as con:
                if con.execute("SELECT 1 FROM carregamentos WHERE sessao_tesla=?", (sid,)).fetchone():
                    continue
                ini_utc = datetime.fromisoformat(ini.replace("Z", "+00:00")).astimezone(timezone.utc)
                janela = (iso(ini_utc - timedelta(minutes=30)), iso(ini_utc + timedelta(minutes=30)))
                par = con.execute("SELECT id FROM carregamentos WHERE vin=? AND sessao_tesla IS NULL AND inicio BETWEEN ? AND ?"
                                  " ORDER BY inicio LIMIT 1", (v, *janela)).fetchone()
                if par:
                    con.execute("UPDATE carregamentos SET sessao_tesla=?, local=?, custo=?, moeda=?, tipo='DC',"
                                " energia_kwh=COALESCE(?, energia_kwh) WHERE id=?",
                                (sid, s.get("siteLocationName"), custo, moeda, kwh, par[0]))
                    atualizados += 1
                else:
                    con.execute("""INSERT INTO carregamentos(vin, inicio, fim, estado, origem, tipo, local, energia_kwh,
                                   custo, moeda, sessao_tesla) VALUES (?,?,?,'terminado','supercharger','DC',?,?,?,?,?)""",
                                (v, iso(ini_utc), fim and iso(datetime.fromisoformat(fim.replace("Z", "+00:00")).astimezone(timezone.utc)),
                                 s.get("siteLocationName"), kwh, custo, moeda, sid))
                    novos += 1
        if not lote or not resp.get("hasMoreData"):
            break
        pagina += 1
    c.set_def("recolha_supercharger_ts", time.time())
    if novos or atualizados:
        c.log("INFO", "recolha", f"histórico de Superchargers: {novos} novos, {atualizados} associados")
    return novos, atualizados


def vins():
    """VINs da conta (atualizado a cada 6 h)."""
    with c.db() as con:
        guardados = [r[0] for r in con.execute("SELECT vin FROM estado_veiculo")]
    ult = float(c.get_def("recolha_vins_ts") or 0)
    if guardados and time.time() - ult < 6 * 3600:
        return guardados
    r = c.tesla("GET", "vehicles", cliente_consumo="recolha")
    if r.status_code != 200:
        return guardados
    lista = [x["vin"] for x in r.json().get("response", []) if x.get("vin")]
    for v in lista:
        if v not in guardados:
            guardar_estado(v, proxima_leitura=0)
    c.set_def("recolha_vins_ts", time.time())
    return lista


def main():
    signal.signal(signal.SIGTERM, _sair)
    signal.signal(signal.SIGINT, _sair)
    c.init_db()
    print("recolha: a arrancar", flush=True)
    while not _parar:
        try:
            c.backup_diario()
            if c.get_def("recolha_ativa") == "1":
                for vin in vins():
                    processar(vin)
                if (c.get_def("recolha_supercharger") == "1"
                        and time.time() - float(c.get_def("recolha_supercharger_ts") or 0) > 24 * 3600):
                    importar_supercharger()
        except c.ErroGateway as e:
            c.log("WARNING", "recolha", e.mensagem)
            time.sleep(60)
        except Exception as e:  # nunca parar o ciclo
            c.log("ERROR", "recolha", f"{type(e).__name__}: {e}")
            time.sleep(60)
        for _ in range(15):
            if _parar:
                break
            time.sleep(1)
    print("recolha: terminada", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
