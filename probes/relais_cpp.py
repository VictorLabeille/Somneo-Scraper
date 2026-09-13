"""Relais CPP — capturer une session cloud du reveil, en clair. Sonde, 2026-09-13.

Protocole ecrit et commite AVANT la mesure (plan technique du collecteur, §1, P2bis voie 1).

P2 a montre que le reveil ne prend l'heure qu'a l'ouverture d'une session avec la plateforme
Philips (`www.ecdinterface.philips.com`, port `backend`), et P2ter que sa config de temps
(`wutms.tmser`/`tmsrc`) ne s'ecrit pas. La seule voie qui garde l'afficheur juste, une fois le
cloud coupe, est de repondre nous-memes a cette session. Pour cela il faut d'abord SAVOIR ce
qu'elle contient : requete et reponse d'une vraie ouverture de session.

La liaison est en HTTP CLAIR (`backend.url` commence par `http://`) et la carte est sur le meme
segment L2 que le reveil. On se met donc sur le chemin par EMPOISONNEMENT ARP — se faire passer
pour la passerelle aupres du reveil, et pour le reveil aupres de la passerelle —, on active le
routage (`ip_forward`) pour que le trafic continue de passer (relais transparent, le reveil
reste en ligne), et on OBSERVE : on ne termine rien, on ne modifie rien, on lit le clair qui
transite. Aucune ecriture, ni sur le reveil ni sur la passerelle ; ARP et routage se restaurent
a la sortie.

Ce que ca etablit :
  - la forme d'une requete du reveil vers `RequestHandler.ashx` (methode, chemin, en-tetes, corps) ;
  - la reponse de Philips, et si elle porte l'heure (c'est le but) ;
  - de quoi, plus tard, ecrire un repondeur sur la carte.

ROOT REQUIS (AF_PACKET, ip_forward). N'ECRIT RIEN sur le reveil : capture.py peut continuer a
tourner en parallele (il parle a l'API locale du reveil en 443, chemin direct, non affecte par
l'empoisonnement de la route vers la passerelle). Borne par `--duree` (defaut 90 min) ; s'arrete
des qu'une requete ET une reponse cloud sont captees. Restauration ARP (vraies MAC) + ip_forward
d'origine dans tous les cas (fin, signal, exception).

DONNEES : le clair capte porte l'identite du reveil (serial, MAC, cppid) et les donnees de la
chambre en cours de televersement. Le releve BRUT reste sur la carte, jamais versionne. Le
nettoyage pour le depot ne garde que la STRUCTURE (voir nettoyer_prealables.py).

Reserves (docs/somneo-api.md §5) : fait transiter le trafic du reveil par une carte au WiFi
instable → attendu, en journee, courte fenetre. A ne pas lancer la nuit ni avant une alarme.

`--selftest` : valide le parseur de trames sur une trame synthetique, sans root, sans reseau.
"""
import argparse
import base64
import os
import signal
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from somneo_probe import discover                                      # noqa: E402

ETH_P_ALL = 0x0003
ETH_P_IP = 0x0800
ETH_P_ARP = 0x0806
ARP_REPLY = 2
ARP_REQUEST = 1
POISON_INTERVALLE = 2.0        # s entre deux salves d'empoisonnement
DUREE_DEFAUT = 90              # minutes
IFACE = "wlan0"


def mac_octets(m):
    return bytes(int(x, 16) for x in m.split(":"))


def mac_texte(b):
    return ":".join("%02x" % x for x in b)


def ip_passerelle():
    """Passerelle par defaut, lue dans /proc/net/route (hex little-endian)."""
    with open("/proc/net/route") as fh:
        for ligne in fh.readlines()[1:]:
            champs = ligne.split()
            if champs[1] == "00000000" and int(champs[3], 16) & 2:
                return socket.inet_ntoa(struct.pack("<L", int(champs[2], 16)))
    return None


def mac_locale(iface):
    with open(f"/sys/class/net/{iface}/address") as fh:
        return fh.read().strip()


def ip_locale(cible):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((cible, 80))
        return s.getsockname()[0]
    finally:
        s.close()


def trame_arp(op, exp_mac, exp_ip, cible_mac, cible_ip):
    """Trame Ethernet + ARP prete a emettre (vers `cible_mac`)."""
    eth = mac_octets(cible_mac) + mac_octets(exp_mac) + struct.pack("!H", ETH_P_ARP)
    arp = struct.pack("!HHBBH", 1, ETH_P_IP, 6, 4, op)
    arp += mac_octets(exp_mac) + socket.inet_aton(exp_ip)
    arp += mac_octets(cible_mac) + socket.inet_aton(cible_ip)
    return eth + arp


