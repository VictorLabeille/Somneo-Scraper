"""Découverte : le réveil par SSDP, le collecteur par mDNS (plan §2).

Le réveil est en DHCP : son adresse change, on ne la fige jamais, on redécouvre. Le correctif
qui a manqué à `capture.py` : sur une carte hors réseau, l'envoi SSDP lève
`OSError: Network is unreachable` — on **laisse remonter** cette exception au lieu de rendre
`None`, pour que le collecteur distingue « réveil injoignable » (None) de « carte hors réseau »
(OSError) : ce ne sont pas la même cause d'indisponibilité (§3).
"""
from __future__ import annotations

import logging
import re
import socket
import time

_LOGGER = logging.getLogger(__name__)

SSDP_ADDR = ("239.255.255.250", 1900)
SSDP_ST = "urn:philips-com:device:DiProduct:1"


def discover(timeout: float = 5.0) -> str | None:
    """M-SEARCH SSDP. IP du premier Somneo qui répond, ou None (aucune réponse).

    Lève `OSError` si le réseau est injoignable (carte hors réseau) — le cas que
    `capture.py` confondait avec « pas de réponse »."""
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
        sock.sendto(msg, SSDP_ADDR)          # OSError ici = carte hors réseau : on le laisse filer
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


def ip_locale(cible: str) -> str:
    """IP de la carte sur la route vers `cible`, sans rien émettre."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((cible.split(":")[0], 80))
        return s.getsockname()[0]
    finally:
        s.close()


class MdnsAnnonce:
    """Annonce le collecteur en mDNS : `_somneo-scraper._tcp`, TXT `api=v1` (contrat SleepMaxxer).

    `zeroconf` est optionnel à l'import : sans lui (poste de dev, tests), l'annonce est un no-op
    qui le dit, plutôt qu'une erreur."""

    def __init__(self, config, ip: str | None = None) -> None:
        self.cfg = config
        self.ip = ip
        self._zc = None
        self._info = None

    def demarrer(self) -> bool:
        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ImportError:
            _LOGGER.warning("zeroconf absent : pas d'annonce mDNS")
            return False
        ip = self.ip or ip_locale(SSDP_ADDR[0])
        nom = f"somneo-collector.{self.cfg.mdns_service}.local."
        self._info = ServiceInfo(
            f"{self.cfg.mdns_service}.local.",
            nom,
            addresses=[socket.inet_aton(ip)],
            port=self.cfg.api_port,
            properties={"api": self.cfg.mdns_txt_api},
        )
        self._zc = Zeroconf()
        self._zc.register_service(self._info)
        _LOGGER.info("annonce mDNS %s sur %s:%s", nom, ip, self.cfg.api_port)
        return True

    def arreter(self) -> None:
        if self._zc is not None:
            try:
                if self._info is not None:
                    self._zc.unregister_service(self._info)
            finally:
                self._zc.close()
                self._zc = None
