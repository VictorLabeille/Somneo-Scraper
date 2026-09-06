"""Campagne de capture nocturne — Somneo HF3671/01. Lecture seule, stdlib uniquement.

Sert trois fins d'un seul relevé, parce que la nuit ne se rejoue pas :
  1. des fixtures rejouables pour les PR pysomneo (`wusts` en etat d'alarme et de snooze,
     agregats `dataupload/*/data`, port `wungt` absent de la bibliotheque) ;
  2. les questions ouvertes du cadrage : d'ou vient une heure « estimee » (wungt se
     remplit-il sans qu'on ecrive ?), et quelle cadence l'appareil supporte ;
  3. la courbe de reconstitution du tas ThreadX, mesure qui repond a l'issue #8.

Phase calme d'abord : 15 minutes a une seule requete par minute (`mem`), pour voir si le tas
remonte apres la rafale — avec le moins de trafic possible pour ne pas fausser la mesure.
Puis la boucle normale jusqu'a l'arret.

N'ECRIT JAMAIS dans l'appareil : aucun PUT, aucun POST. L'alarme de 6 h 50 doit etre observee,
pas provoquee.
"""
import json
import os
import signal
import sys
import time

from somneo_probe import discover, get

ESPACEMENT = 0.2        # s entre deux requetes : l'appareil sature sous rafale
PHASE_CALME = 15 * 60   # s de mesure de reconstitution du tas
ECHECS_AVANT_REDECOUVERTE = 5

RAPIDE = [(1, "wusts")]                                   # etat : alarme, snooze, veille
MOYEN = [(1, "wusrd"), (1, "wungt")]                      # capteurs et suivi de nuit
LENT = [(1, f"dataupload/{g}.1/data") for g in ("temp", "hum", "snd", "lux")]
TAS = [(0, "mem")]
INSTANTANE = [
    (1, "wualm"), (1, "wualm/aenvs"), (1, "wualm/aalms"), (1, "wudsk"),
    (1, "wulgt"), (1, "wuply"), (1, "wutms"), (1, "wutmr"), (1, "wurlx"),
    (1, "device"), (1, "wifiui"), (1, "dataupload"),
    (0, "time"), (0, "locale"), (0, "backend"), (0, "transport"), (0, "firmware"),
]

_stop = False


def _arret(signum, frame):
    global _stop
    _stop = True


class Journal:
    """JSONL append-only, vide a chaque ligne : une coupure de courant ne perd rien."""

    def __init__(self, dossier):
        os.makedirs(dossier, exist_ok=True)
        self.dossier = dossier
        self._jour = None
        self._fh = None

    def _fichier(self):
        jour = time.strftime("%Y-%m-%d")
        if jour != self._jour:
            if self._fh:
                self._fh.close()
            self._fh = open(os.path.join(self.dossier, f"{jour}.jsonl"), "a",
                            encoding="utf-8")
            self._jour = jour
        return self._fh

    def ecrire(self, rec, phase):
        rec["phase"] = phase
        fh = self._fichier()
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())

    def fermer(self):
        if self._fh:
            self._fh.close()


def main():
    signal.signal(signal.SIGTERM, _arret)
    signal.signal(signal.SIGINT, _arret)

    ip = discover()
    if not ip:
        print("AUCUN SOMNEO TROUVE", file=sys.stderr)
        return 1
    journal = Journal(os.path.join(os.path.dirname(os.path.abspath(__file__)), "capture"))
    echecs = 0
    print(f"capture demarree {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

    def lire(cible, phase):
        """Une lecture, journalisee, avec redecouverte SSDP si l'appareil se derobe."""
        nonlocal ip, echecs
        produit, port = cible
        rec = get(ip, produit, port)
        journal.ecrire(rec, phase)
        if rec["ok"]:
            echecs = 0
        else:
            echecs += 1
            if echecs >= ECHECS_AVANT_REDECOUVERTE:
                # Cause la plus probable : le bail DHCP a change, pas une panne.
                trouve = discover()
                if trouve:
                    ip = trouve
                    journal.ecrire({"ts": time.time(), "evenement": "redecouverte",
                                    "ok": True}, phase)
                echecs = 0
        time.sleep(ESPACEMENT)

    # ---- Phase calme : reconstitution du tas apres la rafale ----
    fin_calme = time.time() + PHASE_CALME
    while not _stop and time.time() < fin_calme:
        lire((0, "mem"), "calme")
        for _ in range(60):
            if _stop:
                break
            time.sleep(1)

    # ---- Boucle normale ----
    for cible in INSTANTANE:
        if _stop:
            break
        lire(cible, "instantane")

    t0 = time.time()
    prochaine = {"rapide": 0.0, "moyen": 0.0, "lent": 0.0, "tas": 0.0, "instantane": 3600.0}
    while not _stop:
        maintenant = time.time() - t0
        for nom, cibles, periode in (
            ("rapide", RAPIDE, 30.0),
            ("moyen", MOYEN, 60.0),
            ("lent", LENT, 300.0),
            ("tas", TAS, 300.0),
            ("instantane", INSTANTANE, 3600.0),
        ):
            if maintenant >= prochaine[nom]:
                for cible in cibles:
                    if _stop:
                        break
                    lire(cible, nom)
                prochaine[nom] = maintenant + periode
        time.sleep(1)

    journal.fermer()
    print(f"capture arretee {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
