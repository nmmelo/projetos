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
        guardar_estado(vin, ts=iso(agora()), estado="asleep" if l is None else "erro",
                       proxima_leitura=t + defi("recolha_intervalo_dormir", 1800))
        return

    a_conduzir = l["mudanca"] in ("D", "R", "N") or (l["velocidade_kmh"] or 0) > 0
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
    guardar_estado(vin, ts=l["ts"], estado="parado", mudanca=l["mudanca"], lat=l["lat"], lon=l["lon"],
                   odo_km=l["odo_km"], bateria=l["bateria"], parado_desde=parado_desde,
                   proxima_leitura=t + intervalo)


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
