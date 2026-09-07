"""`dsi` reagit-il aux appuis sur la facade ? Test cooperatif — quelqu'un doit toucher l'appareil.

Le port `dsi`, mis au jour par le balayage du 2026-09-07, renvoie
`{"keypress": "", "screenid": "NA"}`. Les deux cles sont evocatrices, mais vingt minutes
d'observation au repos ne l'ont jamais vu bouger — personne n'etait devant l'appareil. Tant
qu'un appui n'a pas ete tente, **on ne sait rien** : ecrire que `dsi` expose les touches serait
une lecture de noms de champs, pas une mesure.

Si `keypress` se remplit, c'est une trouvaille qui vaut pour le collecteur : le geste « je me
couche » pourrait se lire sur la facade, sans passer par l'application — et `wungt` a montre
qu'il ne se remplit qu'au geste.

Cadence : une lecture toutes les 400 ms, en reutilisant la connexion. C'est plus rapide que le
palier de 5 s mesure le 2026-09-07, mais sur une duree courte, une seule connexion, et une
latence relevee a 116 ms en keep-alive : la marge est large. `wusts` est relu en parallele pour
situer ce que fait l'appareil.

Lecture seule : rien n'est ecrit. C'est l'operateur qui agit sur la facade, pas la sonde.
"""
import json
import ssl
import sys
import time
import urllib.request

sys.path.insert(0, ".")
from somneo_probe import discover

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

FENETRE = 300      # 5 minutes : l'operateur est deja devant l'appareil
PAS = 0.4
RAPPEL = 30        # un rappel du temps restant, toutes les 30 s


def decouvrir(essais=4):
    for n in range(1, essais + 1):
        ip = discover(timeout=6)
        if ip:
            return ip
        print(f"  SSDP sans reponse (essai {n}/{essais})", flush=True)
        time.sleep(3)
    return None


def lire(opener, ip, port):
    """GET sur connexion reutilisee. Renvoie (corps, ms).

    **Le corps BRUT est conserve, toujours.** Premiere version de cette sonde : elle faisait
    `json.loads` et ne gardait que le resultat. Le 2026-09-07 a 22:11:39, `dsi` a renvoye un
    JSON invalide — « Invalid control character at char 29 » — pendant que l'operateur pressait
    les boutons. C'etait precisement l'evenement cherche, et il a ete perdu : il ne restait que
    le message d'erreur. Un port qui repond mal a quand meme repondu.
    """
    url = f"https://{ip}/di/v1/products/1/{port}"
    t0 = time.monotonic()
    rec = {}
    try:
        req = urllib.request.Request(url, headers={"Connection": "keep-alive"})
        with opener.open(req, timeout=10) as r:
            octets = r.read()
        rec["brut"] = octets.decode("utf-8", errors="replace")
        # Les octets non imprimables, en clair : c'est eux qui cassent le parseur.
        rec["hex"] = octets.hex()
        try:
            rec["json"] = json.loads(rec["brut"] or "{}")
        except ValueError as exc:
            rec["json_invalide"] = str(exc)[:90]
    except Exception as exc:
        rec["_erreur"] = f"{type(exc).__name__}: {exc}"[:80]
    return rec, round((time.monotonic() - t0) * 1000, 1)


def main():
    ip = decouvrir()
    if not ip:
        print("Somneo introuvable en SSDP — sonde abandonnee.")
        return 1

    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX))
    print("=" * 68)
    print(f"Fenetre de {FENETRE // 60} minutes — APPUYER SUR LES BOUTONS DU REVEIL")
    print("=" * 68)
    print("  Ce qui aide le depouillement : espacer les appuis de quelques secondes,")
    print("  et varier (afficheur, lumiere, snooze, molette).")
    print(f"  Debut {time.strftime('%H:%M:%S')}, fin "
          f"{time.strftime('%H:%M:%S', time.localtime(time.time() + FENETRE))}\n", flush=True)

    fin = time.time() + FENETRE
    serie, changements = [], 0
    precedent = None
    dernier_rappel = 0
    tour = 0

    while time.time() < fin:
        tour += 1
        dsi, ms = lire(opener, ip, "dsi")
        # `wusts` une fois sur dix : il situe l'etat sans saturer l'appareil.
        st = None
        if tour % 10 == 0:
            corps, _ = lire(opener, ip, "wusts")
            st = corps.get("wusts") if isinstance(corps, dict) else None

        cle = dsi.get("brut", dsi.get("_erreur", ""))
        ligne = {"ts": time.time(), "dsi": dsi, "ms": ms, "wusts": st}
        serie.append(ligne)

        if precedent is not None and cle != precedent:
            changements += 1
            print(f"  {time.strftime('%H:%M:%S')}  >>> CHANGEMENT #{changements}", flush=True)
            print(f"      brut : {cle!r}", flush=True)
            if "hex" in dsi:
                print(f"      hex  : {dsi['hex']}", flush=True)
            if "json_invalide" in dsi:
                print(f"      JSON INVALIDE : {dsi['json_invalide']}", flush=True)
            print(f"      wusts={st}", flush=True)
        precedent = cle

        reste = int(fin - time.time())
        if reste // RAPPEL != dernier_rappel // RAPPEL and reste > 0:
            print(f"  … {reste} s restantes, {changements} changement(s), "
                  f"{tour} lectures", flush=True)
            dernier_rappel = reste
        time.sleep(PAS)

    erreurs = sum(1 for l in serie if "_erreur" in l["dsi"])
    valeurs = {l["dsi"]["brut"] for l in serie if "brut" in l["dsi"]}
    invalides = [l for l in serie if "json_invalide" in l["dsi"]]
    lat = sorted(l["ms"] for l in serie)

    print()
    print("=" * 68)
    print(f"  {len(serie)} lectures, {erreurs} en erreur, "
          f"latence mediane {lat[len(lat) // 2]:.0f} ms")
    print(f"  valeurs distinctes de dsi : {len(valeurs)}")
    for v in sorted(valeurs):
        print(f"    {v!r}")
    if invalides:
        print(f"\n  {len(invalides)} reponse(s) au JSON invalide — le contenu qui compte :")
        for l in invalides[:5]:
            print(f"    {time.strftime('%H:%M:%S', time.localtime(l['ts']))}  "
                  f"{l['dsi']['brut']!r}")
            print(f"      hex {l['dsi']['hex']}")
    if len(valeurs) <= 1:
        print("\n  dsi n'a pas bouge. Soit personne n'a touche l'appareil, soit ce port")
        print("  n'expose pas les appuis — les deux restent possibles, ne pas trancher.")
    else:
        print(f"\n  dsi BOUGE : {changements} transitions. Le port reagit a quelque chose.")

    nom = time.strftime("dsi-appuis-%Y%m%dT%H%M%S.json")
    with open(nom, "w", encoding="utf-8") as fh:
        json.dump(serie, fh, ensure_ascii=False, indent=2)
    print(f"\nReleve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
