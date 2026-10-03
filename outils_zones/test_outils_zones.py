"""Tests unitaires des outils de réglage des zones (outils_zones/), sur
des images SYNTHÉTIQUES en mémoire : aucun accès DVR/Jetson requis,
exécutable n'importe où (voir NOTES-SESSION.md).

Usage :
    python3 -m unittest outils_zones.test_outils_zones -v
"""

import os
import sys
import unittest
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _commun  # noqa: E402
import quadrillage  # noqa: E402
import seuils  # noqa: E402
import vitesse_lecture  # noqa: E402
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


class TestAmplitudeParSeuil(unittest.TestCase):
    """seuils.py : amplitude_zone calculée UNE fois sur les images,
    fraction_au_dessus évaluée ensuite pour chaque seuil sans jamais
    retoucher aux images — reproduit le constat terrain (seuil 10 trop
    sensible le soir, seuil 25 plus sûr) sur des données synthétiques."""

    def test_bruit_amplitude_15_compte_avec_seuil_10_pas_avec_25(self):
        zone = (100, 100, 150, 150)
        images = [image_grise_unie(100) for _ in range(15)]
        # Amplitude exactement 15 sur toute la zone (derniere image a 115) :
        # un bruit/leger mouvement parasite plausible, pas un vrai mouvement.
        x1, y1, x2, y2 = zone
        images[-1] = images[-1].copy()
        images[-1][y1:y2, x1:x2] = 115
        amplitude = seuils.amplitude_zone(images, zone)

        self.assertGreater(seuils.fraction_au_dessus(amplitude, 10), 50.0)
        self.assertEqual(seuils.fraction_au_dessus(amplitude, 25), 0.0)

    def test_variation_forte_comptee_avec_tous_les_seuils(self):
        zone = (100, 100, 150, 150)
        images = [image_grise_unie(100) for _ in range(15)]
        x1, y1, x2, y2 = zone
        images[-1] = images[-1].copy()
        images[-1][y1:y2, x1:x2] = 250  # amplitude 150 : vrai mouvement net
        amplitude = seuils.amplitude_zone(images, zone)

        for seuil in (10, 15, 18, 20, 25):
            self.assertGreater(
                seuils.fraction_au_dessus(amplitude, seuil), 0.0,
                f"un vrai mouvement net doit être détecté même au seuil le plus strict ({seuil})."
            )


class TestVerdictEtPourcentage(unittest.TestCase):
    def test_pourcentage_secondes_en_mouvement(self):
        fractions = [10.0, 2.0, 8.0, 1.0]  # 2 au-dessus de SEUIL_SECONDE_POURCENT=5%, 2 en dessous
        self.assertEqual(seuils.pourcentage_secondes_en_mouvement(fractions), 50.0)
        self.assertIsNone(seuils.pourcentage_secondes_en_mouvement([]))

    def test_verdict_marche_au_dessus_de_40_pourcent(self):
        self.assertEqual(seuils.verdict(50.0), "marche")
        self.assertEqual(seuils.verdict(40.0), "arret")  # pas strictement superieur : arret
        self.assertEqual(seuils.verdict(10.0), "arret")
        self.assertIsNone(seuils.verdict(None))

    def test_parser_instants_etat_optionnel(self):
        jetons = ["2026-10-01", "20:31:00", "arret", "2026-10-01", "20:50:00"]
        instants = seuils.parser_instants(jetons)
        self.assertEqual(len(instants), 2)
        self.assertEqual(instants[0], (datetime(2026, 10, 1, 20, 31, 0), "arret"))
        self.assertEqual(instants[1][1], None)

    def test_parser_instants_argument_incomplet_leve(self):
        with self.assertRaises(ValueError):
            seuils.parser_instants(["2026-10-01"])


