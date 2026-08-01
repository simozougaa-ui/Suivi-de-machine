"""Boucle principale de suivi de présence.

Connecte le flux caméra, applique la détection de personnes, vérifie leur
présence dans la zone de travail, et affiche en console le statut
("présent" / "absent") avec horodatage.

L'envoi vers l'API suivi-production-imprimerie n'est pas encore implémenté
(voir NOTES-SESSION.md).
"""

import logging
from datetime import datetime

from camera_stream import frames
from detection import detect_persons
from zone import Zone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Zone de travail provisoire (coordonnées en pixels, image 640x480 supposée).
# À CALIBRER une fois le matériel reçu, sur une image réelle du flux caméra
# (voir NOTES-SESSION.md).
WORK_ZONE = Zone(x1=200, y1=150, x2=600, y2=450)


def check_presence(frame, zone):
    """Retourne True si au moins une personne détectée est dans la zone."""
    persons = detect_persons(frame)
    return any(zone.intersects_bbox(person["bbox"]) for person in persons)


def main():
    logger.info("Démarrage du suivi de présence...")
    try:
        for frame in frames():
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            try:
                present = check_presence(frame, WORK_ZONE)
            except Exception:
                logger.exception("Erreur lors de la détection, image ignorée.")
                continue

            status = "présent" if present else "absent"
            print(f"[{timestamp}] {status}")

            # TODO: envoyer {timestamp, status} vers l'API
            # suivi-production-imprimerie via requests, une fois l'endpoint
            # défini côté API.

    except KeyboardInterrupt:
        logger.info("Arrêt demandé par l'utilisateur.")


if __name__ == "__main__":
    main()
