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
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import detecter_marche_arret as dma  # noqa: E402
from detecter_marche_arret import (  # noqa: E402
    FENETRE_MINUTES, PLAFOND_RATTRAPAGE_MINUTES, RETARD_MINUTES, SEUIL_FRACTION,
    SEUIL_FRACTION_M2, SEUIL_PIXEL, ZONE, ZONE_M2, amplitude_zone_m2, classer,
    ecrire_curseur, fenetre_a_analyser, gris_floute_m2, lire_curseur,
    seconde_avec_mouvement, zone_grise,
)

LARGEUR, HAUTEUR = 1280, 720
GRIS_FOND = 100


class FakeCapture:
    """Simule un cv2.VideoCapture : consomme `frames` dans l'ordre, via
    grab() (avance sans decoder) ou read() (avance ET decode), comme un
    VRAI flux RTSP le ferait — pas de retour arriere, pas de double lecture
    de la meme position."""

    def __init__(self, frames):
        self._frames = frames
        self._pos = 0
        self.appels_grab = 0
        self.appels_read = 0

    def grab(self):
        self.appels_grab += 1
        if self._pos >= len(self._frames):
            return False
        self._pos += 1
        return True

    def read(self):
        self.appels_read += 1
        if self._pos >= len(self._frames):
            return False, None
        frame = self._frames[self._pos]
        self._pos += 1
        return True, frame

    def release(self):
        pass


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


class TestLectureEfficace(unittest.TestCase):
    """Correctif de perf du 2026-10-01 bis (voir NOTES-SESSION.md) :
    analyser_fenetre() ne doit decoder (read()) QUE l'image gardee par
    seconde, et seulement grab() (sans decoder) les 14 autres — sans changer
    ni la selection des images ni le resultat du classement."""

    def _fond_uniforme(self):
        return np.full((HAUTEUR, LARGEUR, 3), GRIS_FOND, dtype=np.uint8)

    def test_une_image_decodee_par_seconde_le_reste_en_grab(self):
        secondes = 3
        fond = self._fond_uniforme()
        frames = [fond.copy() for _ in range(secondes * 15)]
        fake = FakeCapture(frames)
        debut = datetime(2026, 10, 1, 18, 20, 0)
        fin = debut + timedelta(seconds=secondes)

        with mock.patch.object(dma, "open_stream", return_value=fake), \
                mock.patch.object(dma, "build_rtsp_playback_url", return_value="rtsp://factice"):
            # calculer_machine2=False : seul ce mode reutilise grab() (voir
            # analyser_fenetre) — avec la machine 2 active, toutes les
            # images sont decodees, voir TestMachine2PleineCadence.
            par_minute, _ = dma.analyser_fenetre(debut, fin, calculer_machine2=False)

        # secondes appels a read() (un par image gardee) + UN appel qui
        # echoue en fin de flux (meme comportement de fin qu'avant ce
        # correctif) ; le reste (14 images sur 15) passe par grab().
        self.assertEqual(fake.appels_read, secondes + 1)
        self.assertEqual(fake.appels_grab, secondes * 14)

        minute = debut.replace(second=0, microsecond=0)
        avec, total = par_minute[minute]
        self.assertEqual(total, secondes - 1)  # N images gardees = N-1 comparaisons
        self.assertEqual(avec, 0)

    def test_les_images_jetees_ne_sont_jamais_comparees(self):
        """Les images jetees (grab()) contiennent un changement franc dans la
        zone ; si elles finissaient quand meme par etre comparees (bug de
        selection introduit par ce correctif), ca se verrait ici."""
        fond = self._fond_uniforme()
        bruit = fond.copy()
        x1, y1, x2, y2 = ZONE
        bruit[y1:y2, x1:x2] = 250

        secondes = 2
        frames = [fond.copy() if i % 15 == 0 else bruit.copy() for i in range(secondes * 15)]
        fake = FakeCapture(frames)
        debut = datetime(2026, 10, 1, 18, 20, 0)
        fin = debut + timedelta(seconds=secondes)

        with mock.patch.object(dma, "open_stream", return_value=fake), \
                mock.patch.object(dma, "build_rtsp_playback_url", return_value="rtsp://factice"):
            par_minute, _ = dma.analyser_fenetre(debut, fin, calculer_machine2=False)

        minute = debut.replace(second=0, microsecond=0)
        avec, total = par_minute[minute]
        self.assertEqual(total, secondes - 1)
        self.assertEqual(
            avec, 0,
            "une image jetee (grab()) a influence le resultat : la sélection "
            "d'image ne correspond plus à avant ce correctif."
        )


