#!/usr/bin/env python3
# Copyright 2026 Nuno Melo
# SPDX-License-Identifier: Apache-2.0
"""
melcloud.py - MELCloud integrador: cliente simples para a API (não oficial) do MELCloud clássico.

Só usa a biblioteca padrão do Python (sem dependências).

Credenciais via variáveis de ambiente:
    export MELCLOUD_USER="email@exemplo.pt"
    export MELCLOUD_PASS="password"

Exemplos:
    ./melcloud.py list
    ./melcloud.py status 123456
    ./melcloud.py set 123456 --power on --mode cool --temp 22 --fan 3
    ./melcloud.py set 123456 --power off

Notas:
  - Não abusar do polling: a Mitsubishi bloqueia contas com pedidos a mais
    (usar intervalos >= 3-6 minutos).
  - O ContextKey é guardado em ~/.melcloud_token para evitar logins repetidos.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://app.melcloud.com/Mitsubishi.Wifi.Client"
TOKEN_FILE = Path.home() / ".melcloud_token"

DEVICE_TYPES = {0: "ATA (ar-ar)", 1: "ATW (ar-água)", 3: "ERV (ventilação)"}

# Modos de operação ATA
MODES = {"heat": 1, "dry": 2, "cool": 3, "fan": 7, "auto": 8}
MODES_INV = {v: k for k, v in MODES.items()}

# EffectiveFlags ATA (indicam que campos estão a ser alterados)
FLAG_POWER = 0x01
FLAG_MODE = 0x02
FLAG_TEMP = 0x04
FLAG_FAN = 0x08
FLAG_VANE_V = 0x10
FLAG_VANE_H = 0x100


class MELCloud:
    def __init__(self, user, password):
        self.user = user
        self.password = password
        self.key = TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else None

    # ---------- HTTP ----------
    def _req(self, method, path, body=None, auth=True, retry=True):
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        }
        if auth:
            if not self.key:
                self.login()
            headers["X-MitsContextKey"] = self.key
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code == 401 and auth and retry:   # token expirado
                self.login()
                return self._req(method, path, body, auth, retry=False)
            if e.code == 429:
                sys.exit("Erro 429: demasiados pedidos — a conta pode estar bloqueada temporariamente.")
            sys.exit(f"Erro HTTP {e.code}: {e.read().decode(errors='ignore')[:300]}")

    # ---------- Autenticação ----------
    def login(self):
        body = {
            "Email": self.user,
            "Password": self.password,
            "Language": 0,
            "AppVersion": "1.32.1.0",
            "Persist": True,
            "CaptchaResponse": None,
        }
        r = self._req("POST", "/Login/ClientLogin", body, auth=False)
        if not r or r.get("ErrorId") or not r.get("LoginData"):
            sys.exit(f"Login falhou (ErrorId={r.get('ErrorId') if r else '?'}). Verifica credenciais.")
        self.key = r["LoginData"]["ContextKey"]
        TOKEN_FILE.write_text(self.key)
        TOKEN_FILE.chmod(0o600)

    # ---------- Dispositivos ----------
    def devices(self):
        """Devolve lista plana de dispositivos de todos os edifícios/pisos/áreas."""
        out = []
        for b in self._req("GET", "/User/ListDevices") or []:
            s = b.get("Structure", {})
            pools = [s.get("Devices", [])]
            for a in s.get("Areas", []):
                pools.append(a.get("Devices", []))
            for f in s.get("Floors", []):
                pools.append(f.get("Devices", []))
                for a in f.get("Areas", []):
                    pools.append(a.get("Devices", []))
            for pool in pools:
                for d in pool:
                    out.append({
                        "id": d["DeviceID"],
                        "building": d["BuildingID"],
                        "name": d["DeviceName"],
                        "type": d.get("Device", {}).get("DeviceType"),
                    })
        return out

    def find(self, device_id):
        for d in self.devices():
            if d["id"] == device_id:
                return d
        sys.exit(f"Dispositivo {device_id} não encontrado.")

    def get(self, device_id, building_id):
        return self._req("GET", f"/Device/Get?id={device_id}&buildingID={building_id}")

    def set_ata(self, state, power=None, mode=None, temp=None, fan=None, vane_v=None, vane_h=None):
        flags = 0
        if power is not None:
            state["Power"] = power; flags |= FLAG_POWER
        if mode is not None:
            state["OperationMode"] = MODES[mode]; flags |= FLAG_MODE
        if temp is not None:
            state["SetTemperature"] = temp; flags |= FLAG_TEMP
        if fan is not None:
            state["SetFanSpeed"] = fan; flags |= FLAG_FAN
        if vane_v is not None:
            state["VaneVertical"] = vane_v; flags |= FLAG_VANE_V
        if vane_h is not None:
            state["VaneHorizontal"] = vane_h; flags |= FLAG_VANE_H
        if not flags:
            sys.exit("Nada para alterar.")
        state["EffectiveFlags"] = flags
        state["HasPendingCommand"] = True
        return self._req("POST", "/Device/SetAta", state)


# ---------- Output ----------
def table(rows, headers):
    w = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    line = "  ".join("{:<%d}" % n for n in w)
    print(line.format(*headers))
    print("  ".join("-" * n for n in w))
    for r in rows:
        print(line.format(*[str(x) for x in r]))


def show_status(s, dtype):
    if dtype == 0:
        rows = [
            ("Ligado", "Sim" if s.get("Power") else "Não"),
            ("Modo", MODES_INV.get(s.get("OperationMode"), s.get("OperationMode"))),
            ("Temp. ambiente", f'{s.get("RoomTemperature")} °C'),
            ("Temp. definida", f'{s.get("SetTemperature")} °C'),
            ("Ventilador", s.get("SetFanSpeed")),
            ("Lâmina vertical", s.get("VaneVertical")),
            ("Lâmina horizontal", s.get("VaneHorizontal")),
            ("Offline", s.get("Offline")),
        ]
    elif dtype == 1:
        rows = [
            ("Ligado", "Sim" if s.get("Power") else "Não"),
            ("Temp. exterior", f'{s.get("OutdoorTemperature")} °C'),
            ("Zona 1 ambiente", f'{s.get("RoomTemperatureZone1")} °C'),
            ("Zona 1 definida", f'{s.get("SetTemperatureZone1")} °C'),
            ("Depósito AQS", f'{s.get("TankWaterTemperature")} °C'),
            ("Depósito definido", f'{s.get("SetTankWaterTemperature")} °C'),
            ("Offline", s.get("Offline")),
        ]
    else:
        print(json.dumps(s, indent=2, ensure_ascii=False)); return
    table(rows, ("Campo", "Valor"))


def main():
    p = argparse.ArgumentParser(description="Cliente MELCloud")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="Listar dispositivos")
    st = sub.add_parser("status", help="Estado de um dispositivo")
    st.add_argument("id", type=int)
    st.add_argument("--json", action="store_true", help="Mostrar JSON completo")
    se = sub.add_parser("set", help="Alterar um dispositivo ATA (ar condicionado)")
    se.add_argument("id", type=int)
    se.add_argument("--power", choices=["on", "off"])
    se.add_argument("--mode", choices=list(MODES))
    se.add_argument("--temp", type=float)
    se.add_argument("--fan", type=int, help="0=auto, 1..5")
    se.add_argument("--vane-v", type=int, help="0=auto, 1..5, 7=swing")
    se.add_argument("--vane-h", type=int, help="0=auto, 1..5, 8=split, 12=swing")
    a = p.parse_args()

    user, pwd = os.getenv("MELCLOUD_USER"), os.getenv("MELCLOUD_PASS")
    if not user or not pwd:
        sys.exit("Define MELCLOUD_USER e MELCLOUD_PASS.")
    mc = MELCloud(user, pwd)

    if a.cmd == "list":
        devs = mc.devices()
        table([(d["id"], d["building"], d["name"], DEVICE_TYPES.get(d["type"], d["type"]))
               for d in devs], ("DeviceID", "BuildingID", "Nome", "Tipo"))

    elif a.cmd == "status":
        d = mc.find(a.id)
        s = mc.get(d["id"], d["building"])
        print(json.dumps(s, indent=2, ensure_ascii=False)) if a.json else show_status(s, d["type"])

    elif a.cmd == "set":
        d = mc.find(a.id)
        if d["type"] != 0:
            sys.exit("O comando 'set' só suporta unidades ATA (ar-ar).")
        s = mc.get(d["id"], d["building"])
        power = None if a.power is None else a.power == "on"
        r = mc.set_ata(s, power, a.mode, a.temp, a.fan, a.vane_v, a.vane_h)
        print("Comando enviado." if r else "Sem resposta.")
        if r:
            show_status(r, 0)


if __name__ == "__main__":
    main()
