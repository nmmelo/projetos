"""Percurso pelas estradas entre os pontos de uma viagem (em vez de linhas retas entre leituras).

Usa um servidor OSRM (Open Source Routing Machine). Por omissão o servidor público
https://router.project-osrm.org (gratuito, sem chave, uso moderado); pode apontar-se para um OSRM próprio em
Definições → Recolha. Os pontos registados não são alterados: o percurso calculado fica em `viagem_rota`.
"""
import json
import math
import time

import requests

import common as c

MAX_PONTOS = 90          # limite de coordenadas por pedido (o servidor público aceita ~100)
_ultimo = [0.0]


def ativo():
    return c.get_def("rota_ativa") == "1"


def _dist_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _reduzir(pts):
    """Mantém o primeiro e o último ponto e escolhe os intermédios igualmente espaçados."""
    if len(pts) <= MAX_PONTOS:
        return pts
    passo = (len(pts) - 1) / (MAX_PONTOS - 1)
    return [pts[round(i * passo)] for i in range(MAX_PONTOS)]


def _pedir(url):
    # o servidor público pede no máximo 1 pedido por segundo
    espera = 1.1 - (time.time() - _ultimo[0])
    if espera > 0:
        time.sleep(espera)
    _ultimo[0] = time.time()
    r = requests.get(url, timeout=30, headers={"User-Agent": "tesla-key-gateway"})
    if r.status_code != 200:
        raise c.ErroGateway(502, f"serviço de rotas respondeu {r.status_code}: {r.text[:150]}")
    d = r.json()
    if d.get("code") != "Ok" or not d.get("routes"):
        raise c.ErroGateway(422, f"serviço de rotas: {d.get('code')} {d.get('message', '')}".strip())
    return d["routes"][0]


def calcular(vid, forcar=False):
    """Calcula e guarda o percurso pelas estradas da viagem `vid`. Devolve (n_coordenadas, km) ou None."""
    with c.db() as con:
        v = con.execute("SELECT id, km, estado FROM viagens WHERE id=?", (vid,)).fetchone()
        if not v:
            raise c.ErroGateway(404, "viagem não encontrada")
        if not forcar and con.execute("SELECT 1 FROM viagem_rota WHERE viagem_id=?", (vid,)).fetchone():
            return None
        pts = [dict(r) for r in con.execute("SELECT lat, lon, direcao, velocidade_kmh FROM pontos WHERE viagem_id=?"
                                            " AND lat IS NOT NULL ORDER BY ts", (vid,))]
    # remove pontos repetidos (parado no trânsito) para não criar voltas
    limpos = []
    for p in pts:
        if not limpos or _dist_km((limpos[-1]["lat"], limpos[-1]["lon"]), (p["lat"], p["lon"])) > 0.03:
            limpos.append(p)
    if len(limpos) < 2:
        return None
    limpos = _reduzir(limpos)
    coords = ";".join(f"{p['lon']:.6f},{p['lat']:.6f}" for p in limpos)
    # direção do carro em cada ponto: evita encaixar o ponto na faixa do sentido contrário (autoestradas)
    rumos = ";".join(f"{int(p['direcao']) % 360},60" if p.get("direcao") is not None and (p.get("velocidade_kmh") or 0) > 5
                     else "" for p in limpos)
    base = (c.get_def("rota_url") or "https://router.project-osrm.org").rstrip("/")
    url = (f"{base}/route/v1/driving/{coords}?overview=full&geometries=geojson&continue_straight=true"
           f"&bearings={rumos}&radiuses={';'.join(['60'] * len(limpos))}")
    try:
        rota = _pedir(url)
    except c.ErroGateway:
        # sem as direções (alguns pontos podem não ter estrada compatível)
        rota = _pedir(url.split("&bearings=")[0] + f"&radiuses={';'.join(['100'] * len(limpos))}")
    km = rota["distance"] / 1000
    # validação: um percurso muito maior do que o odómetro indica encaixes errados (desvios, voltas)
    if v["km"] and km > v["km"] * 1.35 + 2:
        c.log("WARNING", "rotas", f"viagem {vid}: percurso calculado ({km:.1f} km) muito maior do que o odómetro "
                                  f"({v['km']} km); mantém-se o traçado das leituras")
        return None
    linha = [[round(la, 6), round(lo, 6)] for lo, la in rota["geometry"]["coordinates"]]
    with c.db() as con:
        con.execute("INSERT INTO viagem_rota(viagem_id, coordenadas, km, criada) VALUES (?,?,?,?)"
                    " ON CONFLICT(viagem_id) DO UPDATE SET coordenadas=excluded.coordenadas, km=excluded.km,"
                    " criada=excluded.criada", (vid, json.dumps(linha), round(km, 1), c.agora_iso()))
    return len(linha), round(km, 1)


def apagar(vid):
    with c.db() as con:
        con.execute("DELETE FROM viagem_rota WHERE viagem_id=?", (vid,))


def tentar(vid, forcar=True):
    """Para usar depois de fechar/juntar/completar uma viagem: falhas ficam só no log."""
    if not ativo():
        return None
    try:
        return calcular(vid, forcar=forcar)
    except Exception as e:                       # rede, serviço indisponível, etc.
        c.log("WARNING", "rotas", f"viagem {vid}: percurso pelas estradas não calculado: "
                                  f"{getattr(e, 'mensagem', None) or type(e).__name__ + ': ' + str(e)}"[:300])
        return None


def calcular_todas(forcar=False, limite=None):
    """Calcula o percurso das viagens fechadas que ainda não o têm. Devolve contagens."""
    with c.db() as con:
        ids = [r[0] for r in con.execute("SELECT id FROM viagens WHERE estado='fechada' AND pontos >= 2"
                                         + ("" if forcar else " AND id NOT IN (SELECT viagem_id FROM viagem_rota)")
                                         + " ORDER BY inicio DESC")]
    st = {"viagens": len(ids), "calculadas": 0, "ignoradas": 0}
    for vid in ids[:limite] if limite else ids:
        r = tentar(vid, forcar=True)
        st["calculadas" if r else "ignoradas"] += 1
    return st
