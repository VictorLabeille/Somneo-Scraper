"""P2ter — Peut-on donner au reveil un serveur de temps a nous ? Sonde de refutation, 2026-09-13.

Protocole ecrit et commite AVANT la mesure (plan technique du collecteur, §1, P2ter).

P2 a montre que `PUT products/0/time {"datetime"}` est refuse (500/500) et que le reveil ne
corrige son horloge qu'a l'ouverture d'une session cloud (20/20, `derive_horloge.py`). Couper
internet lui retire donc sa seule source d'heure. Mais le port `wutms` porte deux champs jamais
testes en ecriture : `tmser` (« time server », `http://www.noserver.com` chez nous) et `tmsrc`
(« time source », `irq`). Le nom, le schema `http://` et le defaut « noserver » suggerent un
champ fait pour recevoir l'URL d'un serveur de temps. S'il est inscriptible ET honore, on pointe
le reveil sur un serveur qu'on tient sur la carte : pas de MITM, pas de DNS a detourner (le reveil
vient a l'URL qu'on lui donne), et son protocole de temps se revele au passage.

Le piege de P2 s'applique : une ecriture ignoree ressemble a une ecriture appliquee. On ne se fie
donc jamais au `200` seul — on relit le champ, et surtout on regarde si le reveil FRAPPE a notre
serveur, et si son horloge bouge.

Hypotheses, et ce qui les ferait tomber :
  H10  `tmser` est inscriptible : `PUT wutms {"tmser": <url>}` rend `200` et la valeur relue est
       celle qu'on a ecrite. Tombe sur `422`, ou si la relecture rend l'ancienne valeur.
  H11  Au moins une valeur de `tmsrc` autre que `irq` est acceptee. `422` partout = non.
  H12  Le reveil interroge `tmser` : dans la fenetre de surveillance, notre serveur recoit une
       requete venant de l'IP du reveil. Tombe si rien n'arrive (non concluant sur le rythme,
       concluant sur H10/H11).
  H13  Le reveil applique l'heure servie : son decalage (mesure a la seconde) se rapproche de
       zero apres une requete. Tombe si le decalage ne bouge pas.

Tests, dans l'ordre, chacun relu, avec restauration a la fin :
  T1  `tmser` <- URL d'un serveur local sur la carte. Relu. (H10)
  T2  `tmsrc` <- chaque candidat de CANDIDATS_TMSRC, un par un, relu, restaure a `irq` apres
      chacun. Note lesquels donnent `200`. (H11)
  T3  Surveillance : `tmser` pose (et `tmsrc` au premier candidat accepte, s'il y en a un),
      serveur de temps leve sur la carte, journalisation de toute requete recue ; decalage du
      reveil mesure toutes les 60 s pendant `--duree` minutes. (H12, H13)

Serveur de temps local : `http.server` sur un port haut (pas de root), lie a l'IP de la carte
face au reveil. Il repond `200` a toute requete, avec l'en-tete HTTP `Date` (UTC courant) et un
corps JSON portant l'heure sous plusieurs formes (datetime local, UTC, epoch) — on ne sait pas
encore ce que le reveil attend ; ce premier tir sert surtout a voir S'IL vient et CE qu'il
demande. Le format de reponse s'affinera a un second tir, la requete captee en main.

ECRITURE : `wutms` uniquement (`tmser`, `tmsrc`). Jamais `wualm`, jamais la lumiere, jamais
`fac`, jamais `time`. Aucun effet visible attendu (ni afficheur, ni lumiere, ni son). Abandon si
une alarme est en cours. Restauration : `tmser`, `tmsrc` et tout champ de `wutms` touche remis a
sa valeur d'origine, verifie par relecture ; un instantane large avant/apres verifie qu'aucun
autre port n'a bouge.

Arrete `capture.py` au demarrage (SIGTERM par PID) ; le superviseur la relance a la fin.

`--lecture` : lit `wutms` et l'IP de la carte, aucune ecriture. `--ecriture` : T1 a T3.
"""
import argparse
import http.server
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from email.utils import formatdate

from somneo_session import (Arret, Inattendu, Journal, Session, alarme_en_cours, arreter_capture,
                            dormir, effets_de_bord, heure_appareil, installer_signaux,
                            instantane, lever_arret, trouver, verifier)

CHAMPS_WUTMS = ("tmser", "tmsrc", "tmsyn", "tmupd", "tzhrm", "dstwu")
CANDIDATS_TMSRC = ("ntp", "sntp", "http", "net", "srv", "cpp", "cloud", "manual")
PORT_DEFAUT = 8123
DUREE_DEFAUT = 20        # minutes de surveillance
PAS_MESURE = 60          # s entre deux mesures du decalage
ECRITS = ("1/wutms",)


def ip_carte(hote):
    """IP de la carte sur la route vers le reveil, sans rien emettre (socket UDP non connecte)."""
    cible = hote.split(":")[0]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((cible, 80))
        return s.getsockname()[0]
    finally:
        s.close()


