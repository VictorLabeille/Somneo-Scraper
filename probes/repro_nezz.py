"""Le bug rapporte par Nezz sur l'issue #13 — reproduit, explique, et date.

Nezz, 18 juin 2024 : regler la luminosite puis l'affichage-toujours-allume depuis la meme
automation Home Assistant fait perdre le premier reglage. « The newly set brightness being
set for a split second, which gets reverted back to the initial setting by the always on
switch. » Un delai de deux secondes suffit a corriger.

L'hypothese naturelle est la concurrence (issue #8). Elle est FAUSSE, et c'est ce que cette
sonde etablit. La cause est dans le code client, visible a l'oeil nu :

    # juin 2024 — pysomneo/__init__.py
    payload['dspon'] = state if state != None else self.data['display_always_on']
    payload['brght'] = brightness if brightness else self.data['display_brightness']
    self.alarm_status = self._put('wusts', payload = payload)   # <- self.data JAMAIS rafraichi

`set_display` envoie TOUJOURS les deux champs : celui qu'on lui passe, et l'autre repris du
cache local `self.data`. Or ce cache n'est pas mis a jour apres le PUT. Le deuxieme appel
renvoie donc a l'appareil la valeur d'AVANT le premier appel, et l'annule. Le delai de deux
secondes marche parce que le coordinator de Home Assistant a le temps de rafraichir entre-temps
— pas parce qu'il desengorge quoi que ce soit.

Trois volets :
  1. la sequence de Nezz avec le `pysomneo` installe (5.0.6) — le bug est-il encore la ?
  2. la meme sequence avec la logique de juin 2024, cache fige a la main — le bug apparait-il ?
  3. deux `set_display` menes en parallele — la concurrence ajoute-t-elle un defaut a part ?

PRECAUTIONS — reveil en service dans une chambre :
  - etat initial lu AVANT, restaure dans un `finally`, restauration verifiee par relecture ;
  - valeurs choisies dans les bornes mesurees (1-6), jamais au-dela ;
  - une seule ecriture a la fois hors du volet 3, qui teste justement le contraire.
"""
import json
import ssl
import sys
import threading
import time
import urllib.request

sys.path.insert(0, ".")
from somneo_probe import discover, get

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

ESPACEMENT = 0.4


