"""Tests unitaires des outils de réglage des zones (outils_zones/), sur
des images SYNTHÉTIQUES en mémoire : aucun accès DVR/Jetson requis,
exécutable n'importe où (voir NOTES-SESSION.md).

Usage :
    python3 -m unittest outils_zones.test_outils_zones -v
"""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _commun  # noqa: E402
import quadrillage  # noqa: E402
import zoom  # noqa: E402
from zones import SEUIL_PIXEL, fraction_mouvement  # noqa: E402

LARGEUR, HAUTEUR = 1280, 720
ZONE = (100, 100, 150, 150)  # 50x50


def image_grise_unie(valeur):
    return np.full((HAUTEUR, LARGEUR), valeur, dtype=np.uint8)


class TestFractionMouvement(unittest.TestCase):
    def test_images_identiques_zero_mouvement(self):
        images = [image_grise_unie(100) for _ in range(15)]
        self.assertEqual(fraction_mouvement(images, ZONE), 0.0)

    def test_moitie_de_la_zone_change_fortement(self):
        images = [image_grise_unie(100) for _ in range(15)]
        # Sur la derniere image, la moitie gauche de la zone passe a 250 :
        # amplitude 150 (> SEUIL_PIXEL) sur la moitie de la zone.
        x1, y1, x2, y2 = ZONE
        milieu = x1 + (x2 - x1) // 2
        images[-1] = images[-1].copy()
        images[-1][y1:y2, x1:milieu] = 250
        fraction = fraction_mouvement(images, ZONE)
        self.assertAlmostEqual(fraction, 50.0, delta=1.0)

    def test_bruit_leger_sous_le_seuil_ne_declenche_pas(self):
        rng = np.random.default_rng(0)
        base = 100
        images = [
            np.clip(base + rng.integers(-3, 4, size=(HAUTEUR, LARGEUR)), 0, 255).astype(np.uint8)
            for _ in range(15)
        ]
        # Amplitude max possible ici : ~6, largement sous SEUIL_PIXEL=25.
        self.assertLess(fraction_mouvement(images, ZONE), 1.0)
        self.assertLess(6, SEUIL_PIXEL)


class TestZoomGeometrie(unittest.TestCase):
    def test_vignette_dimensions_et_zoom(self):
        frame = np.zeros((HAUTEUR, LARGEUR, 3), dtype=np.uint8)
        zone = (130, 70, 200, 125)
        vignette = zoom.vignette_zone(frame, zone)
        x1, y1, x2, y2 = zone
        largeur_attendue = (x2 - x1 + 2 * zoom.MARGE) * zoom.ZOOM
        hauteur_attendue = (y2 - y1 + 2 * zoom.MARGE) * zoom.ZOOM
        self.assertEqual(vignette.shape[1], largeur_attendue)
        self.assertEqual(vignette.shape[0], hauteur_attendue)

    def test_vignette_proche_du_bord_est_tronquee_sans_erreur(self):
        # Zone touchant le coin haut-gauche : la marge ne doit pas sortir
        # du cadre (pas d'indices negatifs silencieusement acceptes par
        # numpy, qui fausseraient le recadrage).
        frame = np.zeros((HAUTEUR, LARGEUR, 3), dtype=np.uint8)
        zone = (0, 0, 40, 40)
        vignette = zoom.vignette_zone(frame, zone)
        largeur_attendue = (40 + zoom.MARGE) * zoom.ZOOM  # pas 2x la marge : coupee a 0
        self.assertEqual(vignette.shape[1], largeur_attendue)

    def test_page_empile_deux_lignes(self):
        frame = np.zeros((HAUTEUR, LARGEUR, 3), dtype=np.uint8)
        zones = _commun.charger_zones()
        from datetime import datetime
        instant = datetime(2026, 9, 30, 10, 38, 0)
        page = zoom.construire_page(frame, instant, frame, instant, zones)
        ligne_seule = zoom.construire_ligne(frame, instant, zones)
        self.assertEqual(page.shape[0], 2 * ligne_seule.shape[0])
        self.assertEqual(page.shape[1], ligne_seule.shape[1])


class TestQuadrillage(unittest.TestCase):
    def test_dimensions_agrandies(self):
        frame = np.zeros((HAUTEUR, LARGEUR, 3), dtype=np.uint8)
        resultat = quadrillage.construire_quadrillage(frame)
        x1, y1, x2, y2 = quadrillage.ZONE_COIN
        self.assertEqual(resultat.shape[1], (x2 - x1) * quadrillage.ZOOM)
        self.assertEqual(resultat.shape[0], (y2 - y1) * quadrillage.ZOOM)


if __name__ == "__main__":
    unittest.main()
