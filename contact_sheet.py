"""Produit des pages de validation visuelle (lisibles sur téléphone) pour
la détection par fragments sur un enregistrement réel : des vignettes
recadrées en pleine résolution sur la zone utile (pas toute la caméra),
avec le statut prédit et les 3 zones dessinées.

Deux sources possibles :
- Un enregistrement du DVR (--date --debut --duree --pas), comme avant.
  Chaque image échantillonnée est en plus sauvegardée en JPEG dans
  debug_frames/ (voir plus bas) — utile car le DVR écrase ses
  enregistrements au bout de 17 jours.
- Un dossier déjà rempli par une exécution précédente (--depuis-dossier),
  pour régénérer les pages sans avoir besoin du DVR.

Sorties :
- contact_pages/page_01.png, page_02.png, ... — 6 vignettes par page
  (2 colonnes x 3 lignes), consultables depuis le téléphone via
  dashboard.py (page /contact). Les anciennes pages sont supprimées
  avant d'écrire les nouvelles.
- debug_frames/<AAAA-MM-JJ>_<HHMMSS-debut>/frame_<HHMMSS>.jpg — images
  source plein cadre (uniquement en lecture directe du DVR, pas avec
  --depuis-dossier qui les relit).

Usage :
    .venv/bin/python3 contact_sheet.py --date 2026-09-05 --debut 11:40:00 --duree 1200
    .venv/bin/python3 contact_sheet.py --depuis-dossier debug_frames/2026-09-05_114000
"""

import argparse
import glob
import os
import re
from datetime import datetime, timedelta

import cv2
import numpy as np

from src.camera_stream import build_rtsp_playback_url, open_stream
from src.fragment_detection import FragmentPresenceTracker, THRESHOLDS, ZONES, load_reference

# Rectangle (x1, y1, x2, y2) en pixels du flux 1280x720, qui couvre les
# zones (tete/jambes/pile/convoyeur), le bout droit de la machine et la
# palette de feuilles blanches — la partie réellement utile de l'image
# pour cette validation. Élargi à droite (x2 820 -> 860) avec l'ajout de
# la zone convoyeur, pour voir la palette en entier. Vérifié visuellement
# (voir NOTES-SESSION.md) : toutes les zones tiennent entièrement dedans.
CROP = (420, 60, 860, 440)

SAMPLE_INTERVAL_SECONDS = 10
ASSUMED_FPS = 15.0

THUMB_WIDTH = 540  # 2 colonnes cote a cote ~= 1080px de large
PAGE_COLUMNS = 2
PAGE_ROWS = 3
PER_PAGE = PAGE_COLUMNS * PAGE_ROWS

PAGES_DIR = "contact_pages"
FRAMES_DIR = "debug_frames"
FRAME_FILENAME_RE = re.compile(r"^frame_(\d{2})(\d{2})(\d{2})\.jpg$")