class TestMeilleurCouple(unittest.TestCase):
    """construire_rapport : le couple zone+seuil choisi doit être celui qui
    classe correctement le plus d'instants, departagé par la plus grande
    marge entre la marche la plus faible et l'arrêt le pire."""

    def test_choisit_le_seuil_qui_separe_bien_marche_et_arret(self):
        instant_marche = datetime(2026, 1, 1, 10, 0)
        instant_arret = datetime(2026, 1, 1, 11, 0)
        instants = [(instant_marche, "marche"), (instant_arret, "arret")]
        donnees = {
            # seuil 10 : arret mesure a 90% (> 40%) -> classe "marche" a tort.
            # seuil 25 : marche=90% (ok), arret=2% (ok) -> separe parfaitement.
            instant_marche: {"C": {10: [90.0] * 10, 25: [90.0] * 10}},
            instant_arret: {"C": {10: [90.0] * 10, 25: [2.0] * 10}},
        }
        rapport = seuils.construire_rapport(instants, donnees, [10, 25], ["C"])

        bloc_10 = rapport.split("=== Zone C, seuil 10 ===")[1].split("=== Zone C, seuil 25 ===")[0]
        bloc_25 = rapport.split("=== Zone C, seuil 25 ===")[1].split("=== Meilleur couple ===")[0]
        self.assertIn("Verdict : 1/2 bons", bloc_10)
        self.assertIn("Verdict : 2/2 bons", bloc_25)
        self.assertIn("Meilleur couple", rapport)
        # Le bloc final doit retenir le seuil 25 (100% de bons), pas le 10.
        meilleur = rapport.split("=== Meilleur couple ===")[1]
        self.assertIn("seuil 25", meilleur)
        self.assertNotIn("seuil 10", meilleur)

    def test_instant_non_lu_affiche_sans_faire_planter_le_rapport(self):
        instant_lu = datetime(2026, 1, 1, 10, 0)
        instant_non_lu = datetime(2026, 1, 1, 11, 0)
        instants = [(instant_lu, "marche"), (instant_non_lu, "arret")]
        donnees = {instant_lu: {"C": {10: [90.0] * 10}}}  # instant_non_lu absent

        rapport = seuils.construire_rapport(instants, donnees, [10], ["C"])
        self.assertIn("non lu", rapport)


class FauxLecteur:
    """Simule un cv2.VideoCapture pour vitesse_lecture : rend `n_images`
    images puis (False, None). `echoue_a_l_ouverture` lève à la création."""

    def __init__(self, n_images):
        self._reste = n_images
        self.libere = False

    def read(self):
        if self._reste <= 0:
            return False, None
        self._reste -= 1
        return True, object()

    def release(self):
        self.libere = True


class HorlogeFactice:
    """Avance d'un pas fixe à chaque appel : temps déterministe, sans sleep."""

    def __init__(self, pas=0.01):
        self.t = 0.0
        self.pas = pas

    def __call__(self):
        self.t += self.pas
        return self.t


class TestMesureFlux(unittest.TestCase):
    def test_images_par_seconde_calculees(self):
        # 30 images lues, horloge qui avance de 0,1 s par appel.
        # mesurer_flux appelle l'horloge : t0, après ouverture, puis 2 par
        # tour (test du délai + lecture), puis une fin. On vérifie surtout
        # que images et duree sont cohérents et ips = images/duree.
        lecteur = FauxLecteur(30)
        images, duree, ouverture, interrompu = vitesse_lecture.mesurer_flux(
            lambda: lecteur, total_images=30, delai_max=1000, horloge=HorlogeFactice(0.1))
        self.assertEqual(images, 30)
        self.assertFalse(interrompu)
        self.assertTrue(lecteur.libere)
        self.assertGreater(duree, 0)

    def test_arret_au_delai_maxi(self):
        lecteur = FauxLecteur(10_000)  # beaucoup plus que ce que le délai permet
        images, duree, ouverture, interrompu = vitesse_lecture.mesurer_flux(
            lambda: lecteur, total_images=10_000, delai_max=1.0, horloge=HorlogeFactice(0.1))
        self.assertTrue(interrompu)
        self.assertLess(images, 10_000)
        self.assertTrue(lecteur.libere)

    def test_fin_de_flux_avant_total(self):
        lecteur = FauxLecteur(5)
        images, duree, ouverture, interrompu = vitesse_lecture.mesurer_flux(
            lambda: lecteur, total_images=100, delai_max=1000, horloge=HorlogeFactice(0.1))
        self.assertEqual(images, 5)
        self.assertFalse(interrompu)