def _groupe_mouvement_m2():
    """15 images (1 seconde) avec un vrai mouvement dans ZONE_M2 : amplitude
    bien au-dessus de SEUIL_AMPLITUDE_M2 sur toute la zone (alternance
    100/160, amplitude 60)."""
    x1, y1, x2, y2 = ZONE_M2
    images = []
    for i in range(15):
        frame = np.full((HAUTEUR, LARGEUR, 3), GRIS_FOND, dtype=np.uint8)
        frame[y1:y2, x1:x2] = 100 if i % 2 == 0 else 160
        images.append(frame)
    return images


def _groupe_arret_m2(graine):
    """15 images (1 seconde) avec seulement un bruit léger dans ZONE_M2 :
    amplitude sous SEUIL_AMPLITUDE_M2 (±3, amplitude max 6 < 10)."""
    rng = np.random.default_rng(graine)
    x1, y1, x2, y2 = ZONE_M2
    images = []
    for _ in range(15):
        frame = np.full((HAUTEUR, LARGEUR, 3), GRIS_FOND, dtype=np.uint8)
        bruit = rng.integers(-3, 4, size=(y2 - y1, x2 - x1, 3))
        frame[y1:y2, x1:x2] = np.clip(GRIS_FOND + bruit, 0, 255).astype(np.uint8)
        images.append(frame)
    return images


class TestMachine2PleineCadence(unittest.TestCase):
    """Machine 2 (zone C, méthode pleine cadence validée via
    outils_zones/zones.py) : amplitude_zone_m2 + classer() doivent classer
    correctement une minute de marche et une minute d'arrêt."""

    def test_amplitude_zone_m2_detecte_le_mouvement(self):
        fraction = amplitude_zone_m2(_groupe_mouvement_m2())
        self.assertGreater(fraction, SEUIL_FRACTION_M2)

    def test_amplitude_zone_m2_ignore_le_bruit_leger(self):
        fraction = amplitude_zone_m2(_groupe_arret_m2(graine=1))
        self.assertLess(fraction, SEUIL_FRACTION_M2)

    def _minute_via_pipeline_complet(self, groupes):
        """Fait passer `groupes` (une liste de listes de 15 images) par le
        pipeline RÉEL (analyser_fenetre + classer), via FakeCapture — pas
        seulement amplitude_zone_m2 en direct — pour prouver que le
        découpage par seconde/minute de la boucle principale est correct,
        pas seulement la fonction de calcul isolée."""
        frames = [img for groupe in groupes for img in groupe]
        secondes = len(groupes)
        debut = datetime(2026, 10, 1, 18, 20, 0)
        fin = debut + timedelta(seconds=secondes)
        fake = FakeCapture(frames)
        with mock.patch.object(dma, "open_stream", return_value=fake), \
                mock.patch.object(dma, "build_rtsp_playback_url", return_value="rtsp://factice"):
            _, par_minute_m2 = dma.analyser_fenetre(debut, fin, calculer_machine2=True)
        return classer(par_minute_m2)

    def test_minute_marche_classee_marche(self):
        lignes = self._minute_via_pipeline_complet([_groupe_mouvement_m2() for _ in range(60)])
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes[0]["etat"], "marche")
        self.assertEqual(lignes[0]["pourcentage"], 100.0)

    def test_minute_arret_classee_arret(self):
        lignes = self._minute_via_pipeline_complet([_groupe_arret_m2(graine=i) for i in range(60)])
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes[0]["etat"], "arret")
        self.assertEqual(lignes[0]["pourcentage"], 0.0)