class Serveur:
    """Serveur de temps HTTP minimal, journalisant chaque requete. Lecture seule cote reveil."""

    def __init__(self, ip, port, journal, ip_reveil):
        self.hits = []
        rel, cible = journal, ip_reveil.split(":")[0]

        class H(http.server.BaseHTTPRequestHandler):
            def _repondre(hs):
                maintenant = time.time()
                dt = datetime.fromtimestamp(maintenant)
                corps = (b'{"datetime":"%s","utc":"%s","epoch":%d}'
                         % (dt.isoformat().encode(),
                            datetime.fromtimestamp(maintenant, timezone.utc)
                            .strftime("%Y-%m-%dT%H:%M:%SZ").encode(), int(maintenant)))
                hs.send_response(200)
                hs.send_header("Date", formatdate(maintenant, usegmt=True))
                hs.send_header("Content-Type", "application/json")
                hs.send_header("Content-Length", str(len(corps)))
                hs.end_headers()
                return corps

            def _note(hs, methode):
                longueur = int(hs.headers.get("Content-Length") or 0)
                charge = hs.rfile.read(longueur).decode("utf-8", "replace") if longueur else ""
                hit = {"de": hs.client_address[0], "du_reveil": hs.client_address[0] == cible,
                       "methode": methode, "chemin": hs.path,
                       "entetes": {k: v for k, v in hs.headers.items()}, "corps": charge}
                self.hits.append(hit)
                rel.ecrire(type="hit", **hit)
                print(f"  >>> requete {methode} {hs.path} depuis {hs.client_address[0]}"
                      f"{' (LE REVEIL)' if hit['du_reveil'] else ''}", flush=True)

            def do_GET(hs):
                hs._note("GET"); hs.wfile.write(hs._repondre())

            def do_POST(hs):
                hs._note("POST"); hs.wfile.write(hs._repondre())

            def log_message(hs, *a):
                pass

        self._srv = http.server.ThreadingHTTPServer((ip, port), H)
        self._srv.timeout = 1
        self._th = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def demarrer(self):
        self._th.start()

    def arreter(self):
        self._srv.shutdown()
        self._srv.server_close()


def ecart_grossier(s):
    """Decalage a la seconde pres : heure du reveil - heure de la carte, au milieu de la requete."""
    dt, rec = heure_appareil(s)
    return None if dt is None else round(dt.timestamp() - (rec["t_envoi"] + rec["t_recu"]) / 2, 1)


def garde_alarme(s):
    sts = s.corps(1, "wusts") or {}
    if alarme_en_cours(sts.get("wusts")):
        raise Arret()


def ecrire_champ(s, champ, valeur):
    """PUT wutms {champ: valeur}, relu ; rend (put, valeur_relue, persistant)."""
    put = s.put(1, "wutms", {champ: valeur})
    dormir(0.5)
    relu = (s.corps(1, "wutms") or {}).get(champ)
    return put, relu, relu == valeur


def restaurer(s, rel, origine):
    """Remet chaque champ de wutms touche a sa valeur d'origine, verifie par relecture."""
    actuel = s.corps(1, "wutms") or {}
    remises = []
    for champ in CHAMPS_WUTMS:
        if champ in origine and actuel.get(champ) != origine[champ]:
            put = s.put(1, "wutms", {champ: origine[champ]})
            remises.append({"champ": champ, "put_status": put.get("status")})
    dormir(0.5, interruptible=False)
    final = s.corps(1, "wutms") or {}
    conforme = all(final.get(c) == origine[c] for c in CHAMPS_WUTMS if c in origine)
    rel.ecrire(type="restauration", remises=remises, conforme=conforme)
    print(f"restauration wutms : {'conforme' if conforme else 'NON CONFORME — A VERIFIER'}"
          f" ({len(remises)} champs remis)", flush=True)
    return conforme


