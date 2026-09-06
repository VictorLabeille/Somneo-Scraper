"""Socle commun des sondes Somneo — lecture seule, bibliotheque standard uniquement.

Ne contient AUCUN PUT ni POST : ces sondes ne modifient jamais l'appareil.
La Radxa n'ayant ni pip ni requests par defaut, tout tient en stdlib (voir docs/radxa.md).
"""
import json
import re
import socket
import ssl
import time
import urllib.request

SSDP_ADDR = ("239.255.255.250", 1900)
SSDP_ST = "urn:philips-com:device:DiProduct:1"

# Certificat auto-signe cote appareil : la verification est desactivee sciemment.
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def discover(timeout=5):
    """M-SEARCH SSDP. Renvoie l'IP du premier Somneo qui repond, ou None.

    L'adresse est en DHCP et change : ne jamais la figer, toujours redecouvrir.
    """
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 3\r\n"
        f"ST: {SSDP_ST}\r\n\r\n"
    ).encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(timeout)
    try:
        sock.sendto(msg, SSDP_ADDR)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                break
            if re.search(r"Wake-up Light", data.decode(errors="replace"), re.I):
                return addr[0]
    finally:
        sock.close()
    return None


def get(ip, product, port, timeout=15):
    """GET sur un port de l'appareil. Ne leve jamais : renvoie toujours un dict.

    Le champ `ok` dit si la lecture a abouti ; `error` porte la cause sinon.
    C'est ce qui permet d'enregistrer les `500 Timeout` au lieu de les perdre.
    """
    url = f"https://{ip}/di/v1/products/{product}/{port}"
    started = time.monotonic()
    rec = {"ts": time.time(), "product": product, "port": port, "ok": False}
    try:
        req = urllib.request.Request(url, headers={"Connection": "keep-alive"})
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            rec["status"] = resp.status
            try:
                rec["body"] = json.loads(raw)
            except ValueError:
                rec["raw"] = raw
            rec["ok"] = True
    except urllib.error.HTTPError as exc:
        rec["status"] = exc.code
        body = exc.read().decode("utf-8", errors="replace")
        try:
            rec["body"] = json.loads(body)
        except ValueError:
            rec["raw"] = body
        rec["error"] = f"HTTP {exc.code}"
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    rec["ms"] = round((time.monotonic() - started) * 1000, 1)
    return rec


def heap(ip):
    """Tas libre ThreadX, en octets. C'est la mesure qui explique l'issue #8."""
    rec = get(ip, 0, "mem")
    body = rec.get("body") or {}
    return body.get("heap_free"), body.get("heap_size"), rec