class TestMachine1InchangeeParMachine2(unittest.TestCase):
    """Garantie demandée explicitement : ajouter la machine 2 ne doit rien
    changer au résultat de la machine 1 (voir NOTES-SESSION.md)."""

    def test_resultat_machine1_identique_que_machine2_active_ou_non(self):
        secondes = 5
        fond = np.full((HAUTEUR, LARGEUR, 3), GRIS_FOND, dtype=np.uint8)
        debut = datetime(2026, 10, 1, 18, 20, 0)
        fin = debut + timedelta(seconds=secondes)

        resultats = {}
        for actif in (False, True):
            frames = [fond.copy() for _ in range(secondes * 15)]
            fake = FakeCapture(frames)
            with mock.patch.object(dma, "open_stream", return_value=fake), \
                    mock.patch.object(dma, "build_rtsp_playback_url", return_value="rtsp://factice"):
                par_minute_m1, _ = dma.analyser_fenetre(debut, fin, calculer_machine2=actif)
            resultats[actif] = par_minute_m1

        self.assertEqual(
            resultats[False], resultats[True],
            "la machine 1 doit produire EXACTEMENT le même résultat, que la "
            "machine 2 soit active ou non."
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
        debut, fin, retard = fenetre_a_analyser(["machine-1"], maintenant)
        self.assertEqual(fin, maintenant - timedelta(minutes=RETARD_MINUTES))
        self.assertEqual(debut, fin - timedelta(minutes=FENETRE_MINUTES))
        self.assertEqual(retard, timedelta(0))

    def test_reprend_exactement_a_la_fin_du_dernier_passage_sans_trou(self):
        derniere_fin = datetime(2026, 10, 1, 3, 10)
        ecrire_curseur("machine-1", derniere_fin)
        self.assertEqual(lire_curseur("machine-1"), derniere_fin)

        # Même si « maintenant » a largement avancé (donc même si le passage
        # précédent a pris du retard), le prochain début doit être EXACTEMENT
        # la fin précédente — c'est précisément ce que l'ancien code (calé
        # sur « maintenant ») ne faisait pas, créant le trou du 03:11-03:13.
        maintenant = datetime(2026, 10, 1, 3, 25)
        debut, fin, retard = fenetre_a_analyser(["machine-1"], maintenant)
        self.assertEqual(debut, derniere_fin)
        self.assertGreater(fin, debut, "la fenêtre ne doit jamais être vide ou inversée.")

    def test_le_rattrapage_est_plafonne_pour_ne_jamais_depasser_le_delai_systemd(self):
        # Gros retard accumulé (ex. Jetson éteint une heure) : un seul passage
        # ne doit PAS tenter de tout rattraper d'un coup (risque d'être tué
        # par TimeoutStartSec en pleine fenêtre, voir le service systemd).
        ecrire_curseur("machine-1", datetime(2026, 10, 1, 2, 0))
        maintenant = datetime(2026, 10, 1, 3, 20)
        debut, fin, retard = fenetre_a_analyser(["machine-1"], maintenant)
        self.assertEqual(debut, datetime(2026, 10, 1, 2, 0))
        self.assertLessEqual((fin - debut).total_seconds() / 60, PLAFOND_RATTRAPAGE_MINUTES)
        self.assertGreater(retard, timedelta(0), "le retard restant doit être signalé, pas caché.")

    def test_rien_a_analyser_si_deja_a_jour(self):
        maintenant = datetime(2026, 10, 1, 3, 20)
        ecrire_curseur("machine-1", maintenant - timedelta(minutes=RETARD_MINUTES))
        debut, fin, retard = fenetre_a_analyser(["machine-1"], maintenant)
        self.assertIsNone(debut)
        self.assertIsNone(fin)

    def test_la_fenetre_part_du_curseur_le_moins_avance_entre_machines(self):
        """Machine 2 en retard (son dernier envoi a échoué) pendant que
        machine 1 a déjà avancé : la fenêtre partagée doit repartir de
        machine 2, pas sauter sa partie non confirmée."""
        ecrire_curseur("machine-1", datetime(2026, 10, 1, 3, 15))
        ecrire_curseur("machine-2", datetime(2026, 10, 1, 3, 5))
        maintenant = datetime(2026, 10, 1, 3, 25)
        debut, fin, retard = fenetre_a_analyser(["machine-1", "machine-2"], maintenant)
        self.assertEqual(debut, datetime(2026, 10, 1, 3, 5))

    def test_curseur_n_avance_jamais_en_arriere(self):
        """ecrire_curseur ne doit jamais régresser une machine déjà plus
        avancée (voir sa docstring) : la fenêtre partagée peut repartir
        plus tôt que le curseur d'une machine déjà à jour."""
        ecrire_curseur("machine-1", datetime(2026, 10, 1, 3, 15))
        ecrire_curseur("machine-1", datetime(2026, 10, 1, 3, 10))  # plus ancien
        self.assertEqual(lire_curseur("machine-1"), datetime(2026, 10, 1, 3, 15))

    def test_migration_de_l_ancien_format_mono_machine(self):
        """L'ancien format ({"derniere_fin_analysee": ...}) ne concernait que
        machine-1 (seule machine suivie avant l'ajout de la machine 2)."""
        with open(dma.FICHIER_CURSEUR, "w", encoding="utf-8") as f:
            import json
            json.dump({"derniere_fin_analysee": "2026-10-01T03:10"}, f)
        self.assertEqual(lire_curseur("machine-1"), datetime(2026, 10, 1, 3, 10))
        self.assertIsNone(lire_curseur("machine-2"))


class TestEnvoiIndependantParMachine(unittest.TestCase):
    """traiter_envoi_machine (correctif du 2026-10-01 ter) : le curseur
    n'avance qu'après une fenêtre mise en sécurité (envoyée ou mise en file
    durablement), jamais avant — et un échec sur une machine ne doit jamais
    affecter une autre. Voir NOTES-SESSION.md."""

    def setUp(self):
        self._dossier = tempfile.TemporaryDirectory()
        self._fichier_original = dma.FICHIER_CURSEUR
        dma.FICHIER_CURSEUR = os.path.join(self._dossier.name, "curseur.json")

    def tearDown(self):
        dma.FICHIER_CURSEUR = self._fichier_original
        self._dossier.cleanup()

    def test_echec_envoi_machine2_sans_effet_sur_machine1(self):
        fin = datetime(2026, 10, 1, 18, 25)
        lignes_m1 = [{"minute": "2026-10-01T18:20", "etat": "arret",
                      "pourcentage": 0.0, "secondes_analysees": 60}]
        lignes_m2 = [{"minute": "2026-10-01T18:20", "etat": "marche",
                      "pourcentage": 80.0, "secondes_analysees": 60}]

        def envoyer_factice(lot, url, jeton):
            if lot["machine_id"] == "machine-2":
                raise RuntimeError("panne réseau simulée (machine 2 seulement)")
            return {"enregistrees": len(lot["minutes"])}

        with mock.patch.object(dma, "envoyer", side_effect=envoyer_factice), \
                mock.patch.object(dma, "mettre_en_file") as mef:
            reussite_m1 = dma.traiter_envoi_machine("machine-1", lignes_m1, fin, "http://x", "jeton", mode_auto=True)
            reussite_m2 = dma.traiter_envoi_machine("machine-2", lignes_m2, fin, "http://x", "jeton", mode_auto=True)

        self.assertTrue(reussite_m1, "machine-1 n'a jamais échoué, doit réussir.")
        self.assertTrue(reussite_m2, "l'échec de machine-2 est mis en file (mocké, ne lève pas) : sans danger.")
        mef.assert_called_once()
        self.assertEqual(mef.call_args[0][1], "machine-2")
        # machine-1 a réussi : son curseur avance.
        self.assertEqual(lire_curseur("machine-1"), fin)
        # machine-2 a échoué mais a été mise en file en sécurité : son
        # curseur avance AUSSI (voir docstring de traiter_envoi_machine) —
        # l'échec réseau n'a fait régresser ni perdre aucune des 2 machines.
        self.assertEqual(lire_curseur("machine-2"), fin)

    def test_curseur_non_avance_si_meme_la_mise_en_file_echoue(self):
        fin = datetime(2026, 10, 1, 18, 25)
        lignes = [{"minute": "2026-10-01T18:20", "etat": "marche",
                   "pourcentage": 80.0, "secondes_analysees": 60}]

        with mock.patch.object(dma, "envoyer", side_effect=RuntimeError("réseau coupé")), \
                mock.patch.object(dma, "mettre_en_file", side_effect=OSError("disque plein")):
            reussite = dma.traiter_envoi_machine("machine-2", lignes, fin, "http://x", "jeton", mode_auto=True)

        self.assertFalse(reussite)
        self.assertIsNone(
            lire_curseur("machine-2"),
            "le curseur ne doit JAMAIS avancer si la fenêtre n'a pas été mise en sécurité "
            "(ni envoyée, ni mise en file) : c'est le bug corrigé le 2026-10-01 ter."
        )

    def test_rien_a_envoyer_avance_quand_meme_le_curseur(self):
        """Fenêtre tentée mais sans minute exploitable (ex. <30s lues) :
        pas d'envoi possible, mais le curseur avance quand même — un trou
        honnête ne se retente pas indéfiniment (même règle que machine 1
        depuis le correctif du 2026-10-01)."""
        fin = datetime(2026, 10, 1, 18, 25)
        with mock.patch.object(dma, "envoyer") as env, mock.patch.object(dma, "mettre_en_file") as mef:
            reussite = dma.traiter_envoi_machine("machine-2", [], fin, "http://x", "jeton", mode_auto=True)
        self.assertTrue(reussite)
        env.assert_not_called()
        mef.assert_not_called()
        self.assertEqual(lire_curseur("machine-2"), fin)

    def test_mode_sans_envoi_ne_touche_pas_au_curseur(self):
        """--sans-envoi (mode_auto=False dans l'appel) ne doit jamais écrire
        de curseur, même en cas de succès."""
        fin = datetime(2026, 10, 1, 18, 25)
        lignes = [{"minute": "2026-10-01T18:20", "etat": "arret",
                   "pourcentage": 0.0, "secondes_analysees": 60}]
        with mock.patch.object(dma, "envoyer", return_value={"enregistrees": 1}):
            dma.traiter_envoi_machine("machine-1", lignes, fin, "http://x", "jeton", mode_auto=False)
        self.assertIsNone(lire_curseur("machine-1"))


if __name__ == "__main__":
    unittest.main()