def make_thumbnail(frame, present, zone_results, timestamp):
    """Recadre `frame` sur CROP, agrandit, et dessine par-dessus le
    bandeau de statut + les 3 zones (rouge si déclenchée) + leur ratio."""
    x1, y1, x2, y2 = CROP
    cropped = frame[y1:y2, x1:x2]
    crop_h, crop_w = cropped.shape[:2]
    scale = THUMB_WIDTH / crop_w
    thumb_h = int(round(crop_h * scale))
    thumb = cv2.resize(cropped, (THUMB_WIDTH, thumb_h), interpolation=cv2.INTER_CUBIC)

    def to_local(px, py):
        return int(round((px - x1) * scale)), int(round((py - y1) * scale))

    for name, (zx1, zy1, zx2, zy2) in ZONES.items():
        ratio, triggered = zone_results.get(name, (0.0, False))
        color = (0, 0, 255) if triggered else (0, 200, 0)
        lx1, ly1 = to_local(zx1, zy1)
        lx2, ly2 = to_local(zx2, zy2)
        cv2.rectangle(thumb, (lx1, ly1), (lx2, ly2), color, 2)
        # Etiquette a l'INTERIEUR du rectangle (pas au-dessus) : les zones
        # jambes/pile se touchent presque, une etiquette flottant au-dessus
        # se chevaucherait avec celle de la zone voisine.
        label = f"{name} {ratio:.2f}/{THRESHOLDS[name]:.2f}"
        cv2.putText(thumb, label, (lx1 + 4, ly1 + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)

    banner_color = (0, 0, 255) if present else (0, 150, 0)
    cv2.rectangle(thumb, (0, 0), (thumb.shape[1], 22), banner_color, -1)
    label = f"{timestamp.strftime('%H:%M:%S')} {'PRESENT' if present else 'absent'}"
    cv2.putText(thumb, label, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return thumb


def write_pages(thumbnails):
    if os.path.isdir(PAGES_DIR):
        for old in glob.glob(os.path.join(PAGES_DIR, "page_*.png")):
            os.remove(old)
    else:
        os.makedirs(PAGES_DIR)

    if not thumbnails:
        print("ECHEC: aucune vignette produite, aucune page ecrite.", flush=True)
        return 0

    thumb_h, thumb_w = thumbnails[0].shape[:2]
    total_pages = (len(thumbnails) + PER_PAGE - 1) // PER_PAGE

    for page_index in range(total_pages):
        page_thumbs = thumbnails[page_index * PER_PAGE:(page_index + 1) * PER_PAGE]
        page = np.full((PAGE_ROWS * thumb_h, PAGE_COLUMNS * thumb_w, 3), 40, dtype=np.uint8)
        for i, thumb in enumerate(page_thumbs):
            r, c = divmod(i, PAGE_COLUMNS)
            page[r * thumb_h:(r + 1) * thumb_h, c * thumb_w:(c + 1) * thumb_w] = thumb
        path = os.path.join(PAGES_DIR, f"page_{page_index + 1:02d}.png")
        cv2.imwrite(path, page)
        print(f"  {path} ecrite ({len(page_thumbs)} vignette(s))", flush=True)

    return total_pages


def process_samples(sample_iter, reference, source_dir=None):
    """Consomme un itérateur de (timestamp, frame) plein cadre : sauvegarde
    optionnellement chaque frame source, calcule la détection, et
    construit les vignettes. Retourne la liste des vignettes."""
    tracker = FragmentPresenceTracker()
    thumbnails = []

    for timestamp, frame in sample_iter:
        if source_dir is not None:
            os.makedirs(source_dir, exist_ok=True)
            frame_path = os.path.join(source_dir, f"frame_{timestamp.strftime('%H%M%S')}.jpg")
            cv2.imwrite(frame_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 92])

        try:
            present, zone_results = tracker.update(frame, reference)
        except Exception as exc:
            print(f"Erreur detection a {timestamp.strftime('%H:%M:%S')}: {exc}", flush=True)
            continue

        thumbnails.append(make_thumbnail(frame, present, zone_results, timestamp))
        print(f"  vignette {len(thumbnails)} : {timestamp.strftime('%H:%M:%S')} {'PRESENT' if present else 'absent'}", flush=True)

    return thumbnails


def sample_from_recording(jour, debut_heure, duree, pas):
    start = datetime.combine(jour, debut_heure)
    end = start + timedelta(seconds=duree)
    url = build_rtsp_playback_url(start, end)
    print(f"Lecture de l'enregistrement entre {start.time()} et {end.time()}...", flush=True)
    capture = open_stream(url)

    frame_count = 0
    next_sample_frame = 0
    step_frames = max(1, int(pas * ASSUMED_FPS))

    try:
        while True:
            ret, frame = capture.read()
            if not ret or frame is None:
                break
            frame_count += 1

            if frame_count < next_sample_frame:
                continue
            next_sample_frame = frame_count + step_frames

            elapsed = frame_count / ASSUMED_FPS
            timestamp = start + timedelta(seconds=elapsed)
            yield timestamp, frame
    finally:
        capture.release()


def sample_from_folder(folder):
    """Relit les images JPEG déjà sauvegardées par une exécution
    précédente (debug_frames/<date>_<HHMMSS-debut>/frame_<HHMMSS>.jpg),
    sans connexion au DVR."""
    folder = folder.rstrip("/")
    base = os.path.basename(folder)
    date_part = base.split("_")[0]
    try:
        jour = datetime.strptime(date_part, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(
            f"Impossible de deduire la date depuis le nom du dossier '{base}' "
            "(attendu: AAAA-MM-JJ_HHMMSS-debut, ex: 2026-09-05_114000)."
        )

    filenames = sorted(f for f in os.listdir(folder) if FRAME_FILENAME_RE.match(f))
    if not filenames:
        raise ValueError(f"Aucune image frame_HHMMSS.jpg trouvee dans {folder}.")

    for filename in filenames:
        match = FRAME_FILENAME_RE.match(filename)
        hh, mm, ss = match.groups()
        timestamp = datetime.combine(jour, datetime.min.time()).replace(
            hour=int(hh), minute=int(mm), second=int(ss)
        )
        frame = cv2.imread(os.path.join(folder, filename))
        if frame is None:
            print(f"ATTENTION: image illisible, ignoree: {filename}", flush=True)
            continue
        yield timestamp, frame


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debut", help="Heure de debut, format HH:MM:SS (ignore avec --depuis-dossier)")
    parser.add_argument("--duree", type=int, default=1200, help="Duree a couvrir, en secondes (defaut 1200 = 20 min)")
    parser.add_argument("--date", default=None, help="Date, format YYYY-MM-DD (defaut: aujourd'hui)")
    parser.add_argument("--pas", type=int, default=SAMPLE_INTERVAL_SECONDS, help="Secondes entre deux images echantillonnees (defaut 10)")
    parser.add_argument("--depuis-dossier", default=None, help="Regenere les pages depuis un dossier debug_frames/ existant, sans acces au DVR")
    args = parser.parse_args()

    reference = load_reference()

    if args.depuis_dossier:
        thumbnails = process_samples(sample_from_folder(args.depuis_dossier), reference, source_dir=None)
    else:
        if not args.debut:
            print("ECHEC: --debut est requis (sauf avec --depuis-dossier).", flush=True)
            return
        jour = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else datetime.now().date()
        debut_heure = datetime.strptime(args.debut, "%H:%M:%S").time()
        start = datetime.combine(jour, debut_heure)
        source_dir = os.path.join(FRAMES_DIR, f"{jour.isoformat()}_{start.strftime('%H%M%S')}")
        thumbnails = process_samples(
            sample_from_recording(jour, debut_heure, args.duree, args.pas),
            reference,
            source_dir=source_dir,
        )

    total_pages = write_pages(thumbnails)
    print(f"OK: {len(thumbnails)} vignette(s), {total_pages} page(s) dans {PAGES_DIR}/", flush=True)


if __name__ == "__main__":
    main()
