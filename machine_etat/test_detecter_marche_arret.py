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
    SEUIL_AMPLITUDE_M2, SEUIL_FRACTION_M2, SEUIL_MINUTE_POURCENT, SEUIL_PIXEL,
    ZONE, ZONE_M2, amplitude_zone_m2, classer,
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

        # Exactement un read() par image gardee, le reste (14 images sur 15)
        # en grab(). Avant le correctif du 2026-10-03 il y avait un read() de
        # plus, sur l'image d'apres la fenetre : sur le vrai flux, c'est lui
        # qui attendait 30 s (voir TestArretEnFinDeFenetre).
        self.assertEqual(fake.appels_read, secondes)
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
    amplitude sous SEUIL_AMPLITUDE_M2 (±3, amplitude max 6 < 25)."""
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
    """Machine 2 (zone D, méthode pleine cadence validée via
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


# --- Correctif du 2026-10-03 : arrêt de lecture, remise à jour, garde-fou ----

class FakeCaptureBornee(FakeCapture):
    """Comme FakeCapture, mais tout appel au-delà de `limite` images échoue
    bruyamment : sur le vrai flux, c'est l'appel qui restait bloqué 30 s
    (le DVR n'envoie rien après endtime sans fermer la session). Des
    images existent volontairement après la limite, pour qu'une lecture
    en trop ne passe pas inaperçue en renvoyant simplement False."""

    def __init__(self, frames, limite):
        super().__init__(frames)
        self._limite = limite

    def _verifier(self):
        if self._pos >= self._limite:
            raise AssertionError(
                f"lecture de l'image {self._pos}, au-delà de la fenêtre ({self._limite} images)")

    def grab(self):
        self._verifier()
        return super().grab()

    def read(self):
        self._verifier()
        return super().read()


def _analyser_fenetre_avant(capture, debut, fin, calculer_machine2):
    """Copie FIGÉE de la boucle de lecture d'analyser_fenetre AVANT le
    correctif du 2026-10-03 (lecture jusqu'à l'image d'après la fenêtre).
    Sert de référence « avant » pour prouver que l'arrêt au compte d'images
    ne change aucun résultat, machine 1 comme machine 2."""
    pas = 15
    par_minute_m1, par_minute_m2 = {}, {}
    precedente_m1 = None
    tampon_m2 = []
    index = 0
    gardees = 0
    while True:
        if not calculer_machine2 and index % pas != 0:
            if not capture.grab():
                break
            index += 1
            continue
        ok, frame = capture.read()
        if not ok:
            break
        if calculer_machine2:
            tampon_m2.append(gris_floute_m2(frame))
        if index % pas == 0:
            instant = debut + timedelta(seconds=gardees)
            if instant >= fin:
                break
            courante_m1 = zone_grise(frame)
            if precedente_m1 is not None:
                minute = instant.replace(second=0, microsecond=0)
                avec, total = par_minute_m1.get(minute, (0, 0))
                if seconde_avec_mouvement(precedente_m1, courante_m1):
                    avec += 1
                par_minute_m1[minute] = (avec, total + 1)
            precedente_m1 = courante_m1
            gardees += 1
        if calculer_machine2 and len(tampon_m2) == pas:
            instant_seconde = debut + timedelta(seconds=(index // pas))
            if instant_seconde < fin:
                fraction = amplitude_zone_m2(tampon_m2)
                minute = instant_seconde.replace(second=0, microsecond=0)
                avec, total = par_minute_m2.get(minute, (0, 0))
                if fraction > SEUIL_FRACTION_M2:
                    avec += 1
                par_minute_m2[minute] = (avec, total + 1)
            tampon_m2 = []
        index += 1
    return par_minute_m1, par_minute_m2


def _frames_variees(secondes_fenetre, secondes_en_trop):
    """3 secondes typiques puis des images « piège » après la fenêtre :
    - machine 1 : image gardée de la seconde 1 et 2 à 250 dans ZONE (la
      seconde 1 bouge par rapport à la 0, la 2 non) ;
    - machine 2 : seconde 1 en alternance 100/160 dans ZONE_M2 (mouvement),
      secondes 0 et 2 immobiles ;
    - après la fenêtre : 0 et 255 alternés dans les deux zones, qui
      changeraient les deux résultats si elles étaient prises en compte."""
    fond = np.full((HAUTEUR, LARGEUR, 3), GRIS_FOND, dtype=np.uint8)
    x1, y1, x2, y2 = ZONE
    a1, b1, a2, b2 = ZONE_M2
    frames = []
    for i in range(secondes_fenetre * 15):
        f = fond.copy()
        if i >= 15:
            f[y1:y2, x1:x2] = 250
        if 15 <= i < 30:
            f[b1:b2, a1:a2] = 100 if i % 2 == 0 else 160
        frames.append(f)
    for i in range(secondes_en_trop * 15):
        f = fond.copy()
        valeur = 0 if i % 2 == 0 else 255
        f[y1:y2, x1:x2] = valeur
        f[b1:b2, a1:a2] = valeur
        frames.append(f)
    return frames


class TestArretEnFinDeFenetre(unittest.TestCase):
    """La lecture s'arrête à la DERNIÈRE image de la fenêtre, sans jamais
    demander la suivante (celle qui bloquait 30 s), et les résultats des deux
    machines sont identiques à ceux de l'ancienne boucle."""

    debut = datetime(2026, 10, 3, 7, 15, 0)

    def _lancer(self, capture, secondes, calculer_machine2):
        fin = self.debut + timedelta(seconds=secondes)
        with mock.patch.object(dma, "open_stream", return_value=capture), \
                mock.patch.object(dma, "build_rtsp_playback_url", return_value="rtsp://factice"):
            return dma.analyser_fenetre(self.debut, fin, calculer_machine2=calculer_machine2)

    def test_s_arrete_a_la_derniere_image_machine2_active(self):
        frames = _frames_variees(3, 1)
        fake = FakeCaptureBornee(frames, limite=45)
        self._lancer(fake, 3, calculer_machine2=True)
        self.assertEqual(fake.appels_read, 45)
        self.assertEqual(fake.appels_grab, 0)

    def test_s_arrete_a_la_derniere_image_machine2_desactivee(self):
        frames = _frames_variees(3, 1)
        fake = FakeCaptureBornee(frames, limite=45)
        self._lancer(fake, 3, calculer_machine2=False)
        self.assertEqual(fake.appels_read + fake.appels_grab, 45)

    def test_l_ancienne_boucle_lisait_bien_une_image_de_trop(self):
        """Preuve que FakeCaptureBornee détecte vraiment le bug corrigé."""
        frames = _frames_variees(3, 1)
        fin = self.debut + timedelta(seconds=3)
        with self.assertRaises(AssertionError):
            _analyser_fenetre_avant(FakeCaptureBornee(frames, 45), self.debut, fin, True)

    def test_machines_1_et_2_inchangees_avant_apres(self):
        fin = self.debut + timedelta(seconds=3)
        minute = self.debut.replace(second=0)
        for calculer_machine2 in (True, False):
            with self.subTest(calculer_machine2=calculer_machine2):
                avant = _analyser_fenetre_avant(
                    FakeCapture(_frames_variees(3, 1)), self.debut, fin, calculer_machine2)
                apres = self._lancer(FakeCapture(_frames_variees(3, 1)), 3, calculer_machine2)
                self.assertEqual(apres, avant)
                # Valeurs attendues explicites, pour que l'égalité ne soit pas
                # triviale (deux dicts vides seraient « égaux » aussi).
                self.assertEqual(apres[0][minute], (1, 2))
                if calculer_machine2:
                    self.assertEqual(apres[1][minute], (1, 3))
                else:
                    self.assertEqual(apres[1], {})

    def test_flux_trop_court_signale_et_garde_ce_qui_a_ete_lu(self):
        frames = _frames_variees(3, 0)[:40]  # 5 images manquent
        with self.assertLogs("machine_etat", level="WARNING") as journal:
            m1, m2 = self._lancer(FakeCapture(frames), 3, calculer_machine2=True)
        self.assertTrue(any("40/45" in l for l in journal.output))
        self.assertEqual(sum(t for _, t in m2.values()), 2)  # 2 secondes complètes


class TestRepartirAJour(unittest.TestCase):
    """--repartir-a-jour : curseurs des deux machines placés à
    maintenant - 5 min (minute inférieure), rien analysé, rien envoyé, et
    le passage suivant ne touche jamais au trou."""

    ANCIEN = "2026-10-03T07:15"
    T_REPARTIR = datetime(2026, 10, 3, 16, 52, 30)
    CIBLE = datetime(2026, 10, 3, 16, 47)

    def setUp(self):
        self._dossier = tempfile.TemporaryDirectory()
        d = self._dossier.name
        self._patches = [
            mock.patch.object(dma, "FICHIER_CURSEUR", os.path.join(d, "curseur.json")),
            mock.patch.object(dma, "FICHIER_VERROU", os.path.join(d, "passage.lock")),
            mock.patch.object(dma, "DOSSIER_FILE", os.path.join(d, "file_attente")),
            mock.patch.dict(os.environ, {
                "MACHINE_ETAT_ID": "machine-1", "MACHINE2_ETAT_ID": "machine-2",
                "MACHINE2_ACTIVE": "1", "SUIVI_API_URL": "http://factice",
                "SUIVI_API_JETON": "jeton"}),
        ]
        for p in self._patches:
            p.start()
        dma._ecrire_curseurs({"machine-1": self.ANCIEN, "machine-2": self.ANCIEN})

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._dossier.cleanup()

    def _interdits(self):
        erreur = AssertionError("--repartir-a-jour ne doit rien lire ni envoyer")
        return [mock.patch.object(dma, nom, side_effect=erreur)
                for nom in ("analyser_fenetre", "open_stream", "envoyer", "mettre_en_file", "vider_la_file")]

    def _repartir(self):
        patches = self._interdits() + [mock.patch.object(dma, "_maintenant", return_value=self.T_REPARTIR)]
        for p in patches:
            p.start()
        try:
            return dma.main(["--repartir-a-jour"])
        finally:
            for p in patches:
                p.stop()

    def test_journal_ancien_nouveau_et_duree_par_machine(self):
        dma._ecrire_curseurs({"machine-1": self.ANCIEN})  # machine-2 sans curseur
        with self.assertLogs("machine_etat", level="INFO") as journal:
            self.assertEqual(self._repartir(), 0)
        sortie = "\n".join(journal.output)
        self.assertIn("machine-1 : curseur 07:15 -> 16:47, 9 h 32 min sautées", sortie)
        self.assertIn("machine-2 : aucun curseur -> 16:47 (durée sautée inconnue)", sortie)
        self.assertEqual(lire_curseur("machine-1"), self.CIBLE)
        self.assertEqual(lire_curseur("machine-2"), self.CIBLE)

    def test_place_les_deux_curseurs_sans_rien_envoyer(self):
        self.assertEqual(self._repartir(), 0)
        self.assertEqual(lire_curseur("machine-1"), self.CIBLE)
        self.assertEqual(lire_curseur("machine-2"), self.CIBLE)

    def test_machine2_desactivee_son_curseur_est_quand_meme_place(self):
        with mock.patch.dict(os.environ, {"MACHINE2_ACTIVE": "0"}):
            self.assertEqual(self._repartir(), 0)
        self.assertEqual(lire_curseur("machine-2"), self.CIBLE)

    def test_ne_recule_jamais_un_curseur_plus_avance(self):
        dma._ecrire_curseurs({"machine-1": "2026-10-03T16:50", "machine-2": self.ANCIEN})
        self._repartir()
        self.assertEqual(lire_curseur("machine-1"), datetime(2026, 10, 3, 16, 50))
        self.assertEqual(lire_curseur("machine-2"), self.CIBLE)

    def test_refuse_si_un_passage_tient_le_verrou(self):
        verrou = dma.prendre_verrou()
        try:
            self.assertEqual(self._repartir(), 1)
        finally:
            os.close(verrou)
        self.assertEqual(lire_curseur("machine-1"), datetime(2026, 10, 3, 7, 15))

    def test_aucun_envoi_pendant_le_trou(self):
        self._repartir()

        appels_analyse, lots = [], []

        def analyser_factice(debut, fin, garder_frames=None, calculer_machine2=True):
            appels_analyse.append((debut, fin))
            par_minute = {debut: (40, 59)}
            return par_minute, dict(par_minute)

        def envoyer_factice(lot, url, jeton):
            lots.append(lot)
            return {"enregistrees": len(lot["minutes"])}

        t_passage = datetime(2026, 10, 3, 16, 53, 10)  # fenêtre [16:47, 16:48]
        with mock.patch.object(dma, "_maintenant", return_value=t_passage), \
                mock.patch.object(dma, "analyser_fenetre", side_effect=analyser_factice), \
                mock.patch.object(dma, "envoyer", side_effect=envoyer_factice):
            self.assertEqual(dma.main([]), 0)

        self.assertEqual(appels_analyse, [(self.CIBLE, self.CIBLE + timedelta(minutes=1))])
        minutes_envoyees = [m["minute"] for lot in lots for m in lot["minutes"]]
        self.assertEqual(sorted({lot["machine_id"] for lot in lots}), ["machine-1", "machine-2"])
        self.assertTrue(minutes_envoyees)
        self.assertTrue(all(m >= "2026-10-03T16:47" for m in minutes_envoyees),
                        f"minute du trou envoyée : {minutes_envoyees}")
        self.assertEqual(lire_curseur("machine-1"), self.CIBLE + timedelta(minutes=1))
        self.assertEqual(lire_curseur("machine-2"), self.CIBLE + timedelta(minutes=1))


class TestEcritureAtomiqueCurseurs(unittest.TestCase):
    def setUp(self):
        self._dossier = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(
            dma, "FICHIER_CURSEUR", os.path.join(self._dossier.name, "curseur.json"))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._dossier.cleanup()

    def test_echec_au_remplacement_laisse_l_ancien_fichier_intact(self):
        ecrire_curseur("machine-1", datetime(2026, 10, 3, 7, 15))
        with mock.patch.object(dma.os, "replace", side_effect=OSError("disque plein")):
            with self.assertRaises(OSError):
                ecrire_curseur("machine-1", datetime(2026, 10, 3, 7, 20))
        self.assertEqual(lire_curseur("machine-1"), datetime(2026, 10, 3, 7, 15))
        self.assertEqual(os.listdir(self._dossier.name), ["curseur.json"],
                         "aucun fichier temporaire ne doit traîner")


class TestGardeFouDuree(unittest.TestCase):
    debut = datetime(2026, 10, 3, 7, 15)
    fin = datetime(2026, 10, 3, 7, 20)

    def test_avertit_si_le_passage_depasse_la_fenetre(self):
        with self.assertLogs("machine_etat", level="WARNING") as journal:
            self.assertTrue(dma.signaler_si_trop_long(330.0, self.debut, self.fin))
        self.assertIn("330 s pour 300 s", journal.output[0])

    def test_silencieux_si_le_passage_tient_dans_la_fenetre(self):
        self.assertFalse(dma.signaler_si_trop_long(290.0, self.debut, self.fin))


# --- Réglage machine 2 du 2026-10-04 : zone D, seuil d'amplitude 25 ----------

def _groupe_scintillement_m2(amplitude=15):
    """15 images (1 seconde) où TOUTE la zone de la machine 2 oscille de
    `amplitude` niveaux de gris (100 / 100+amplitude) : le scintillement de
    luminosité de la caméra le soir et la nuit, uniforme sur la zone, donc
    pas effacé par le flou 5x5 — c'est lui que l'ancien seuil 10 prenait
    pour du mouvement."""
    x1, y1, x2, y2 = ZONE_M2
    images = []
    for i in range(15):
        frame = np.full((HAUTEUR, LARGEUR, 3), GRIS_FOND, dtype=np.uint8)
        frame[y1:y2, x1:x2] = GRIS_FOND if i % 2 == 0 else GRIS_FOND + amplitude
        images.append(frame)
    return images


class TestReglageMachine2ZoneDSeuil25(unittest.TestCase):
    """Le bruit du soir (amplitude 15) n'est plus du mouvement pour la
    machine 2, une vraie variation forte dans la zone D l'est toujours, et la
    machine 1 n'a pas bougé."""

    def test_zone_d_et_seuil_25(self):
        self.assertEqual(ZONE_M2, (233, 94, 289, 118))
        self.assertEqual(SEUIL_AMPLITUDE_M2, 25)
        self.assertEqual(SEUIL_FRACTION_M2, 0.05)
        self.assertEqual(SEUIL_MINUTE_POURCENT, 40.0)
        # Valeur par défaut réellement utilisée par la production (liée à la
        # définition de la fonction, pas seulement à la constante).
        self.assertEqual(amplitude_zone_m2.__defaults__, ((233, 94, 289, 118), 25))

    def test_constantes_machine1_inchangees(self):
        self.assertEqual(ZONE, (320, 200, 400, 240))
        self.assertEqual(SEUIL_PIXEL, 12)
        self.assertEqual(SEUIL_FRACTION, 0.015)

    def test_bruit_15_compte_avec_l_ancien_seuil_10_mais_plus_avec_25(self):
        images = [gris_floute_m2(f) for f in _groupe_scintillement_m2(15)]
        self.assertGreater(amplitude_zone_m2(images, seuil=10), SEUIL_FRACTION_M2,
                           "ce bruit doit tromper l'ancien seuil, sinon le test ne prouve rien")
        self.assertEqual(amplitude_zone_m2(images), 0.0)

    def _minute_m2(self, groupe, secondes=31):
        """Pipeline réel (analyser_fenetre + classer) sur `secondes` secondes
        identiques ; 31 s suffisent pour dépasser le minimum de 30 s lues."""
        frames = [img for _ in range(secondes) for img in groupe]
        debut = datetime(2026, 10, 1, 20, 31, 0)
        with mock.patch.object(dma, "open_stream", return_value=FakeCapture(frames)), \
                mock.patch.object(dma, "build_rtsp_playback_url", return_value="rtsp://factice"):
            _, par_minute_m2 = dma.analyser_fenetre(
                debut, debut + timedelta(seconds=secondes), calculer_machine2=True)
        return classer(par_minute_m2)

    def test_minute_de_bruit_15_classee_arret(self):
        lignes = self._minute_m2(_groupe_scintillement_m2(15))
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes[0]["etat"], "arret")
        self.assertEqual(lignes[0]["pourcentage"], 0.0)

    def test_variation_forte_dans_la_zone_d_classee_marche(self):
        lignes = self._minute_m2(_groupe_mouvement_m2())
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes[0]["etat"], "marche")
        self.assertEqual(lignes[0]["pourcentage"], 100.0)


# --- Remise à jour quotidienne à 8 h (2026-10-04) ----------------------------

class TestLigneSaut(unittest.TestCase):
    """Ligne de journal écrite par --repartir-a-jour pour chaque machine."""

    nouveau = datetime(2026, 10, 5, 8, 0)

    def test_trou_nul(self):
        self.assertEqual(dma.ligne_saut("machine-1", self.nouveau, self.nouveau),
                         "machine-1 : curseur 08:00 -> 08:00, aucune minute sautée")

    def test_trou_de_20_minutes(self):
        self.assertEqual(dma.ligne_saut("machine-1", datetime(2026, 10, 5, 7, 40), self.nouveau),
                         "machine-1 : curseur 07:40 -> 08:00, 20 min sautées")

    def test_curseur_absent(self):
        self.assertEqual(dma.ligne_saut("machine-2", None, self.nouveau),
                         "machine-2 : aucun curseur -> 08:00 (durée sautée inconnue)")

    def test_trou_de_plusieurs_heures_sur_la_veille(self):
        self.assertEqual(dma.ligne_saut("machine-2", datetime(2026, 10, 4, 22, 28), self.nouveau),
                         "machine-2 : curseur 04/10 22:28 -> 08:00, 9 h 32 min sautées")


def _lire_unite(nom):
    """Lignes « clé=valeur » d'une unité systemd de deploy/ (les clés
    répétées, comme ExecStartPre, sont toutes gardées)."""
    chemin = os.path.join(dma.ROOT, "deploy", nom)
    paires = []
    with open(chemin, encoding="utf-8") as f:
        for ligne in f:
            ligne = ligne.strip()
            if ligne and not ligne.startswith(("#", "[")) and "=" in ligne:
                cle, valeur = ligne.split("=", 1)
                paires.append((cle.strip(), valeur.strip()))
    return paires


class TestUnitesRattrapage(unittest.TestCase):
    """Invariants des unités deploy/suivi-machine-rattrapage.* qui ne
    doivent pas régresser lors d'une modification future (la syntaxe
    elle-même est vérifiée par systemd-analyze verify, voir NOTES)."""

    def test_python_jamais_en_root(self):
        service = _lire_unite("suivi-machine-rattrapage.service")
        self.assertIn(("User", "mbelkhayat"), service)
        exec_start = [v for c, v in service if c == "ExecStart"]
        self.assertEqual(len(exec_start), 1)
        self.assertIn("--repartir-a-jour", exec_start[0])
        self.assertFalse(exec_start[0].startswith(("+", "!")),
                         "la commande Python ne doit jamais être élevée en root")

    def test_arret_puis_relance_toujours_du_timer_de_detection(self):
        service = dict(_lire_unite("suivi-machine-rattrapage.service"))
        self.assertTrue(service["ExecStartPre"].startswith("+"))
        self.assertIn("stop suivi-machine-etat.timer suivi-machine-etat.service", service["ExecStartPre"])
        self.assertTrue(service["ExecStopPost"].startswith("+"))
        self.assertIn("start --no-block suivi-machine-etat.timer", service["ExecStopPost"])

    def test_timer_8h_tous_les_jours_sans_rattrapage_ni_fuseau(self):
        timer = _lire_unite("suivi-machine-rattrapage.timer")
        on_calendar = [v for c, v in timer if c == "OnCalendar"]
        self.assertEqual(on_calendar, ["*-*-* 08:00:00"])
        self.assertIn(("Persistent", "false"), timer)


# --- Bascule lecture HTTP du DVR (2026-10-04) --------------------------------

class FauxReponseHTTP:
    """Simule la réponse streaming de requests pour lecture_dvr : rend des
    blocs via iter_content, un status_code, et peut lever en cours de flux."""

    def __init__(self, blocs, status_code=200, lever_a=None):
        self._blocs = blocs
        self.status_code = status_code
        self._lever_a = lever_a
        self.ferme = False

    def iter_content(self, taille):
        for i, bloc in enumerate(self._blocs):
            if self._lever_a is not None and i == self._lever_a:
                raise ConnectionResetError("coupure réseau simulée")
            yield bloc

    def close(self):
        self.ferme = True


def _faux_session(reponse):
    def session_get(url, auth, delai_connexion, delai_lecture):
        return reponse
    return session_get


class TestTelechargementDVR(unittest.TestCase):
    def setUp(self):
        self._dossier = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(dma.lecture_dvr, "DOSSIER_TMP", self._dossier.name)
        self._patch.start()
        self._env = mock.patch.dict(os.environ, {
            "DVR_IP": "192.168.0.9", "DVR_USER": "admin", "DVR_PASSWORD": "Secret123",
            "CAMERA_CHANNEL": "15"})
        self._env.start()
        self.debut = datetime(2026, 10, 1, 20, 28)
        self.fin = datetime(2026, 10, 1, 20, 33)

    def tearDown(self):
        self._env.stop()
        self._patch.stop()
        self._dossier.cleanup()

    def _restes(self):
        return os.listdir(self._dossier.name)

    def test_succes_renvoie_chemin_et_octets(self):
        gros = b"x" * 70000
        session = _faux_session(FauxReponseHTTP([gros]))
        chemin, octets = dma.lecture_dvr.telecharger_fenetre(
            self.debut, self.fin, tentatives=1, session_get=session)
        self.assertEqual(octets, 70000)
        self.assertTrue(os.path.exists(chemin))
        self.assertEqual(os.path.getsize(chemin), 70000)
        dma.lecture_dvr._supprimer(chemin)

    def test_contexte_supprime_le_fichier(self):
        session = _faux_session(FauxReponseHTTP([b"x" * 70000]))
        with dma.lecture_dvr.fenetre_telechargee(
                self.debut, self.fin, tentatives=1, session_get=session) as (chemin, octets):
            self.assertTrue(os.path.exists(chemin))
        self.assertFalse(os.path.exists(chemin))
        self.assertEqual(self._restes(), [])

    def test_fichier_vide_echoue_et_nettoie(self):
        session = _faux_session(FauxReponseHTTP([]))
        with self.assertRaises(dma.lecture_dvr.ErreurTelechargement):
            dma.lecture_dvr.telecharger_fenetre(self.debut, self.fin, tentatives=1, session_get=session)
        self.assertEqual(self._restes(), [])

    def test_fichier_tronque_sous_le_minimum_echoue(self):
        session = _faux_session(FauxReponseHTTP([b"x" * 100]))
        with self.assertRaises(dma.lecture_dvr.ErreurTelechargement):
            dma.lecture_dvr.telecharger_fenetre(self.debut, self.fin, tentatives=1, session_get=session)
        self.assertEqual(self._restes(), [])

    def test_http_404_echoue(self):
        session = _faux_session(FauxReponseHTTP([b"x" * 70000], status_code=404))
        with self.assertRaises(dma.lecture_dvr.ErreurTelechargement):
            dma.lecture_dvr.telecharger_fenetre(self.debut, self.fin, tentatives=1, session_get=session)
        self.assertEqual(self._restes(), [])

    def test_interruption_en_cours_de_flux_nettoie(self):
        session = _faux_session(FauxReponseHTTP([b"x" * 40000, b"y" * 40000], lever_a=1))
        with self.assertRaises(dma.lecture_dvr.ErreurTelechargement):
            dma.lecture_dvr.telecharger_fenetre(self.debut, self.fin, tentatives=1, session_get=session)
        self.assertEqual(self._restes(), [])

    def test_delai_depasse_nettoie(self):
        faux_temps = iter([0.0, 0.0, 10_000.0, 10_000.0])

        def monotonic():
            try:
                return next(faux_temps)
            except StopIteration:
                return 10_000.0

        session = _faux_session(FauxReponseHTTP([b"x" * 40000, b"y" * 40000]))
        with mock.patch.object(dma.lecture_dvr.time, "monotonic", monotonic):
            with self.assertRaises(dma.lecture_dvr.ErreurTelechargement):
                dma.lecture_dvr.telecharger_fenetre(
                    self.debut, self.fin, delai_max=1, tentatives=1, session_get=session)
        self.assertEqual(self._restes(), [])

    def test_identifiants_jamais_dans_l_erreur(self):
        session = _faux_session(FauxReponseHTTP([], status_code=403))
        try:
            dma.lecture_dvr.telecharger_fenetre(self.debut, self.fin, tentatives=1, session_get=session)
        except dma.lecture_dvr.ErreurTelechargement as e:
            self.assertNotIn("Secret123", str(e))

    def test_nettoyer_restes_vide_le_dossier(self):
        reste = os.path.join(self._dossier.name, "vieux.dav")
        with open(reste, "w") as f:
            f.write("x")
        dma.lecture_dvr.nettoyer_restes()
        self.assertEqual(self._restes(), [])


class TestMasquageLectureDvr(unittest.TestCase):
    def test_masque_url_et_mot_de_passe(self):
        with mock.patch.dict(os.environ, {"DVR_PASSWORD": "Secret123", "DVR_USER": "admin"}):
            msg = dma.lecture_dvr.masquer(
                "echec http://admin:Secret123@10.0.0.5/cgi-bin/loadfile.cgi")
        self.assertNotIn("Secret123", msg)
        self.assertIn("http://***@10.0.0.5", msg)


class TestConfigLectureEtSecours(unittest.TestCase):
    def test_config_lecture_defaut_rtsp(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LECTURE_DVR", None)
            self.assertEqual(dma.config_lecture(), "rtsp")

    def test_config_lecture_http(self):
        with mock.patch.dict(os.environ, {"LECTURE_DVR": "http"}):
            self.assertEqual(dma.config_lecture(), "http")

    def test_config_lecture_valeur_inconnue_reste_rtsp(self):
        with mock.patch.dict(os.environ, {"LECTURE_DVR": "n'importe quoi"}):
            self.assertEqual(dma.config_lecture(), "rtsp")

    def test_secours_rtsp_si_http_echoue(self):
        debut = datetime(2026, 10, 1, 20, 28)
        fin = debut + timedelta(seconds=2)
        frames = _frames_variees(2, 0)

        def http_qui_echoue(*a, **k):
            raise dma.lecture_dvr.ErreurTelechargement("DVR injoignable")

        with mock.patch.object(dma, "analyser_fenetre_http", side_effect=http_qui_echoue), \
                mock.patch.object(dma, "open_stream", return_value=FakeCapture(frames)), \
                mock.patch.object(dma, "build_rtsp_playback_url", return_value="rtsp://factice"):
            m1, m2, methode = dma.lire_analyser(debut, fin, "http", calculer_machine2=True)
        self.assertEqual(methode, "secours-rtsp")
        self.assertTrue(m1)  # des minutes ont bien été classées via RTSP

    def test_http_reussi_renvoie_http(self):
        debut = datetime(2026, 10, 1, 20, 28)
        fin = debut + timedelta(seconds=2)
        frames = _frames_variees(2, 0)

        def http_ok(d, f, garder_frames=None, calculer_machine2=True):
            m1, m2, *_ = dma.analyser_capture(FakeCapture(frames), d, f, calculer_machine2)
            return m1, m2, {"octets": 1, "t_dl": 0.1, "t_lecture": 0.1, "images": 30, "total": 30}

        with mock.patch.object(dma, "analyser_fenetre_http", side_effect=http_ok):
            m1, m2, methode = dma.lire_analyser(debut, fin, "http", calculer_machine2=True)
        self.assertEqual(methode, "http")


class TestEquivalenceFluxFichier(unittest.TestCase):
    """Le cœur analyser_capture donne les MÊMES minutes que la source soit un
    « flux » ou un « fichier » (mêmes images) — machine 1 et machine 2."""

    debut = datetime(2026, 10, 1, 20, 28)

    def test_memes_minutes_m1_et_m2(self):
        frames = _frames_variees(3, 0)
        fin = self.debut + timedelta(seconds=3)
        flux = dma.analyser_capture(FakeCapture(frames), self.debut, fin, calculer_machine2=True)
        fichier = dma.analyser_capture(FakeCapture(list(frames)), self.debut, fin, calculer_machine2=True)
        self.assertEqual(flux[0], fichier[0])   # par_minute_m1
        self.assertEqual(flux[1], fichier[1])   # par_minute_m2

    def test_analyser_fenetre_http_identique_a_rtsp(self):
        import contextlib
        frames = _frames_variees(3, 0)
        fin = self.debut + timedelta(seconds=3)

        @contextlib.contextmanager
        def faux_contexte(d, f, **kw):
            yield "/tmp/factice.dav", 70000

        with mock.patch.object(dma, "open_stream", return_value=FakeCapture(frames)), \
                mock.patch.object(dma, "build_rtsp_playback_url", return_value="rtsp://factice"):
            m1_rtsp, m2_rtsp = dma.analyser_fenetre(self.debut, fin, calculer_machine2=True)

        with mock.patch.object(dma.lecture_dvr, "fenetre_telechargee", faux_contexte), \
                mock.patch.object(dma.lecture_dvr, "ouvrir_fichier",
                                  return_value=(FakeCapture(frames), "/tmp/factice.dav")):
            m1_http, m2_http, _ = dma.analyser_fenetre_http(self.debut, fin, calculer_machine2=True)

        self.assertEqual(m1_rtsp, m1_http)
        self.assertEqual(m2_rtsp, m2_http)


class TestAlignementRognage(unittest.TestCase):
    """`sauter` (DVR_HTTP_IMAGES_AVANCE) rogne un décalage constant en tête du
    fichier sans changer les minutes par rapport à une fenêtre bien alignée."""

    debut = datetime(2026, 10, 1, 20, 28)

    def test_sauter_le_lead_in_donne_la_meme_fenetre(self):
        fin = self.debut + timedelta(seconds=2)
        fenetre = _frames_variees(2, 0)                 # 30 images alignées
        fond = np.full((HAUTEUR, LARGEUR, 3), GRIS_FOND, dtype=np.uint8)
        x1, y1, x2, y2 = ZONE
        lead = []
        for i in range(15):                             # 1 s de « piège » en tête
            f = fond.copy()
            f[y1:y2, x1:x2] = 0 if i % 2 == 0 else 255
            lead.append(f)

        aligne = dma.analyser_capture(FakeCapture(fenetre), self.debut, fin, calculer_machine2=True)
        avec_lead = dma.analyser_capture(FakeCapture(lead + fenetre), self.debut, fin,
                                         calculer_machine2=True, sauter=15)
        self.assertEqual(aligne[0], avec_lead[0])
        self.assertEqual(aligne[1], avec_lead[1])


class TestSecondesIncompletesHTTP(unittest.TestCase):
    def test_fichier_court_classe_comme_une_fenetre_tronquee(self):
        import contextlib
        debut = datetime(2026, 10, 1, 20, 28)
        fin = debut + timedelta(seconds=60)
        frames = _frames_variees(3, 0)                  # seulement 3 s sur 60 demandées

        @contextlib.contextmanager
        def faux_contexte(d, f, **kw):
            yield "/tmp/factice.dav", 70000

        with mock.patch.object(dma.lecture_dvr, "fenetre_telechargee", faux_contexte), \
                mock.patch.object(dma.lecture_dvr, "ouvrir_fichier",
                                  return_value=(FakeCapture(frames), "/tmp/factice.dav")):
            m1, m2, stats = dma.analyser_fenetre_http(debut, fin, calculer_machine2=True)
        self.assertLess(stats["images"], stats["total"])
        # Minute incomplète (3 s lues) : classer() la rejettera (< 30 s).
        self.assertEqual(dma.classer(m1), [])

    def test_fichier_illisible_leve_pour_secours(self):
        import contextlib
        debut = datetime(2026, 10, 1, 20, 28)
        fin = debut + timedelta(seconds=5)

        @contextlib.contextmanager
        def faux_contexte(d, f, **kw):
            yield "/tmp/factice.dav", 70000

        with mock.patch.object(dma.lecture_dvr, "fenetre_telechargee", faux_contexte), \
                mock.patch.object(dma.lecture_dvr, "ouvrir_fichier",
                                  return_value=(FakeCapture([]), "/tmp/factice.dav")):
            with self.assertRaises(dma.lecture_dvr.ErreurLectureFichier):
                dma.analyser_fenetre_http(debut, fin, calculer_machine2=True)


class TestComparerLectures(unittest.TestCase):
    def test_equivalent_si_memes_etats_et_ecart_faible(self):
        http = [{"minute": "2026-10-01T20:28", "etat": "marche", "pourcentage": 100.0, "secondes_analysees": 59},
                {"minute": "2026-10-01T20:30", "etat": "arret", "pourcentage": 20.0, "secondes_analysees": 59}]
        rtsp = [{"minute": "2026-10-01T20:28", "etat": "marche", "pourcentage": 98.0, "secondes_analysees": 59},
                {"minute": "2026-10-01T20:30", "etat": "arret", "pourcentage": 22.0, "secondes_analysees": 59}]
        equivalent, details = dma.comparer_minutes(http, rtsp)
        self.assertTrue(equivalent)
        self.assertTrue(all(d[-1] for d in details))

    def test_non_equivalent_si_etat_differe(self):
        http = [{"minute": "2026-10-01T20:30", "etat": "marche", "pourcentage": 41.0, "secondes_analysees": 59}]
        rtsp = [{"minute": "2026-10-01T20:30", "etat": "arret", "pourcentage": 39.0, "secondes_analysees": 59}]
        equivalent, _ = dma.comparer_minutes(http, rtsp)
        self.assertFalse(equivalent)

    def test_non_equivalent_si_minute_manquante_d_un_cote(self):
        http = [{"minute": "2026-10-01T20:28", "etat": "arret", "pourcentage": 0.0, "secondes_analysees": 59}]
        equivalent, _ = dma.comparer_minutes(http, [])
        self.assertFalse(equivalent)


class TestBudgetRattrapageSousTimeout(unittest.TestCase):
    def test_budget_strictement_sous_timeout_systemd(self):
        # TimeoutStartSec=480 dans deploy/suivi-machine-etat.service.
        self.assertLess(dma.BUDGET_RATTRAPAGE_S, 480)
        self.assertGreater(dma.BUDGET_RATTRAPAGE_S, 0)


if __name__ == "__main__":
    unittest.main()
