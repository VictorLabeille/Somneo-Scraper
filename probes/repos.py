"""Une connexion reutilisee survit-elle au repos ? Second phenomene de l'issue #8.

Le rapporteur ecrit en 2022 : « if I leave the Somneo object to sit around for a minute or so,
the thing hangs ». C'est distinct de la concurrence : une connexion keep-alive laissee inactive.
Si l'appareil la ferme sans alerte TLS propre, la requete suivante echoue — et un sondage
periodique comme celui de Home Assistant tomberait dessus a chaque cycle, SANS concurrence.

Protocole : ouvrir une connexion, la chauffer, attendre T secondes sans rien envoyer, puis
relire sur la MEME connexion. Trois repetitions par palier. Comparaison avec une connexion
neuve juste apres, comme temoin. Lecture seule.
"""
import http.client
import json
import ssl
import sys
import time

from somneo_probe import discover

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
CHEMIN = "/di/v1/products/1/wusrd"
REPOS = [10, 30, 60, 90]
REPETITIONS = 3


def _get(conn):
    debut = time.monotonic()
    try:
        conn.request("GET", CHEMIN, headers={"Connection": "keep-alive"})
        r = conn.getresponse()
        r.read()
        return {"ok": r.status == 200, "status": r.status,
                "ms": round((time.monotonic() - debut) * 1000, 1)}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__,
                "ms": round((time.monotonic() - debut) * 1000, 1)}


def essai(ip, attente):
    """Chauffe une connexion, attend, relit dessus. Puis un temoin sur connexion neuve."""
    conn = http.client.HTTPSConnection(ip, timeout=25, context=CTX)
    chauffe = _get(conn)
    time.sleep(attente)
    apres = _get(conn)
    try:
        conn.close()
    except Exception:
        pass
    neuve = http.client.HTTPSConnection(ip, timeout=25, context=CTX)
    temoin = _get(neuve)
    neuve.close()
    return {"attente": attente, "chauffe": chauffe, "apres_repos": apres, "temoin_neuve": temoin}


def main():
    ip = discover()
    if not ip:
        print("AUCUN SOMNEO TROUVE", file=sys.stderr)
        return 1

    resultats = []
    for attente in REPOS:
        for i in range(REPETITIONS):
            r = essai(ip, attente)
            resultats.append(r)
            a = r["apres_repos"]
            etat = "ok" if a["ok"] else f"ECHEC {a.get('error', a.get('status'))}"
            print(f"  repos {attente:>3}s, essai {i+1}/{REPETITIONS} : "
                  f"relecture {etat} ({a['ms']:.0f} ms)"
                  f" | temoin neuve {'ok' if r['temoin_neuve']['ok'] else 'ECHEC'}",
                  flush=True)
            time.sleep(3)

    nom = f"repos-{time.strftime('%Y%m%dT%H%M%S')}.json"
    with open(nom, "w", encoding="utf-8") as fh:
        json.dump(resultats, fh, ensure_ascii=False, indent=2)

    echecs = {}
    for r in resultats:
        echecs.setdefault(r["attente"], []).append(r["apres_repos"]["ok"])
    print("\nBilan — relecture sur connexion reutilisee apres repos :")
    for attente, oks in sorted(echecs.items()):
        print(f"  {attente:>3}s : {sum(oks)}/{len(oks)} reussies")
    print(f"Releve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