def resoudre_mac(sock, iface, mon_mac, mon_ip, ip_cible, essais=5):
    """MAC de `ip_cible` : d'abord /proc/net/arp, sinon requete ARP puis relecture."""
    def lire_table():
        with open("/proc/net/arp") as fh:
            for ligne in fh.readlines()[1:]:
                c = ligne.split()
                if c[0] == ip_cible and c[3] != "00:00:00:00:00:00":
                    return c[3]
        return None
    m = lire_table()
    if m:
        return m
    broadcast = "ff:ff:ff:ff:ff:ff"
    for _ in range(essais):
        sock.send(trame_arp(ARP_REQUEST, mon_mac, mon_ip, broadcast, ip_cible))
        time.sleep(0.3)
        m = lire_table()
        if m:
            return m
    return None


# ---- parseur de trames --------------------------------------------------------------------

def parse_trame(trame, ip_reveil):
    """Extrait un segment TCP a destination/en provenance du reveil sur le port 80.

    Rend None si la trame ne concerne pas ce qu'on capture, sinon un dict :
    {sens, pair_ip, pair_port, seq, payload(bytes)}. `sens` : "montant" (reveil->cloud) ou
    "descendant" (cloud->reveil)."""
    if len(trame) < 14:
        return None
    if struct.unpack("!H", trame[12:14])[0] != ETH_P_IP:
        return None
    ip = trame[14:]
    if len(ip) < 20:
        return None
    ihl = (ip[0] & 0x0F) * 4
    if ip[9] != 6:                                   # protocole != TCP
        return None
    src = socket.inet_ntoa(ip[12:16])
    dst = socket.inet_ntoa(ip[16:20])
    total = struct.unpack("!H", ip[2:4])[0]
    tcp = ip[ihl:total]
    if len(tcp) < 20:
        return None
    sport, dport, seq = struct.unpack("!HHI", tcp[0:8])
    data_off = (tcp[12] >> 4) * 4
    payload = tcp[data_off:]
    if src == ip_reveil and dport == 80:
        return {"sens": "montant", "pair_ip": dst, "pair_port": dport, "seq": seq,
                "payload": payload}
    if dst == ip_reveil and sport == 80:
        return {"sens": "descendant", "pair_ip": src, "pair_port": sport, "seq": seq,
                "payload": payload}
    return None


INTERESSANT = (b"RequestHandler", b"HTTP/1", b"POST ", b"GET ", b"ecdinterface")


# ---- capture ------------------------------------------------------------------------------

