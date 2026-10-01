"""Sert les images produites dans outils_zones/sorties/ (zones.py, zoom.py,
quadrillage.py) sur le réseau Tailscale, pour les consulter depuis un
téléphone sans copier de fichier. Ne sert QUE ce dossier : aucun autre
fichier du dépôt n'est accessible via ce serveur.

N'écoute jamais sur 0.0.0.0 : seulement l'IP Tailscale (réseau privé entre
appareils) ou, à défaut, 127.0.0.1 — jamais exposé sur le réseau local de
l'atelier.

Usage :
    python3 outils_zones/servir.py
    (Ctrl+C pour arreter)
"""

import functools
import http.server
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _commun  # noqa: E402

PORT = 8000


def adresse_tailscale():
    """Retourne l'IPv4 Tailscale de cette machine, ou None si `tailscale`
    est absent, pas connecté, ou ne renvoie rien d'exploitable."""
    try:
        resultat = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if resultat.returncode != 0:
        return None
    ip = resultat.stdout.strip()
    return ip or None


def main():
    os.makedirs(_commun.SORTIES_DIR, exist_ok=True)

    ip = adresse_tailscale()
    if ip is None:
        ip = "127.0.0.1"
        print(
            "Tailscale introuvable ou non connecte : ecoute sur 127.0.0.1 "
            "uniquement (accessible depuis le Jetson lui-meme, pas depuis "
            "le telephone)."
        )

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=_commun.SORTIES_DIR)
    with http.server.ThreadingHTTPServer((ip, PORT), handler) as serveur:
        print(f"Ouvrir : http://{ip}:{PORT}/")
        try:
            serveur.serve_forever()
        except KeyboardInterrupt:
            print("\nArret du serveur.")


if __name__ == "__main__":
    main()