def put(ip, port, payload, timeout=15):
    """PUT sur un port. Renvoie toujours un dict, ne leve jamais."""
    url = f"https://{ip}/di/v1/products/1/{port}"
    data = json.dumps(payload).encode()
    rec = {"payload": payload, "ok": False}
    try:
        req = urllib.request.Request(url, data=data, method="PUT",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
            rec["status"] = resp.status
            rec["reponse"] = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
            rec["ok"] = True
    except urllib.error.HTTPError as exc:
        rec["status"] = exc.code
        rec["error"] = f"HTTP {exc.code}"
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def etat(ip):
    """(dspon, brght) tels que l'appareil les rapporte."""
    r = get(ip, 1, "wusts")
    b = r.get("body") or {}
    return b.get("dspon"), b.get("brght")


def volet_bibliotheque(ip, cible_brght, cible_dspon):
    """La sequence de Nezz, jouee par le `pysomneo` reellement installe."""
    print("=" * 66)
    print("1. Sequence de Nezz avec le pysomneo installe")
    print("=" * 66)
    try:
        import pysomneo
        from pysomneo import Somneo
    except ImportError as exc:
        print(f"  pysomneo indisponible : {exc}")
        return {"erreur": str(exc)}

    version = getattr(pysomneo, "__version__", "inconnue")
    print(f"  version : {version}")
    s = Somneo(ip)
    s.fetch_data()
    time.sleep(ESPACEMENT)

    print(f"  set_display(brightness={cible_brght})")
    s.set_display(brightness=cible_brght)
    time.sleep(ESPACEMENT)
    apres_1 = etat(ip)
    print(f"    -> appareil : dspon={apres_1[0]} brght={apres_1[1]}")

    time.sleep(ESPACEMENT)
    print(f"  set_display(state={cible_dspon})   <- l'appel qui ecrasait, en 2024")
    s.set_display(state=cible_dspon)
    time.sleep(ESPACEMENT)
    apres_2 = etat(ip)
    print(f"    -> appareil : dspon={apres_2[0]} brght={apres_2[1]}")

    perdu = apres_2[1] != cible_brght
    print(f"\n  brght demande={cible_brght}, final={apres_2[1]} "
          f"=> {'PERDU, le bug est present' if perdu else 'TENU, le bug est corrige'}")
    return {"version": version, "apres_1": apres_1, "apres_2": apres_2, "perdu": perdu}


def volet_logique_2024(ip, cible_brght, cible_dspon, cache):
    """La logique de juin 2024, rejouee a la main : cache fige, jamais rafraichi.

    `cache` est le (dspon, brght) lu avant la sequence — c'est exactement ce que
    `self.data` contenait, et continuait de contenir, dans la version de l'epoque.
    """
    print()
    print("=" * 66)
    print("2. La meme sequence avec la logique de juin 2024 (cache fige)")
    print("=" * 66)
    cache_dspon, cache_brght = cache
    print(f"  cache client fige a dspon={cache_dspon} brght={cache_brght}")

    p1 = {"dspon": cache_dspon, "brght": cible_brght}
    print(f"  set_display(brightness={cible_brght}) -> PUT {p1}")
    put(ip, "wusts", p1)
    time.sleep(ESPACEMENT)
    apres_1 = etat(ip)
    print(f"    -> appareil : dspon={apres_1[0]} brght={apres_1[1]}")

    time.sleep(ESPACEMENT)
    # Le cache n'a pas bouge : brght y vaut toujours l'ancienne valeur.
    p2 = {"dspon": cible_dspon, "brght": cache_brght}
    print(f"  set_display(state={cible_dspon})      -> PUT {p2}   <- reinjecte l'ancien brght")
    put(ip, "wusts", p2)
    time.sleep(ESPACEMENT)
    apres_2 = etat(ip)
    print(f"    -> appareil : dspon={apres_2[0]} brght={apres_2[1]}")

    perdu = apres_2[1] != cible_brght
    print(f"\n  brght demande={cible_brght}, final={apres_2[1]} "
          f"=> {'PERDU — le symptome de Nezz, reproduit' if perdu else 'tenu'}")
    return {"cache": cache, "apres_1": apres_1, "apres_2": apres_2, "perdu": perdu}


def volet_parallele(ip, cible_brght, cible_dspon):
    """Deux PUT vraiment simultanes : la concurrence ajoute-t-elle un defaut distinct ?"""
    print()
    print("=" * 66)
    print("3. Deux ecritures en parallele — la concurrence, cette fois pour de vrai")
    print("=" * 66)
    res = {}
    depart = threading.Barrier(2)

    def ecrire(nom, payload):
        depart.wait()
        res[nom] = put(ip, "wusts", payload, timeout=25)

    fils = [
        threading.Thread(target=ecrire, args=("brght", {"brght": cible_brght})),
        threading.Thread(target=ecrire, args=("dspon", {"dspon": cible_dspon})),
    ]
    for f in fils:
        f.start()
    for f in fils:
        f.join()

    for nom, r in sorted(res.items()):
        print(f"  {nom:<6} -> {r.get('status') or r.get('error')}")
    time.sleep(1.0)
    final = etat(ip)
    print(f"  etat final : dspon={final[0]} brght={final[1]}")
    return {"resultats": res, "final": final}


def main():
    ip = discover()
    if not ip:
        print("Somneo introuvable en SSDP — sonde abandonnee.")
        return 1

    initial = etat(ip)
    if initial == (None, None):
        print("Etat initial illisible — on n'ecrit rien.")
        return 1
    print(f"Etat initial : dspon={initial[0]} brght={initial[1]}\n")

    # Cibles distinctes de l'etat initial, dans les bornes mesurees (1-6).
    cible_brght = 4 if initial[1] != 4 else 2
    cible_dspon = not initial[0]

    releve = {"initial": {"dspon": initial[0], "brght": initial[1]},
              "cible": {"brght": cible_brght, "dspon": cible_dspon}}
    try:
        releve["bibliotheque"] = volet_bibliotheque(ip, cible_brght, cible_dspon)
        time.sleep(1.0)

        # Le cache de 2024, c'est l'etat d'AVANT la sequence — celui que `fetch_data` avait
        # depose et que le PUT ne rafraichissait pas. Le relire apres le volet 1 le remplirait
        # avec la valeur cible et le test ne prouverait plus rien : on repart donc de l'etat
        # initial, apres avoir remis l'appareil dedans.
        put(ip, "wusts", {"dspon": initial[0], "brght": initial[1]})
        time.sleep(1.0)
        depart = etat(ip)
        print(f"\n  (appareil ramene a dspon={depart[0]} brght={depart[1]} avant le volet 2)")
        time.sleep(ESPACEMENT)
        releve["logique_2024"] = volet_logique_2024(ip, cible_brght, cible_dspon, depart)
        time.sleep(1.0)
        releve["parallele"] = volet_parallele(ip, cible_brght, cible_dspon)
    finally:
        print()
        print("=" * 66)
        print("Restauration")
        print("=" * 66)
        time.sleep(1.0)
        put(ip, "wusts", {"dspon": initial[0], "brght": initial[1]})
        time.sleep(1.0)
        final = etat(ip)
        ok = final == initial
        print(f"  attendu dspon={initial[0]} brght={initial[1]} | relu dspon={final[0]} "
              f"brght={final[1]} -> {'OK' if ok else 'ECHEC, A VERIFIER A LA MAIN'}")
        releve["restauration"] = {"attendu": initial, "relu": final, "ok": ok}

    nom = time.strftime("repro-nezz-%Y%m%dT%H%M%S.json")
    with open(nom, "w", encoding="utf-8") as fh:
        json.dump(releve, fh, ensure_ascii=False, indent=2, default=str)
    print(f"\nReleve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
