"""M-SEARCH SSDP : trouve le Somneo sur le LAN. Stdlib uniquement."""
import socket, re

MSG = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 3\r\n"
    "ST: urn:philips-com:device:DiProduct:1\r\n\r\n"
).encode()

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
s.settimeout(5)
s.sendto(MSG, ("239.255.255.250", 1900))

seen = {}
try:
    while True:
        data, addr = s.recvfrom(4096)
        txt = data.decode(errors="replace")
        loc = re.search(r"LOCATION:\s*(\S+)", txt, re.I)
        srv = re.search(r"SERVER:\s*(.+)", txt, re.I)
        seen[addr[0]] = (loc.group(1) if loc else "?", srv.group(1).strip() if srv else "?")
except socket.timeout:
    pass

for ip, (loc, srv) in seen.items():
    print(f"TROUVE {ip}\n  LOCATION {loc}\n  SERVER   {srv}")
if not seen:
    print("AUCUNE REPONSE")
