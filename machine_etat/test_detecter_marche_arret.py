"""Test unitaire du correctif du 2026-10-01 (flou avant comparaison).

Construit des frames SYNTHÉTIQUES en mémoire (aucun accès DVR/Jetson requis,
exécutable n'importe où) : un bruit faible, indépendant d'une image à
l'autre, simule ce qu'une caméra produit réellement entre deux images d'une
scène immobile (bruit de capteur, recompression) — c'est précisément ce
qui, sans flou, faisait classer la machine "marche" à 100 % des minutes
alors qu'elle était à l'arrêt (voir NOTES-SESSION.md). Un vrai mouvement est
simulé par un bloc de forte amplitude dans la zone.

Utilise `unittest` (bibliothèque standard) : aucune nouvelle dépendance, pas
de précédent pytest/unittest dans ce dépôt à l'heure où ce test est ajouté.

Usage :
    python3 -m unittest machine_etat.test_detecter_marche_arret -v
    (ou, depuis la racine du dépôt) python3 machine_etat/test_detecter_marche_arret.py
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import detecter_marche_arret as dma  # noqa: E402
from detecter_marche_arret import (  # noqa: E402
    FENETRE_MINUTES, PLAFOND_RATTRAPAGE_MINUTES, RETARD_MINUTES, SEUIL_FRACTION,
    SEUIL_PIXEL, ZONE, ecrire_curseur, fenetre_a_analyser, lire_curseur,
    seconde_avec_mouvement, zone_grise,
)

LARGEUR, HAUTEUR = 1280, 720
GRIS_FOND = 100


def frame_bruitee(graine, amplitude):
    """Frame unie à GRIS_FOND, perturbée d'un bruit indépendant uniforme dans
    [-amplitude, +amplitude] par pixel et par canal — un bruit de capteur/
    recompression typique, PAS un mouvement réel (aucune structure spatiale)."""
    rng = np.random.default_rng(graine)
    bruit = rng.integers(-amplitude, amplitude + 1, size=(HAUTEUR, LARGEUR, 3))
    frame = np.clip(GRIS_FOND + bruit, 0, 255).astype(np.uint8)
    return frame


def frame_avec_objet(graine, amplitude):
    """Comme frame_bruitee, plus un bloc de forte amplitude occupant la
    MOITIÉ de la zone surveillée — simule un vrai changement (passage,
    pièce en mouvement), pas du bruit."""
    frame = frame_bruitee(graine, amplitude)
    x1, y1, x2, y2 = ZONE
    milieu = y1 + (y2 - y1) // 2
    frame[y1:milieu, x1:x2] = 250  # bien au-dessus du fond (100) et du bruit
    return frame


class TestFlouAvantComparaison(unittest.TestCase):
    def test_bruit_faible_entre_deux_images_quasi_identiques_ne_declenche_pas(self):
        # Même amplitude de bruit que la caméra réelle (constatée suffisante
        # pour déclencher à tort SANS flou, voir le test de régression
        # ci-dessous) : deux tirages INDÉPENDANTS de la même scène immobile.
        a = zone_grise(frame_bruitee(graine=1, amplitude=10))
        b = zone_grise(frame_bruitee(graine=2, amplitude=10))
        self.assertFalse(
            seconde_avec_mouvement(a, b),
            "un bruit faible entre deux images d'une scène immobile ne doit "
            "jamais être pris pour un mouvement (c'était le bug du 01/10)."
        )

    def test_changement_de_forte_amplitude_declenche(self):
        a = zone_grise(frame_bruitee(graine=1, amplitude=10))
        b = zone_grise(frame_avec_objet(graine=2, amplitude=10))
        self.assertTrue(
            seconde_avec_mouvement(a, b),
            "un vrai changement (bloc à 250 sur un fond à 100, moitié de la "
            "zone) doit déclencher un mouvement : le flou ne doit pas "
            "masquer un vrai signal, seulement du bruit."
        )

    def test_le_flou_est_necessaire_ici_pas_un_filet_de_securite_inutile(self):
        """Preuve que ce test engage vraiment le flou : SANS lui (comparaison
        directe sur les frames non flloutées, mêmes seuils), le même bruit
        déclenche à tort un mouvement — reproduit le bug du 01/10 pour
        vérifier que ce test l'aurait bien détecté."""
        x1, y1, x2, y2 = ZONE
        import cv2
        a_sans_flou = cv2.cvtColor(frame_bruitee(graine=1, amplitude=10), cv2.COLOR_BGR2GRAY)[y1:y2, x1:x2]
        b_sans_flou = cv2.cvtColor(frame_bruitee(graine=2, amplitude=10), cv2.COLOR_BGR2GRAY)[y1:y2, x1:x2]
        diff = cv2.absdiff(a_sans_flou, b_sans_flou)
        fraction = np.count_nonzero(diff > SEUIL_PIXEL) / diff.size
        self.assertGreater(
            fraction, SEUIL_FRACTION,
            "ce bruit doit dépasser le seuil SANS flou (sinon ce test ne "
            "prouve rien sur l'utilité du flou)."
        )


