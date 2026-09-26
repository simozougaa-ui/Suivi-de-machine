"""Détection de présence par fragments corporels, dans 3 zones autour de
la machine du milieu (le même contour que MACHINE_POLYGON dans
background_detection.py, x≈180-700, y≈90-410 sur le flux 1280x720).

Pourquoi : le conducteur (t-shirt noir) n'est vu que de loin et de dos ;
la poutre horizontale perforée et les montants de la machine lui coupent
le corps en morceaux. Un détecteur de "personne entière" (YOLO) ou de
"changement sur toute la machine" (background_detection) le rate souvent,
car aucun des deux ne s'attend à ne voir qu'un fragment. Ici on ne cherche
plus "une personne" : on surveille 3 petites zones où un bout de son corps
apparaît presque toujours quand il travaille sur cette machine :

- ZONE_TETE : juste au-dessus du bord supérieur de la machine, autour de
  la boucle de câble (près de la roue) — sa tête/casquette y forme une
  masse sombre arrondie.
- ZONE_JAMBES : sous la poutre horizontale perforée, entre le montant
  pâle et le montant sombre — pantalon sombre et chaussures visibles à
  travers les barreaux.
- ZONE_PILE : autour des piles de feuilles à droite du montant sombre —
  son dos ou son bras quand il est penché dessus, ou une feuille portée.
- ZONE_CONVOYEUR (ajoutée le 2026-09-24) : à droite de la tête de
  machine, le long du convoyeur de sortie jusqu'au haut de la palette de
  feuilles blanches — là où il se tient quand il manipule la palette.
  Les 3 premières zones le rataient dans cette position (confirmé sur
  l'enregistrement du 2026-09-23, 13:21:07-13:21:16).

Présence = au moins une zone dont le ratio de pixels changés (par rapport
à une image de référence "machine vide", voir compute_reference_fragments.py)
dépasse son seuil — avec un lissage sur les dernières mesures pour ignorer
un déclenchement isolé (reflet, ombre qui bouge).

Coordonnées calibrées sur calibrate_day.png du 2026-09-05 12:00:20 (grille
+ repérage des éléments fixes de la machine : boucle de câble, roue,
poutre perforée, montants). Calibrage fait à distance, sans accès direct
au flux DVR depuis cet environnement de développement — voir
NOTES-SESSION.md pour le détail et les limites connues, et
test_fragments_on_recording.py / contact_sheet.py pour valider et
réajuster ces zones et seuils sur des enregistrements réels.
"""

import time
from datetime import datetime

import cv2
import numpy as np

# Chaque zone : (x1, y1, x2, y2) en pixels, repère du flux complet (1280x720).
ZONES = {
    "tete": (580, 85, 700, 160),
    "jambes": (520, 210, 640, 340),
    "pile": (630, 220, 700, 400),
    # Couloir entre le montant droit de la machine (x~700) et le poteau
    # vertical (x~790) ; au-dela commence la machine voisine — exclue
    # (autres ouvriers). Borne basse y=250 : juste au-dessus du dessus de
    # la palette de feuilles. L'inclure (essai a y2=300) declenchait des
    # faux positifs permanents des que le niveau de la palette baissait
    # (feuilles prises = changement durable par rapport a la reference).
    # Zone separee plutot que "pile" agrandie : une zone plus grande dilue
    # le ratio de pixels changes et rend la detection moins sensible.
    "convoyeur": (700, 150, 790, 250),
}

# Seuils par zone : fraction de pixels changés au-delà de laquelle la zone
# est considérée "déclenchée". Valeurs de départ prudentes (plutôt
# sensibles) — à affiner sur des enregistrements réels avec
# test_fragments_on_recording.py et contact_sheet.py.
THRESHOLDS = {
    "tete": 0.08,
    "jambes": 0.06,
    "pile": 0.05,
    "convoyeur": 0.06,
}

REFERENCE_FILE = "reference_fragments.png"
DIFF_THRESHOLD = 25  # écart de niveau de gris pour qu'un pixel compte comme "changé"
BLUR_KERNEL = (5, 5)  # atténue le grain de compression et les petits reflets
HISTORY_LENGTH = 3
HISTORY_MIN_HITS = 2  # 2 detections sur les 3 dernieres mesures pour confirmer

# Zone convoyeur uniquement : un chemin de passage la traverse (personnes,
# palettes sans lien avec la machine). Un changement n'y compte comme
# présence que s'il persiste au moins ce nombre de secondes, mesuré en
# temps réel entre la première et la dernière mesure déclenchées
# consécutives (pas en nombre d'images : le filtre reste le même quel que
# soit le rythme d'échantillonnage). Il faut donc au moins 2 mesures ; en
# dessous, c'est un passage rapide, ignoré.
CONVOYEUR_DUREE_MIN_SEC = 4
ZONES_AVEC_DUREE_MIN = {"convoyeur": CONVOYEUR_DUREE_MIN_SEC}


def crop_zone(frame, zone):
    x1, y1, x2, y2 = zone
    return frame[y1:y2, x1:x2]


def _to_gray_blurred(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, BLUR_KERNEL, 0)


_background_mask_cache = {}