class TestVerdictVitesse(unittest.TestCase):
    def _resultat(self, images, duree, erreur=None):
        r = vitesse_lecture.Resultat("test")
        r.images, r.duree_s, r.erreur = images, duree, erreur
        return r

    def test_plus_rapide_que_temps_reel(self):
        r = self._resultat(images=600, duree=30.0)  # 20 img/s > 15
        self.assertAlmostEqual(r.ips, 20.0)
        self.assertEqual(r.verdict(), "plus rapide que le temps réel")

    def test_temps_reel(self):
        r = self._resultat(images=375, duree=30.0)  # 12,5 img/s
        self.assertEqual(r.verdict(), "temps réel")

    def test_echec_affiche_la_cause(self):
        r = self._resultat(images=0, duree=0.0, erreur="2e session refusée")
        self.assertEqual(r.ips, 0.0)
        self.assertIn("2e session refusée", r.verdict())


class TestMasquageIdentifiants(unittest.TestCase):
    def test_masque_user_pass_dans_url_rtsp(self):
        msg = "Impossible d'ouvrir rtsp://admin:Secret123@192.168.1.10:554/cam/playback"
        masque = vitesse_lecture.masquer(msg)
        self.assertNotIn("Secret123", masque)
        self.assertNotIn("admin:", masque)
        self.assertIn("rtsp://***@192.168.1.10", masque)

    def test_masque_http_et_mots_secrets(self):
        msg = "echec http://admin:MotDePasse@10.0.0.5/cgi-bin/loadfile.cgi"
        masque = vitesse_lecture.masquer(msg, mots_secrets=("MotDePasse", "admin"))
        self.assertNotIn("MotDePasse", masque)
        self.assertIn("http://***@10.0.0.5", masque)

    def test_mot_de_passe_isole_est_masque(self):
        # Le mot de passe pourrait apparaître hors d'une URL (message ffmpeg).
        masque = vitesse_lecture.masquer("auth failed for Secret123", mots_secrets=("Secret123",))
        self.assertEqual(masque, "auth failed for ***")


class TestTableauVitesse(unittest.TestCase):
    def _r(self, nom, images, duree, erreur=None, note=None):
        r = vitesse_lecture.Resultat(nom)
        r.images, r.duree_s, r.erreur, r.note = images, duree, erreur, note
        return r

    def test_conclusion_meilleure_methode_et_estimation_5min(self):
        resultats = [
            self._r("lente", 375, 30.0),       # 12,5 img/s
            self._r("rapide", 900, 30.0),      # 30 img/s
            self._r("cassee", 0, 0.0, erreur="refus"),
        ]
        tableau = vitesse_lecture.construire_tableau(resultats, duree=30, timer_actif=False)
        self.assertIn("Meilleure : rapide", tableau)
        # 5 min à 30 img/s : 4500/30 = 150 s < 300.
        self.assertIn("150 s", tableau)
        self.assertIn("OK", tableau)

    def test_avertissement_timer_actif_en_tete(self):
        tableau = vitesse_lecture.construire_tableau(
            [self._r("x", 450, 30.0)], duree=30, timer_actif=True)
        self.assertTrue(tableau.startswith("!!"))
        self.assertIn("ACTIF", tableau)

    def test_aucune_methode_exploitable(self):
        tableau = vitesse_lecture.construire_tableau(
            [self._r("x", 0, 0.0, erreur="délai dépassé")], duree=30, timer_actif=False)
        self.assertIn("Aucune méthode", tableau)


class TestDelaiDepasse(unittest.TestCase):
    def test_avec_delai_marque_le_resultat_si_fn_bloque(self):
        import threading
        r = vitesse_lecture.Resultat("bloquant")
        barriere = threading.Event()

        def bloque():
            barriere.wait(30)  # ne rend pas la main avant le délai

        vitesse_lecture._avec_delai(bloque, delai=0.2, resultat=r)
        self.assertIsNotNone(r.erreur)
        self.assertIn("délai dépassé", r.erreur)
        barriere.set()

    def test_avec_delai_remonte_une_exception_masquee(self):
        r = vitesse_lecture.Resultat("plante")

        def plante():
            raise RuntimeError("echec rtsp://admin:Secret@1.2.3.4/x")

        vitesse_lecture._avec_delai(plante, delai=1.0, resultat=r)
        self.assertIsNotNone(r.erreur)
        self.assertNotIn("Secret", r.erreur)


if __name__ == "__main__":
    unittest.main()