class TestReprisesSansTrou(unittest.TestCase):
    """fenetre_a_analyser (correctif n°2) : chaque passage doit reprendre
    exactement où le précédent s'est arrêté, sans trou ni chevauchement,
    quel que soit le retard accumulé — voir NOTES-SESSION.md."""

    def setUp(self):
        self._dossier = tempfile.TemporaryDirectory()
        # Isole chaque test dans son propre fichier curseur, sans toucher au
        # vrai machine_etat/curseur.json ni dépendre d'un test précédent.
        self._fichier_original = dma.FICHIER_CURSEUR
        dma.FICHIER_CURSEUR = os.path.join(self._dossier.name, "curseur.json")

    def tearDown(self):
        dma.FICHIER_CURSEUR = self._fichier_original
        self._dossier.cleanup()

    def test_sans_curseur_se_rabat_sur_T_moins_10_T_moins_5(self):
        maintenant = datetime(2026, 10, 1, 3, 20)
        debut, fin, retard = fenetre_a_analyser(maintenant)
        self.assertEqual(fin, maintenant - timedelta(minutes=RETARD_MINUTES))
        self.assertEqual(debut, fin - timedelta(minutes=FENETRE_MINUTES))
        self.assertEqual(retard, timedelta(0))

    def test_reprend_exactement_a_la_fin_du_dernier_passage_sans_trou(self):
        derniere_fin = datetime(2026, 10, 1, 3, 10)
        ecrire_curseur(derniere_fin)
        self.assertEqual(lire_curseur(), derniere_fin)

        # Même si « maintenant » a largement avancé (donc même si le passage
        # précédent a pris du retard), le prochain début doit être EXACTEMENT
        # la fin précédente — c'est précisément ce que l'ancien code (calé
        # sur « maintenant ») ne faisait pas, créant le trou du 03:11-03:13.
        maintenant = datetime(2026, 10, 1, 3, 25)
        debut, fin, retard = fenetre_a_analyser(maintenant)
        self.assertEqual(debut, derniere_fin)
        self.assertGreater(fin, debut, "la fenêtre ne doit jamais être vide ou inversée.")

    def test_le_rattrapage_est_plafonne_pour_ne_jamais_depasser_le_delai_systemd(self):
        # Gros retard accumulé (ex. Jetson éteint une heure) : un seul passage
        # ne doit PAS tenter de tout rattraper d'un coup (risque d'être tué
        # par TimeoutStartSec en pleine fenêtre, voir le service systemd).
        ecrire_curseur(datetime(2026, 10, 1, 2, 0))
        maintenant = datetime(2026, 10, 1, 3, 20)
        debut, fin, retard = fenetre_a_analyser(maintenant)
        self.assertEqual(debut, datetime(2026, 10, 1, 2, 0))
        self.assertLessEqual((fin - debut).total_seconds() / 60, PLAFOND_RATTRAPAGE_MINUTES)
        self.assertGreater(retard, timedelta(0), "le retard restant doit être signalé, pas caché.")

    def test_rien_a_analyser_si_deja_a_jour(self):
        maintenant = datetime(2026, 10, 1, 3, 20)
        ecrire_curseur(maintenant - timedelta(minutes=RETARD_MINUTES))
        debut, fin, retard = fenetre_a_analyser(maintenant)
        self.assertIsNone(debut)
        self.assertIsNone(fin)


if __name__ == "__main__":
    unittest.main()
