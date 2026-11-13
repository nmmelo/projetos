"""
Imagem do veículo a partir do compositor do configurador Tesla (render do modelo, não das câmaras).

Duas formas de definir o aspeto (cor, jantes):
  1. TESLA_IMAGE_OPTIONS em tesla.env (recomendado): os códigos do URL da imagem do carro em
     tesla.com → conta → produtos (botão direito → copiar endereço da imagem → parâmetro options).
  2. Automático: a partir do vehicle_config (car_type, exterior_color, wheel_type). Aproximado.

As imagens ficam guardadas em /opt/tesla/imagens durante TESLA_IMAGE_CACHE_DIAS (omissão 30).
"""
import glob
import os
import re
import time

import requests

import common as c

COMPOSITOR = "https://static-assets.tesla.com/v1/compositor/"
VIEWS = ("STUD_3QTR", "STUD_SIDE", "STUD_REAR", "STUD_FRONT", "STUD_SEAT")
PASTA = os.path.join(c.BASE, "imagens")

MODELOS = {"model3": ("m3", "$MDL3"), "modely": ("my", "$MDLY"),
           "models": ("ms", "$MDLS"), "modelx": ("mx", "$MDLX")}

# exterior_color (vehicle_config) -> código de pintura
PINTURA = {
    "SolidBlack": "$PBSB", "Black": "$PBSB", "ObsidianBlack": "$PMBL",
    "PearlWhite": "$PPSW", "White": "$PPSW",
    "MidnightSilver": "$PMNG", "Silver": "$PMSS",
    "DeepBlue": "$PPSB", "Blue": "$PPSB",
    "RedMulticoat": "$PPMR", "Red": "$PPMR",
    "StealthGrey": "$PN01", "StealthGray": "$PN01",
    "UltraRed": "$PR01", "Quicksilver": "$PN00",
    "DiamondBlack": "$PX02", "MidnightCherryRed": "$PR00",
}

# wheel_type (vehicle_config) -> código de jantes (os mais comuns; os restantes ficam com a jante por omissão)
JANTES = {
    "Pinwheel18": "$W38B", "Pinwheel18CapKit": "$W38B", "AeroTurbine18": "$W38B",
    "Stiletto19": "$W39B", "Sportwheel19": "$W39B",
    "Stiletto20": "$W32P", "Uberturbine20": "$W32P",
    "Photon18": "$W38A", "Nova19": "$W39A",
    "Gemini19": "$WY19B", "Apollo19": "$WY19B", "Induction20": "$WY20P", "Uberturbine21": "$WY21P",
}


def _limpar_opcoes(opcoes):
    cods = [x.strip() for x in re.split(r"[,\s]+", opcoes) if x.strip()]
    return ",".join(x if x.startswith("$") else "$" + x for x in cods)


def url_compositor(conf, vcfg, view, size):
    """Devolve (url, origem) — origem 'config' ou 'auto'."""
    if conf.get("TESLA_IMAGE_OPTIONS"):
        modelo = conf.get("TESLA_IMAGE_MODEL", "m3")
        opcoes, origem = _limpar_opcoes(conf["TESLA_IMAGE_OPTIONS"]), "config"
    else:
        vcfg = vcfg or {}
        modelo, cod_modelo = MODELOS.get((vcfg.get("car_type") or "model3").lower(), MODELOS["model3"])
        cods = [cod_modelo]
        if PINTURA.get(vcfg.get("exterior_color", "")):
            cods.append(PINTURA[vcfg["exterior_color"]])
        if JANTES.get(vcfg.get("wheel_type", "")):
            cods.append(JANTES[vcfg["wheel_type"]])
        opcoes, origem = ",".join(cods), "auto"
        if not PINTURA.get(vcfg.get("exterior_color", "")):
            c.log("WARNING", "imagem", f"cor desconhecida '{vcfg.get('exterior_color')}': defina TESLA_IMAGE_OPTIONS")
    url = f"{COMPOSITOR}?model={modelo}&view={view}&size={size}&options={opcoes}&bkba_opt=1"
    return url, origem


def _base(vin, view, size):
    return os.path.join(PASTA, f"{re.sub(r'[^A-Za-z0-9]', '', vin)}_{view}_{size}")


def mimetype(f):
    return {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}.get(f.rsplit(".", 1)[-1], "image/png")


def do_disco(vin, view, size):
    dias = int(c.get_def("imagem_cache_dias") or c.ler_conf().get("TESLA_IMAGE_CACHE_DIAS") or 30)
    for f in glob.glob(_base(vin, view, size) + ".*"):
        if time.time() - os.path.getmtime(f) < dias * 86400:
            return f
    return None


def descarregar(url, vin, view, size):
    try:
        r = requests.get(url, timeout=c.TIMEOUT, headers={"User-Agent": "Mozilla/5.0 tesla-key"})
    except requests.RequestException as e:
        c.log("ERROR", "imagem", f"falha ao obter a imagem: {e}")
        raise c.ErroGateway(502, "falha ao obter a imagem do compositor Tesla")
    tipo = r.headers.get("Content-Type", "")
    if r.status_code != 200 or not tipo.startswith("image/"):
        c.log("ERROR", "imagem", f"compositor respondeu {r.status_code} {tipo}: {url}")
        raise c.ErroGateway(502, f"o compositor Tesla não devolveu uma imagem ({r.status_code})")
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(tipo.split(";")[0], "png")
    os.makedirs(PASTA, exist_ok=True)
    for antigo in glob.glob(_base(vin, view, size) + ".*"):
        os.remove(antigo)
    destino = f"{_base(vin, view, size)}.{ext}"
    with open(destino, "wb") as f:
        f.write(r.content)
    return destino