def main():
    p = argparse.ArgumentParser(description="P2ter — le serveur de temps du reveil")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--lecture", action="store_true", help="lit wutms et l'IP, aucune ecriture")
    g.add_argument("--ecriture", action="store_true", help="T1 a T3")
    p.add_argument("--hote", help="adresse[:port] imposee (faux reveil) ; sinon SSDP")
    p.add_argument("--port", type=int, default=PORT_DEFAUT, help="port du serveur de temps local")
    p.add_argument("--duree", type=int, default=DUREE_DEFAUT, help="minutes de surveillance (T3)")
    a = p.parse_args()
    installer_signaux()
    mode = "lecture" if a.lecture else "ecriture"
    rel = Journal(f"tmser-{mode}-{time.strftime('%Y%m%dT%H%M%S')}.jsonl")

    def abandon(raison):
        rel.ecrire(type="fin", abandon=raison)
        print(f"ABANDON : {raison}", file=sys.stderr, flush=True)
        return 1

    pids, vivants = arreter_capture()
    rel.ecrire(type="debut", mode=mode, capture_arretee=pids, capture_vivante=vivants)
    if vivants:
        return abandon("capture toujours vivante : deux clients tomberaient ensemble")
    hote = trouver(a.hote)
    if not hote:
        return abandon("Somneo introuvable")
    s = Session(hote)
    sts = s.corps(1, "wusts") or {}
    if alarme_en_cours(sts.get("wusts")):
        return abandon(f"alarme en cours ou wusts illisible ({sts.get('wusts')})")
    wutms0 = s.corps(1, "wutms")
    if not isinstance(wutms0, dict):
        return abandon("wutms illisible")
    carte = ip_carte(hote)
    url = f"http://{carte}:{a.port}/time"
    rel.ecrire(type="etat_initial", wutms=wutms0, ip_carte=carte, url_servie=url)
    print(f"wutms initial : tmser={wutms0.get('tmser')!r} tmsrc={wutms0.get('tmsrc')!r} ; "
          f"IP carte {carte}, URL a servir {url}", flush=True)

    if a.lecture:
        rel.ecrire(type="fin")
        s.fermer()
        print(f"lecture seule terminee -> {rel.nom}", flush=True)
        return 0

    snap0 = instantane(s)
    bilan = {}
    try:
        garde_alarme(s)
        put, relu, persistant = ecrire_champ(s, "tmser", url)                     # T1
        bilan["tmser_inscriptible"] = persistant
        rel.ecrire(type="T1", put=put, relu=relu, persistant=persistant)
        print(f"T1 tmser <- {url} : {put.get('status')} "
              f"{put.get('body') if not put['ok'] else ''} persistant={persistant}", flush=True)

        acceptes = []                                                             # T2
        for v in CANDIDATS_TMSRC:
            garde_alarme(s)
            put, relu, persistant = ecrire_champ(s, "tmsrc", v)
            if persistant:
                acceptes.append(v)
            rel.ecrire(type="T2", valeur=v, put=put, relu=relu, persistant=persistant)
            print(f"T2 tmsrc <- {v!r} : {put.get('status')} persistant={persistant}", flush=True)
            s.put(1, "wutms", {"tmsrc": wutms0.get("tmsrc")})     # remise a l'origine apres chaque
            dormir(0.3)
        bilan["tmsrc_acceptes"] = acceptes

        effets = effets_de_bord(snap0, instantane(s), ECRITS)
        if effets:
            rel.ecrire(type="effet_de_bord", etape="apres T1/T2", effets=effets)
            raise Inattendu(f"effet de bord hors wutms apres T1/T2 : {effets}")

        if not bilan.get("tmser_inscriptible"):
            rel.ecrire(type="T3", saute="tmser non inscriptible : rien a surveiller")
            print("T3 saute : tmser non inscriptible", flush=True)
        else:                                                                     # T3
            garde_alarme(s)
            s.put(1, "wutms", {"tmser": url})
            mode_src = acceptes[0] if acceptes else wutms0.get("tmsrc")
            if acceptes:
                s.put(1, "wutms", {"tmsrc": mode_src})
            dormir(0.5)
            srv = Serveur(carte, a.port, rel, hote)
            srv.demarrer()
            print(f"T3 serveur leve sur {carte}:{a.port}, tmsrc={mode_src!r}, "
                  f"surveillance {a.duree} min", flush=True)
            t0 = time.monotonic()
            suivi = []
            try:
                while (time.monotonic() - t0) < a.duree * 60:
                    verifier()
                    d = ecart_grossier(s)
                    suivi.append({"t_min": round((time.monotonic() - t0) / 60, 1),
                                  "decalage_s": d, "hits": len(srv.hits)})
                    print(f"  +{suivi[-1]['t_min']} min : decalage {d} s, "
                          f"requetes recues {len(srv.hits)}", flush=True)
                    dormir(PAS_MESURE)
            finally:
                srv.arreter()
            bilan["hits_reveil"] = sum(1 for h in srv.hits if h["du_reveil"])
            rel.ecrire(type="T3", tmsrc=mode_src, suivi=suivi, hits=len(srv.hits),
                       hits_reveil=bilan["hits_reveil"])
            print(f"T3 fini : {bilan['hits_reveil']} requete(s) du reveil sur {len(srv.hits)} "
                  f"recue(s)", flush=True)
    except Inattendu as exc:
        rel.ecrire(type="arret_securite", raison=str(exc))
        print(f"ARRET DE SECURITE : {exc}", flush=True)
    except Arret:
        rel.ecrire(type="interruption")
        print("interrompue — restauration", flush=True)
    finally:
        lever_arret()
        ok = restaurer(s, rel, wutms0)
        effets = effets_de_bord(snap0, instantane(s, interruptible=False), ECRITS)
        rel.ecrire(type="effets_finaux", effets=effets)
        rel.ecrire(type="fin", bilan=bilan, restaure=ok)
        s.fermer()
    print(f"-> {rel.nom}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
