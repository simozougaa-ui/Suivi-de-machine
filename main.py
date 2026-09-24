"""Boucle principale de suivi de présence, avec enregistrement des sessions.

Point d'entrée réel du projet (lancé par le service systemd suivi-presence,
voir deploy/).

Trois méthodes de détection de présence, choisies par la variable
d'environnement PRESENCE_MODE :
- "fragments" (défaut) : détection par fragments corporels dans 3 petites
  zones autour de la machine (src/fragment_detection.py) — la méthode
  retenue, car le conducteur n'est vu que par morceaux depuis cette
  caméra (voir NOTES-SESSION.md).
- "yolo" : détection de personne entière (WORK_ZONE + YOLOv8n), gardée
  pour comparaison/secours, mais peu fiable sur cet angle de caméra.
- "both" : présent si l'une OU l'autre méthode détecte une présence.

Chaque passage de "absent" à "présent" démarre une session. La session
n'est clôturée qu'après ABSENCE_TOLERANCE_SECONDS d'absence continue (pas
à la moindre image manquée) — sans cette tolérance, une seule image où la
détection rate coupe la session en plein milieu d'une vraie présence. La
valeur par défaut (30s) est plus généreuse que l'ancienne (8s) car les
allers-retours du conducteur pour chercher une palette, ou un simple
masquage par la machine, durent souvent 6 à 20s.
"""

import csv
import logging
import os
import time
from datetime import datetime

import cv2

from src.camera_stream import frames
from src.detection import detect_persons
from src.zone import Zone
from src.fragment_detection import FragmentPresenceTracker, ZONES, load_reference

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Zone de travail pour le mode "yolo" (personne entière), en pixels.
WORK_ZONE = Zone(x1=620, y1=100, x2=900, y2=280)

MACHINE_NAME = os.getenv("MACHINE_NAME", "Machine 1")
SESSIONS_FILE = "sessions.csv"
ABSENCE_TOLERANCE_SECONDS = float(os.getenv("ABSENCE_TOLERANCE_SECONDS", "30"))
PRESENCE_MODE = os.getenv("PRESENCE_MODE", "fragments").lower()

DETECTION_INTERVAL_SECONDS = 1.0  # on n'analyse qu'~1 image/s
DEBUG_IMAGE_INTERVAL_SECONDS = 10
DEBUG_IMAGE_FILE = "zones_debug.png"


def check_presence_yolo(frame):
    persons = detect_persons(frame)
    return any(WORK_ZONE.intersects_bbox(person["bbox"]) for person in persons)


def write_debug_image(frame, zone_results):
    """Sauvegarde l'image courante avec les 3 zones dessinées (rouge si
    déclenchée, vert sinon) et leur ratio — pour verifier depuis le
    tableau de bord (voir dashboard.py) sans se connecter en SSH."""
    debug_frame = frame.copy()
    for name, (x1, y1, x2, y2) in ZONES.items():
        ratio, triggered = zone_results.get(name, (0.0, False))
        color = (0, 0, 255) if triggered else (0, 200, 0)
        cv2.rectangle(debug_frame, (x1, y1), (x2, y2), color, 2)
        label = f"{name}:{ratio:.2f}"
        cv2.putText(debug_frame, label, (x1, max(15, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    cv2.imwrite(DEBUG_IMAGE_FILE, debug_frame)


def log_session(start, end):
    duration = (end - start).total_seconds()
    file_exists = os.path.isfile(SESSIONS_FILE)
    with open(SESSIONS_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["machine", "debut", "fin", "duree_secondes"])
        writer.writerow([MACHINE_NAME, start.isoformat(), end.isoformat(), round(duration, 1)])


def main():
    logger.info("Demarrage du suivi de presence (PRESENCE_MODE=%s)...", PRESENCE_MODE)

    run_fragments = PRESENCE_MODE in ("fragments", "both")
    run_yolo = PRESENCE_MODE in ("yolo", "both")

    fragment_reference = None
    tracker = FragmentPresenceTracker()
    if run_fragments:
        try:
            fragment_reference = load_reference()
        except FileNotFoundError:
            # Ne jamais planter le service pour un fichier de calibrage
            # manquant : on se replie sur YOLO (qui n'a besoin d'aucun
            # fichier) en attendant que compute_reference_fragments.py
            # soit lance. Sans ce repli, un simple redemarrage avant la
            # premiere calibration ferait boucler le service en echec.
            logger.warning(
                "reference_fragments.png introuvable : mode 'fragments' "
                "desactive pour cette execution, repli sur YOLO. "
                "Lancez compute_reference_fragments.py puis redemarrez le service."
            )
            run_fragments = False
            run_yolo = True

    session_start = None
    last_present_time = None
    last_detection_time = 0.0
    last_debug_write = 0.0

    try:
        for frame in frames():
            # On lit chaque image du flux pour eviter le retard RTSP, mais
            # on n'analyse (detection + logs) qu'~1 image par seconde.
            now_monotonic = time.monotonic()
            if now_monotonic - last_detection_time < DETECTION_INTERVAL_SECONDS:
                continue
            last_detection_time = now_monotonic

            timestamp = datetime.now()
            zone_results = {}

            try:
                present = False
                if run_fragments:
                    frag_present, zone_results = tracker.update(frame, fragment_reference)
                    present = present or frag_present
                if run_yolo:
                    present = present or check_presence_yolo(frame)
            except Exception:
                logger.exception("Erreur lors de la detection, image ignoree.")
                continue

            status = "present" if present else "absent"
            print(f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S')}] {status}")

            if zone_results and (now_monotonic - last_debug_write) >= DEBUG_IMAGE_INTERVAL_SECONDS:
                try:
                    write_debug_image(frame, zone_results)
                except Exception:
                    logger.exception("Erreur lors de l'ecriture de l'image de debogage.")
                last_debug_write = now_monotonic

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