def capturer(a):
    from somneo_session import Journal, installer_signaux, verifier, lever_arret, Arret, trouver
    installer_signaux()
    rel = Journal(f"relais-cpp-{time.strftime('%Y%m%dT%H%M%S')}.jsonl")

    def fin(**kw):
        rel.ecrire(type="fin", **kw)
        rel.fermer()

    if os.geteuid() != 0:
        fin(abandon="root requis (AF_PACKET, ip_forward)")
        print("ABANDON : lancer avec sudo", file=sys.stderr)
        return 1
    reveil = trouver(a.hote)
    passerelle = ip_passerelle()
    if not reveil or not passerelle:
        fin(abandon=f"reveil={reveil} passerelle={passerelle} : introuvable")
        return 1
    mon_mac = mac_locale(IFACE)
    mon_ip = ip_locale(reveil)

    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
    sock.bind((IFACE, 0))
    sock.settimeout(1.0)

    mac_reveil = resoudre_mac(sock, IFACE, mon_mac, mon_ip, reveil)
    mac_passerelle = resoudre_mac(sock, IFACE, mon_mac, mon_ip, passerelle)
    if not mac_reveil or not mac_passerelle:
        fin(abandon=f"MAC introuvable (reveil={mac_reveil}, passerelle={mac_passerelle})")
        print("ABANDON : resolution ARP echouee", file=sys.stderr)
        sock.close()
        return 1
    # Les MAC/IP sont propres au reseau : journalisees dans le brut (non versionne), pas ici.
    rel.ecrire(type="debut", duree_min=a.duree, note="MAC/IP dans le brut uniquement")
    print(f"reveil {reveil} / passerelle {passerelle} / carte {mon_ip} — MAC resolues, "
          f"empoisonnement en cours (Ctrl-C pour arreter et restaurer)", flush=True)

    # ip_forward pour rester un relais transparent ; send_redirects a 0 pour que le noyau
    # n'envoie pas d'ICMP redirect au reveil (qui reapprendrait la vraie passerelle et
    # deferait l'empoisonnement). Les deux sont sauves et restaures.
    bascules = {"/proc/sys/net/ipv4/ip_forward": "1",
                "/proc/sys/net/ipv4/conf/all/send_redirects": "0",
                f"/proc/sys/net/ipv4/conf/{IFACE}/send_redirects": "0"}
    origines = {}
    for chemin, val in bascules.items():
        try:
            with open(chemin) as fh:
                origines[chemin] = fh.read().strip()
            with open(chemin, "w") as fh:
                fh.write(val + "\n")
        except OSError as exc:
            rel.ecrire(type="avertissement", chemin=chemin, erreur=str(exc))
    forward0 = origines.get("/proc/sys/net/ipv4/ip_forward", "0")

    stop = threading.Event()

    def empoisonner():
        while not stop.is_set():
            # au reveil : "la passerelle, c'est moi" ; a la passerelle : "le reveil, c'est moi"
            sock.send(trame_arp(ARP_REPLY, mon_mac, passerelle, mac_reveil, reveil))
            sock.send(trame_arp(ARP_REPLY, mon_mac, reveil, mac_passerelle, passerelle))
            stop.wait(POISON_INTERVALLE)

    def restaurer():
        stop.set()
        for _ in range(5):        # reapprend les vraies MAC aux deux extremites
            sock.send(trame_arp(ARP_REPLY, mac_passerelle, passerelle, mac_reveil, reveil))
            sock.send(trame_arp(ARP_REPLY, mac_reveil, reveil, mac_passerelle, passerelle))
            time.sleep(0.2)
        for chemin, val in origines.items():
            try:
                with open(chemin, "w") as fh:
                    fh.write(f"{val}\n")
            except OSError:
                pass
        rel.ecrire(type="restauration", sysctl_remis=origines)
        print(f"restauration : vraies MAC reannoncees, sysctl remis {origines}", flush=True)

    th = threading.Thread(target=empoisonner, daemon=True)
    th.start()

    vu_montant = vu_descendant = False
    fin_prevue = time.monotonic() + a.duree * 60
    try:
        while time.monotonic() < fin_prevue:
            verifier()
            try:
                trame = sock.recv(65535)
            except socket.timeout:
                continue
            seg = parse_trame(trame, reveil)
            if not seg or not seg["payload"]:
                continue
            marque = next((m.decode() for m in INTERESSANT if m in seg["payload"]), None)
            rel.ecrire(type="segment", sens=seg["sens"], pair_ip=seg["pair_ip"],
                       pair_port=seg["pair_port"], seq=seg["seq"], marque=marque,
                       payload_b64=base64.b64encode(seg["payload"]).decode())
            if marque:
                print(f"  >>> {seg['sens']} {seg['pair_ip']}:{seg['pair_port']} "
                      f"[{marque}] {len(seg['payload'])} o", flush=True)
                if seg["sens"] == "montant":
                    vu_montant = True
                else:
                    vu_descendant = True
                if vu_montant and vu_descendant:
                    print("session cloud captee (requete + reponse) — arret", flush=True)
                    break
    except Arret:
        print("interrompu — restauration", flush=True)
    finally:
        lever_arret()
        restaurer()
        fin(requete_captee=vu_montant, reponse_captee=vu_descendant)
        sock.close()
    print(f"-> {rel.nom}", flush=True)
    return 0


# ---- selftest -----------------------------------------------------------------------------

def selftest():
    """Fabrique une trame eth/ip/tcp reveil->cloud port 80 et verifie le parseur."""
    reveil, cloud = "192.168.1.97", "1.2.3.4"
    payload = b"POST /DevicePortalICPRequestHandler/RequestHandler.ashx HTTP/1.0\r\n\r\n"
    tcp = struct.pack("!HHIIBBHHH", 44100, 80, 1000, 0, (5 << 4), 0x18, 65535, 0, 0) + payload
    ip = struct.pack("!BBHHHBBH", 0x45, 0, 20 + len(tcp), 0, 0, 64, 6, 0)
    ip += socket.inet_aton(reveil) + socket.inet_aton(cloud)
    eth = mac_octets("aa:bb:cc:dd:ee:ff") + mac_octets("11:22:33:44:55:66")
    eth += struct.pack("!H", ETH_P_IP)
    seg = parse_trame(eth + ip + tcp, reveil)
    assert seg is not None, "trame non reconnue"
    assert seg["sens"] == "montant", seg["sens"]
    assert seg["pair_ip"] == cloud, seg["pair_ip"]
    assert seg["pair_port"] == 80
    assert b"RequestHandler" in seg["payload"]
    # une trame sans rapport doit etre ignoree
    autre = mac_octets("11:22:33:44:55:66") * 2 + struct.pack("!H", 0x86DD)
    assert parse_trame(autre, reveil) is None
    print("selftest OK : parseur valide")
    return 0


def main():
    p = argparse.ArgumentParser(description="relais CPP — capture d'une session cloud du reveil")
    p.add_argument("--selftest", action="store_true", help="valide le parseur, sans root")
    p.add_argument("--duree", type=int, default=DUREE_DEFAUT, help="minutes avant arret force")
    p.add_argument("--hote", help="adresse imposee (faux reveil) ; sinon SSDP")
    a = p.parse_args()
    if a.selftest:
        return selftest()
    return capturer(a)


if __name__ == "__main__":
    sys.exit(main())