def _background_mask(shape):
    """Masque booléen (True = fond) de la taille `shape`, excluant les 3
    zones — pour que le signal qu'on cherche à détecter (un fragment DANS
    une zone) ne puisse jamais influencer sa propre compensation
    d'exposition, même si la zone est grande par rapport à l'image."""
    if shape not in _background_mask_cache:
        mask = np.ones(shape, dtype=bool)
        for (x1, y1, x2, y2) in ZONES.values():
            mask[y1:y2, x1:x2] = False
        _background_mask_cache[shape] = mask
    return _background_mask_cache[shape]


def _background_mean_std(gray):
    mask = _background_mask(gray.shape)
    bg_pixels = gray[mask]
    return float(bg_pixels.mean()), float(bg_pixels.std())


def _background_gain_offset(current_frame_gray, reference_mean, reference_std):
    """Calcule un gain/offset à partir des statistiques du **fond** de
    l'image (tout sauf les 3 zones) pour neutraliser les variations
    d'exposition/luminosité globales (nuages, éclairage qui varie dans la
    journée), sans que le contenu des zones elles-mêmes n'influence la
    normalisation."""
    cur_mean, cur_std = _background_mean_std(current_frame_gray)
    gain = (reference_std / cur_std) if cur_std > 1e-6 else 1.0
    offset = reference_mean - cur_mean * gain
    return gain, offset


class ReferenceImage:
    """Image de référence "machine vide" + ses statistiques de fond
    (niveau de gris, moyenne, écart type hors des 3 zones) pré-calculées
    une seule fois au chargement, réutilisées à chaque image pour la
    normalisation d'exposition (voir _background_gain_offset)."""

    def __init__(self, gray):
        self.gray = gray
        self.mean, self.std = _background_mean_std(gray)


def load_reference(path=REFERENCE_FILE):
    raw = cv2.imread(path)
    if raw is None:
        raise FileNotFoundError(
            f"Image de reference introuvable : {path}. "
            "Lancez compute_reference_fragments.py."
        )
    return ReferenceImage(_to_gray_blurred(raw))


def zone_change_ratio(frame, reference, zone_name, gain, offset):
    zone = ZONES[zone_name]
    current_gray = _to_gray_blurred(crop_zone(frame, zone))
    reference_gray = crop_zone(reference.gray, zone)
    if current_gray.shape != reference_gray.shape:
        raise ValueError(
            f"Taille incoherente pour la zone '{zone_name}' "
            f"({current_gray.shape} vs {reference_gray.shape}) — "
            "la reference a-t-elle ete calculee sur la meme resolution de flux ?"
        )

    adjusted = np.clip(current_gray.astype(np.float32) * gain + offset, 0, 255).astype(np.uint8)
    diff = cv2.absdiff(adjusted, reference_gray)
    _, changed = cv2.threshold(diff, DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)
    return cv2.countNonZero(changed) / changed.size


def evaluate_zones(frame, reference):
    """Retourne {nom_zone: (ratio, declenche)} pour les 3 zones.

    `reference` doit venir de load_reference() (porte les statistiques
    globales pré-calculées utilisées pour la normalisation d'exposition).
    """
    current_frame_gray = _to_gray_blurred(frame)
    gain, offset = _background_gain_offset(current_frame_gray, reference.mean, reference.std)

    result = {}
    for name in ZONES:
        ratio = zone_change_ratio(frame, reference, name, gain, offset)
        result[name] = (ratio, ratio > THRESHOLDS[name])
    return result


def _to_seconds(timestamp):
    if timestamp is None:
        return time.monotonic()
    if isinstance(timestamp, datetime):
        return timestamp.timestamp()
    return float(timestamp)


class FragmentPresenceTracker:
    """Lisse la détection brute sur les HISTORY_LENGTH dernières mesures,
    pour ignorer un déclenchement isolé (reflet, ombre qui passe), après
    avoir appliqué la durée minimum des zones de ZONES_AVEC_DUREE_MIN.

    `update(..., timestamp)` : heure de la mesure (datetime ou secondes).
    Sans timestamp (cas de main.py, en direct), l'horloge du système est
    utilisée. Pour rejouer un enregistrement, passer l'heure de la vidéo.

    Dans les résultats par zone, une zone avec durée minimum n'est marquée
    déclenchée qu'une fois la durée atteinte ; `zones_en_attente` liste
    celles qui dépassent leur seuil mais pas encore leur durée.
    """

    def __init__(self, history_length=HISTORY_LENGTH, min_hits=HISTORY_MIN_HITS):
        self.history_length = history_length
        self.min_hits = min_hits
        self._history = []
        self._debut_declenchement = {}
        self.zones_en_attente = set()

    def _appliquer_duree_min(self, zone_results, now):
        self.zones_en_attente = set()
        for name, duree_min in ZONES_AVEC_DUREE_MIN.items():
            ratio, triggered = zone_results[name]
            if not triggered:
                self._debut_declenchement.pop(name, None)
                continue
            debut = self._debut_declenchement.setdefault(name, now)
            if now - debut >= duree_min:
                continue
            zone_results[name] = (ratio, False)
            self.zones_en_attente.add(name)

    def update(self, frame, reference, timestamp=None):
        zone_results = evaluate_zones(frame, reference)
        self._appliquer_duree_min(zone_results, _to_seconds(timestamp))
        raw_present = any(triggered for _, triggered in zone_results.values())

        self._history.append(raw_present)
        if len(self._history) > self.history_length:
            self._history.pop(0)

        smoothed_present = sum(self._history) >= self.min_hits
        return smoothed_present, zone_results
