"""Boucle principale de suivi de présence, avec enregistrement des sessions.

Point d'entrée réel du projet (lancé par le service systemd suivi-presence,
voir deploy/). L'ancien src/main.py était la version écrite avant réception
du matériel ; celui-ci le remplace une fois le Jetson en conditions réelles.

Chaque passage de "absent" à "présent" démarre une session. La session
n'est clôturée qu'après ABSENCE_TOLERANCE_SECONDS d'absence continue (pas
à la moindre image manquée) — sans cette tolérance, une seule image où la
détection rate coupe la session en plein milieu d'une vraie présence,
produisant des durées de 0,1s au lieu de la durée réelle.
"""

import csv
import logging
import os
from datetime import datetime

from src.camera_stream import frames
from src.detection import detect_persons
from src.zone import Zone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Zone de travail, en pixels, calibrée le 2026-09-05 sur calibrate_day.png
# (endroit précis où l'opérateur charge la palette, caméra 15).
WORK_ZONE = Zone(x1=500, y1=150, x2=700, y2=300)
MACHINE_NAME = os.getenv("MACHINE_NAME", "Machine 1")
SESSIONS_FILE = "sessions.csv"
ABSENCE_TOLERANCE_SECONDS = 3


def check_presence(frame, zone):
    persons = detect_persons(frame)
    return any(zone.intersects_bbox(person["bbox"]) for person in persons)


def log_session(start, end):
    duration = (end - start).total_seconds()
    file_exists = os.path.isfile(SESSIONS_FILE)
    with open(SESSIONS_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["machine", "debut", "fin", "duree_secondes"])
        writer.writerow([MACHINE_NAME, start.isoformat(), end.isoformat(), round(duration, 1)])


def main():
    logger.info("Demarrage du suivi de presence...")
    session_start = None
    last_present_time = None
    try:
        for frame in frames():
            timestamp = datetime.now()

            try:
                present = check_presence(frame, WORK_ZONE)
            except Exception:
                logger.exception("Erreur lors de la detection, image ignoree.")
                continue

            status = "present" if present else "absent"
            print(f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S')}] {status}")

            if present:
                if session_start is None:
                    session_start = timestamp
                    logger.info("Debut de session detecte.")
                last_present_time = timestamp
            elif session_start is not None:
                absence = (timestamp - last_present_time).total_seconds()
                if absence > ABSENCE_TOLERANCE_SECONDS:
                    log_session(session_start, last_present_time)
                    logger.info(
                        "Session enregistree, duree %.1fs",
                        (last_present_time - session_start).total_seconds(),
                    )
                    session_start = None
                    last_present_time = None

    except KeyboardInterrupt:
        logger.info("Arret demande par l'utilisateur.")


if __name__ == "__main__":
    main()
