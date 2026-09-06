"""Caracterisation de la couche TLS et des erreurs de corps — complement de documentation.

Ce que l'appareil annonce (version, suite, certificat) n'est ecrit nulle part, et le
comportement face a un corps JSON invalide non plus. Deux choses utiles a une bibliotheque
cliente. Lecture seule ; le PUT invalide est refuse par construction, il ne change rien.
"""
import json
import socket
import ssl
import sys
import time
import urllib.request

sys.path.insert(0, ".")
from somneo_probe import discover

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

ip = discover()
out = {}

print("1. Couche TLS")
with socket.create_connection((ip, 443), timeout=10) as brut:
    with CTX.wrap_socket(brut, server_hostname=ip) as s:
        cert = s.getpeercert(binary_form=False)
        out["tls"] = {"version": s.version(), "suite": s.cipher(),
                      "compression": s.compression()}
        print(f"   version : {s.version()}")
        print(f"   suite   : {s.cipher()}")
        der = s.getpeercert(binary_form=True)
        out["tls"]["taille_cert_der"] = len(der) if der else None
        print(f"   certificat : {len(der) if der else 0} octets (DER)")

print("\n2. Versions TLS acceptees")
out["versions"] = {}
for nom, mini, maxi in (("TLSv1", ssl.TLSVersion.TLSv1, ssl.TLSVersion.TLSv1),
                        ("TLSv1.1", ssl.TLSVersion.TLSv1_1, ssl.TLSVersion.TLSv1_1),
                        ("TLSv1.2", ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_2),
                        ("TLSv1.3", ssl.TLSVersion.TLSv1_3, ssl.TLSVersion.TLSv1_3)):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.minimum_version, ctx.maximum_version = mini, maxi
        ctx.set_ciphers("ALL:@SECLEVEL=0")
        with socket.create_connection((ip, 443), timeout=8) as b:
            with ctx.wrap_socket(b, server_hostname=ip) as s:
                out["versions"][nom] = True
                print(f"   {nom:<8} accepte ({s.cipher()[0]})")
    except Exception as exc:
        out["versions"][nom] = False
        print(f"   {nom:<8} refuse ({type(exc).__name__})")
    time.sleep(0.3)

print("\n3. Corps invalides sur un PUT")
out["corps"] = []
cas = [("JSON tronque", b'{"brght":'), ("pas du JSON", b"bonjour"),
       ("tableau au lieu d objet", b"[1,2,3]"), ("corps vide", b""),
       ("cle inconnue", b'{"zzzzz": 1}')]
for libelle, corps in cas:
    url = f"https://{ip}/di/v1/products/1/wusts"
    rec = {"cas": libelle}
    try:
        req = urllib.request.Request(url, data=corps, method="PUT",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=12, context=CTX) as r:
            rec.update(status=r.status,
                       reponse=r.read().decode("utf-8", errors="replace")[:150])
    except urllib.error.HTTPError as exc:
        rec.update(status=exc.code,
                   reponse=exc.read().decode("utf-8", errors="replace")[:150])
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    print(f"   {libelle:<24} -> {rec.get('status') or rec.get('error')}  "
          f"{rec.get('reponse','')[:70]}")
    out["corps"].append(rec)
    time.sleep(0.4)

with open(f"tls-{time.strftime('%Y%m%dT%H%M%S')}.json", "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2, default=str)
print("\nreleve ecrit")
