#!/usr/bin/env python3
"""Deco X60 soft reboot via its encrypted local API — LANtern actuator.

Why: python-kasa can't drive the plug (TPAP firmware) and a power-cut can't
self-recover (the plug rides the mesh it reboots). But the recurring 5 GHz-
degradation fault leaves the unit ALIVE and manageable, so we reboot it through
its own local API instead — no plug, no HA, no bootstrapping problem.

Ported from the async, HA-coupled ha-tplink-deco api.py to a small sync script
using only `cryptography` + stdlib. Login: RSA-encrypt the password, AES-CBC
session, RSA-signed requests.

  --login-test          log in, print stok, do NOT reboot   (default if no --reboot)
  --list                log in and print the deco list (to get exact MACs)
  --reboot MAC [MAC...] actually reboot these deco MAC(s)
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import secrets
import ssl
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import padding as sympad
from cryptography.hazmat.primitives.asymmetric import padding as asympad
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

AES_DIGITS = 16
MIN_AES = 10 ** (AES_DIGITS - 1)
MAX_AES = 10 ** AES_DIGITS - 1
PKCS1_HEADER = 11


def byte_len(n: int) -> int:
    return (int(math.log2(n)) + 8) >> 3


def rsa_encrypt(n: int, e: int, plaintext: bytes) -> str:
    """PKCS#1 v1.5, TP-Link style: chunk the plaintext, hex-concat the blocks."""
    pub = RSAPublicNumbers(e, n).public_key()
    per = byte_len(n) - PKCS1_HEADER
    out = ""
    for i in range(0, len(plaintext), per):
        out += pub.encrypt(plaintext[i : i + per], asympad.PKCS1v15()).hex()
    return out


def aes_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    padder = sympad.PKCS7(algorithms.AES.block_size).padder()
    data = padder.update(plaintext) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return enc.update(data) + enc.finalize()


def aes_decrypt(key: bytes, iv: bytes, ct: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return dec.update(ct) + dec.finalize()


class Deco:
    def __init__(self, host: str, username: str, password: str):
        self.host = host.rstrip("/")
        self.username = username
        self.password = password
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self.aes_key = secrets.randbelow(MAX_AES - MIN_AES) + MIN_AES
        self.aes_iv = secrets.randbelow(MAX_AES - MIN_AES) + MIN_AES
        self.aes_key_b = str(self.aes_key).encode()
        self.aes_iv_b = str(self.aes_iv).encode()
        self.seq = self.stok = self.cookie = None
        self.pw_n = self.pw_e = self.sign_n = self.sign_e = None

    def _post(self, stok_path: str, form: str, data) -> dict:
        url = f"{self.host}/cgi-bin/luci/;stok={stok_path}?form={form}"
        body = data.encode() if isinstance(data, str) else data
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        with urllib.request.urlopen(req, context=self.ctx, timeout=15) as r:
            raw = r.read()
            for h in r.headers.get_all("Set-Cookie") or []:
                m = re.search(r"(sysauth=[a-f0-9]+)", h)
                if m:
                    self.cookie = m.group(1)
        return json.loads(raw)

    def _encode(self, payload) -> str:
        pj = json.dumps(payload, separators=(",", ":"))
        data = base64.b64encode(aes_encrypt(self.aes_key_b, self.aes_iv_b, pj.encode())).decode()
        s = self.seq + len(data)
        h = hashlib.md5(f"{self.username}{self.password}".encode()).hexdigest()
        sign_text = f"k={self.aes_key}&i={self.aes_iv}&h={h}&s={s}"
        sign = rsa_encrypt(self.sign_n, self.sign_e, sign_text.encode())
        return f"sign={sign}&data={urllib.parse.quote_plus(data)}"

    def _decrypt(self, data_b64: str) -> dict:
        pt = aes_decrypt(self.aes_key_b, self.aes_iv_b, base64.b64decode(data_b64))
        try:
            unp = sympad.PKCS7(algorithms.AES.block_size).unpadder()
            pt = unp.update(pt) + unp.finalize()
        except Exception:
            pass
        end = pt.rfind(b"}")
        return json.loads(pt[: end + 1] if end != -1 else pt)

    def login(self) -> str:
        r = self._post("/login", "keys", json.dumps({"operation": "read"}))
        pw = r["result"]["password"]
        self.pw_n, self.pw_e = int(pw[0], 16), int(pw[1], 16)

        r = self._post("/login", "auth", json.dumps({"operation": "read"}))
        key = r["result"]["key"]
        self.sign_n, self.sign_e = int(key[0], 16), int(key[1], 16)
        self.seq = r["result"]["seq"]

        enc_pw = rsa_encrypt(self.pw_n, self.pw_e, self.password.encode())
        payload = {"params": {"password": enc_pw}, "operation": "login"}
        r = self._post("/login", "login", self._encode(payload))
        data = self._decrypt(r["data"])
        if data.get("error_code") != 0:
            raise SystemExit(f"LOGIN FAILED error_code={data.get('error_code')} data={data}")
        self.stok = data["result"]["stok"]
        if not self.cookie:
            raise SystemExit("LOGIN: no sysauth cookie returned")
        return self.stok

    def list_decos(self) -> list:
        r = self._post(f"{self.stok}/admin/device", "device_list",
                       self._encode({"operation": "read"}))
        return self._decrypt(r["data"])["result"]["device_list"]

    def reboot(self, macs) -> dict:
        payload = {"operation": "reboot", "params": {"mac_list": [{"mac": m} for m in macs]}}
        r = self._post(f"{self.stok}/admin/device", "system", self._encode(payload))
        data = self._decrypt(r["data"])
        if data.get("error_code") != 0:
            raise SystemExit(f"REBOOT FAILED data={data}")
        return data


def load_password(env_file: str) -> str:
    with open(env_file) as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("DECO_PASSWORD="):
                return line[len("DECO_PASSWORD="):]
    raise SystemExit(f"DECO_PASSWORD not found in {env_file}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="https://10.0.50.216")
    ap.add_argument("--username", default="admin")
    ap.add_argument("--env", default=os.path.expanduser("~/deco-reboot/.deco.env"))
    ap.add_argument("--login-test", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--reboot", nargs="+", metavar="MAC")
    args = ap.parse_args()

    d = Deco(args.host, args.username, load_password(args.env))
    stok = d.login()
    print(f"LOGIN OK — stok={stok[:10]}…  sysauth cookie={'yes' if d.cookie else 'no'}")

    if args.list:
        for dev in d.list_decos():
            print(f"  deco mac={dev.get('device_mac') or dev.get('mac')} "
                  f"role={dev.get('role')} nickname={dev.get('custom_nickname')} "
                  f"model={dev.get('device_model')} ip={dev.get('device_ip')}")
    if args.reboot:
        print(f"rebooting {args.reboot} …")
        print("reboot result:", d.reboot(args.reboot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
