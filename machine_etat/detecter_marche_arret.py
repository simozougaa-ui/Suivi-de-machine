"""Détection MARCHE / ARRÊT des machines, minute par minute, pour la production.

Deux machines, UNE SEULE lecture vidéo (même caméra 15, même flux 1280x720
à 15 im/s) : chaque frame décodée alimente les deux méthodes ci-dessous,
indépendantes l'une de l'autre.

MACHINE 1 (zone ZONE, validée sur plusieurs plages horaires réelles, voir
NOTES-SESSION.md) : une image gardée PAR SECONDE (pas à pleine cadence) ;
deux secondes consécutives sont comparées dans la zone fixe ; si assez de
pixels ont changé, cette seconde-là « a du mouvement ». Une minute est
classée « marche » quand plus de SEUIL_MINUTE_POURCENT % de ses secondes
ont du mouvement, « arrêt » sinon.

    zone            x1=320, y1=200, x2=400, y2=240  (sur une frame 1280x720)
    flou            GaussianBlur 7x7, sur l'image ENTIÈRE avant découpage de la
                     zone (voir zone_grise) — sans lui, le bruit de capteur/
                     compression déclenchait « marche » en continu, y compris
                     machine à l'arrêt (correctif du 2026-10-01)
    seuil pixel     12    (différence d'intensité au-delà de laquelle un pixel
                           est dit « changé »)
    seuil fraction  0.015 (part de pixels changés au-delà de laquelle la seconde
                           est dite « avec mouvement »)

MACHINE 2 (zone ZONE_M2, ajoutée le 2026-10-01 ter, voir NOTES-SESSION.md) :
méthode différente, validée via outils_zones/zones.py — PLEINE CADENCE
(les 15 images de chaque seconde, pas une seule) ; un pixel de la zone
« bouge » si son amplitude (max - min) sur ces 15 images dépasse
SEUIL_AMPLITUDE_M2 ; la seconde est « en mouvement » si plus de
SEUIL_FRACTION_M2 de la zone bouge. Seuils fragiles la nuit (marge faible,
voir NOTES-SESSION.md) : constantes isolées en haut de ce fichier, et le
journal affiche le % de secondes en mouvement de chaque minute pour
pouvoir les réajuster sans deviner.

    zone            x1=233, y1=91, x2=250, y2=121
    flou            GaussianBlur 5x5, même ordre (gris -> flou -> découpage)
                     que la machine 1, même raison (artefacts de bord)
    seuil amplitude 10
    seuil fraction  0.05

SEUIL_MINUTE_POURCENT (40 %) est COMMUN aux deux machines.

JAMAIS LE FLUX DIRECT. Le calcul sur le flux live donne des faux positifs (bug
non résolu, voir NOTES-SESSION.md). On relit donc les ENREGISTREMENTS du DVR en
RTSP playback, avec un retard volontaire d'au moins RETARD_MINUTES (5 min), ce
qui laisse au DVR le temps d'avoir fini d'écrire.

REPRISE SANS TROU, UN CURSEUR PAR MACHINE (voir NOTES-SESSION.md, correctifs
du 2026-10-01 et 2026-10-01 ter). Un passage automatique ne recalcule PAS sa
fenêtre à partir de « maintenant » : il reprend pile où le MOINS AVANCÉ des
curseurs actifs s'est arrêté (voir fenetre_a_analyser) — une seule lecture
vidéo sert donc toujours les deux machines, même si l'une est en retard sur
l'autre (ex. son dernier envoi a échoué). Le curseur de chaque machine
n'avance QU'APRÈS que ses minutes ont été mises en sécurité (envoyées, ou
mises en file d'attente durablement) — jamais avant : avancer le curseur
avant l'envoi, comme avant ce correctif, pouvait perdre des minutes si le
processus était tué entre les deux (voir traiter_envoi_machine).

AUCUNE IMAGE N'EST ÉCRITE SUR LE DISQUE. Les frames sont décodées, comparées et
jetées au fil de la lecture. C'est la forme la plus sûre de « nettoyer les images
après calcul » : il n'y a rien à nettoyer, donc rien ne s'accumule si le script
est tué en cours de route. L'option --garder-frames force l'écriture dans un
dossier (frames de la machine 1 uniquement), pour inspecter un cas douteux.

ENVOI DES RÉSULTATS : POST vers l'application de suivi de production
(suivi-production-imprimerie), authentifié par un jeton porteur, un POST par
machine (même API, `machine_id` différent). Si l'envoi d'une machine échoue
(réseau coupé, application en redéploiement), son lot est déposé dans
file_attente/ et réessayé au tour suivant — l'API est idempotente, un renvoi
remplace au lieu de dupliquer. Un échec sur une machine n'affecte jamais
l'autre.

Usage (production, via systemd timer toutes les 5 minutes) :
    python3 machine_etat/detecter_marche_arret.py

Usage (rattrapage ou vérification d'une plage passée, les DEUX machines) :
    python3 machine_etat/detecter_marche_arret.py \
        --date 2026-09-28 --debut 17:24 --fin 18:07
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta

import cv2
import numpy as np
import requests
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.camera_stream import build_rtsp_playback_url, open_stream  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

logger = logging.getLogger("machine_etat")

# --- Machine 1 : valeurs VALIDÉES, à ne pas changer sans revalider ----------
ZONE = (320, 200, 400, 240)          # x1, y1, x2, y2 sur une frame 1280x720
SEUIL_PIXEL = 12
SEUIL_FRACTION = 0.015

# --- Machine 2 : valeurs validées via outils_zones/zones.py (2026-10-01 ter) -
# Marge faible la nuit (55 % de secondes en mouvement mesurées en marche de
# nuit, contre 10 % à l'arrêt) : seuils isolés ici, modifiables sans toucher
# au reste du script si les mesures de Mohamed évoluent.
ZONE_M2 = (233, 91, 250, 121)
FLOU_M2 = (5, 5)
SEUIL_AMPLITUDE_M2 = 10
SEUIL_FRACTION_M2 = 0.05

# Commun aux deux machines.
SEUIL_MINUTE_POURCENT = 40.0

# Le mainstream du DVR est confirmé à 15 fps (voir dvr_check/). La machine 1
# ne garde qu'une frame par seconde (l'échelle à laquelle sa méthode a été
# validée) ; la machine 2 utilise les 15 images de chaque seconde.
FPS_SUPPOSE = 15.0

# Retard volontaire sur le direct (voir en-tête).
RETARD_MINUTES = 5
FENETRE_MINUTES = 5

DOSSIER_FILE = os.path.join(ROOT, "machine_etat", "file_attente")
# Au-delà, la file ne sert plus à rien : l'application a de toute façon perdu
# ces heures-là, et on ne remplit pas le disque du Jetson indéfiniment.
MAX_LOTS_EN_ATTENTE = 288          # 24 h de tours toutes les 5 minutes

# --- Curseur de reprise (correctifs du 2026-10-01 et 2026-10-01 ter) --------
# Un curseur PAR MACHINE dans le même fichier (clé = identifiant machine),
# avec migration automatique de l'ancien format mono-machine (une seule clé
# "derniere_fin_analysee", qui ne pouvait concerner que machine-1, seule
# machine suivie avant l'ajout de la machine 2) — voir lire_curseur.
FICHIER_CURSEUR = os.path.join(ROOT, "machine_etat", "curseur.json")

# Plafond de rattrapage par passage : volontairement égal à FENETRE_MINUTES,
# pas plus. TimeoutStartSec=480s (voir deploy/suivi-machine-etat.service)
# donne une marge sûre tant que le calcul reste sous ~8 min pour 5 min de
# vidéo. Un plafond plus grand (ex. rattraper une heure d'un coup après une
# panne) risquerait de se faire tuer par systemd en pleine fenêtre, perdant
# tout son travail au lieu d'avancer d'un cran sûr.
PLAFOND_RATTRAPAGE_MINUTES = FENETRE_MINUTES


def lire_curseur(machine_id):
    """Fin de la dernière fenêtre traitée avec succès pour `machine_id`, ou
    None si cette machine n'a jamais de progression connue (jamais lancée,
    fichier absent/illisible, ou ancienne entrée mono-machine qui ne la
    concerne pas)."""
    try:
        with open(FICHIER_CURSEUR, encoding="utf-8") as f:
            donnees = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    if "derniere_fin_analysee" in donnees:
        # Ancien format (avant la machine 2) : une seule valeur, qui ne
        # pouvait concerner que machine-1 (seule machine suivie alors).
        if machine_id != "machine-1":
            return None
        valeur = donnees["derniere_fin_analysee"]
    else:
        valeur = donnees.get(machine_id)
        if valeur is None:
            return None

    try:
        return datetime.strptime(valeur, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def ecrire_curseur(machine_id, fin):
    """Avance le curseur de `machine_id` à `fin` — jamais en arrière.

    Deux machines partagent une même fenêtre vidéo (une seule lecture, voir
    fenetre_a_analyser) qui repart du MOINS avancé des curseurs actifs : si
    machine-1 a déjà confirmé au-delà de `fin` (c'est machine-2 qui était en
    retard), un appel ecrire_curseur("machine-1", fin) ne doit surtout pas
    faire reculer son curseur déjà plus avancé.
    """
    try:
        with open(FICHIER_CURSEUR, encoding="utf-8") as f:
            donnees = json.load(f)
        if "derniere_fin_analysee" in donnees:
            donnees = {"machine-1": donnees["derniere_fin_analysee"]}
    except (FileNotFoundError, json.JSONDecodeError):
        donnees = {}

    actuelle = donnees.get(machine_id)
    if actuelle is not None:
        try:
            if datetime.strptime(actuelle, "%Y-%m-%dT%H:%M") >= fin:
                return
        except ValueError:
            pass

    donnees[machine_id] = fin.strftime("%Y-%m-%dT%H:%M")
    os.makedirs(os.path.dirname(FICHIER_CURSEUR), exist_ok=True)
    with open(FICHIER_CURSEUR, "w", encoding="utf-8") as f:
        json.dump(donnees, f)


def config_machine2():
    """Configuration de la machine 2, lue à l'APPEL (pas au chargement du
    module) — comme config_api() — pour rester modifiable sans redéployer
    de code (voir .env.example : MACHINE2_ACTIVE, MACHINE2_ETAT_ID)."""
    active = (os.getenv("MACHINE2_ACTIVE") or "1").strip() == "1"
    machine_id = (os.getenv("MACHINE2_ETAT_ID") or "machine-2").strip()
    return active, machine_id


def zone_grise(frame):
    """Extrait la zone de travail (machine 1) en niveaux de gris, flouté.

    Le gris suffit : on mesure un CHANGEMENT d'intensité, pas une couleur — et
    il divise par trois le travail de comparaison.

    FLOU (correctif du 2026-10-01, voir NOTES-SESSION.md) : sans lui, le bruit
    de capteur/compression pixel à pixel suffisait à dépasser SEUIL_PIXEL=12
    sur une fraction de la zone supérieure à SEUIL_FRACTION=0.015 — la machine
    était classée « marche » à 100 % des minutes alors qu'elle était à l'arrêt
    (vérifié le 01/10, 03:06-03:18, la nuit, machine éteinte). Un
    GaussianBlur(7,7) lisse ce bruit sans effacer un vrai mouvement, qui
    change l'intensité sur une zone bien plus large qu'un pixel isolé.

    ORDRE : gris PUIS flou PUIS découpage de la zone — jamais l'inverse.
    Flouter après avoir découpé appliquerait le noyau 7x7 jusqu'au bord de la
    zone en répétant les pixels de bordure (ou en les mettant à zéro, selon le
    mode), ce qui fausserait le résultat sur toute la bande de ~3 px la plus
    proche du bord. Flouter l'image ENTIÈRE d'abord utilise les vrais pixels
    voisins, y compris hors zone, pour chaque pixel de la zone — exactement ce
    que fait /tmp/minute.py, le script qui a servi à valider ce correctif
    (0 à 8 % de mouvement sur les mêmes images, machine à l'arrêt, mêmes
    seuils, au lieu de 100 %).
    """
    gris = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    flou = cv2.GaussianBlur(gris, (7, 7), 0)
    x1, y1, x2, y2 = ZONE
    return flou[y1:y2, x1:x2]


def seconde_avec_mouvement(precedente, courante):
    """True si assez de pixels ont changé entre deux images consécutives
    (machine 1)."""
    diff = cv2.absdiff(precedente, courante)
    changes = int(np.count_nonzero(diff > SEUIL_PIXEL))
    total = diff.size
    return total > 0 and (changes / total) >= SEUIL_FRACTION


def gris_floute_m2(frame):
    """Gris + flou(5,5) sur l'image ENTIÈRE (machine 2), avant découpage de
    la zone — même raisonnement d'ordre que zone_grise() (machine 1) : évite
    les artefacts de bord du flou en bordure de zone. Ne découpe PAS ici :
    amplitude_zone_m2() accumule plusieurs de ces images avant de découper,
    pour calculer une amplitude sur 15 images (voir sa docstring)."""
    gris = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gris, FLOU_M2, 0)


def amplitude_zone_m2(images_grises_floutees, zone=ZONE_M2, seuil=SEUIL_AMPLITUDE_M2):
    """Fraction (0-1) des pixels de `zone` dont l'amplitude (max - min) sur
    les images fournies dépasse `seuil`. Les images doivent déjà être en
    gris + floutées (voir gris_floute_m2), prises à PLEINE CADENCE sur UNE
    SECONDE (15 images) — méthode validée via outils_zones/zones.py, même
    calcul que sa fonction fraction_mouvement (seuils différents : ceux-ci
    sont ceux retenus pour la machine 2 en production, pas les seuils de
    réglage d'outils_zones/)."""
    x1, y1, x2, y2 = zone
    pile = np.stack(images_grises_floutees, axis=0)[:, y1:y2, x1:x2].astype(np.int16)
    amplitude = pile.max(axis=0) - pile.min(axis=0)
    return np.count_nonzero(amplitude > seuil) / amplitude.size


def analyser_fenetre(debut, fin, garder_frames=None, calculer_machine2=True):
    """Lit les enregistrements entre deux instants et classe chaque minute,
    pour les DEUX machines, en UNE SEULE lecture vidéo.

    Returns:
        (par_minute_m1, par_minute_m2) — chacun un dict minute (datetime
        tronqué à la minute) -> (secondes_avec_mouvement, secondes_analysees).
        par_minute_m2 est vide si `calculer_machine2` est False (aucun calcul
        machine 2 n'est fait dans ce cas — ni travail ni coût en moins pour
        rien). Une minute absente d'un dict n'a tout simplement pas été lue
        pour cette machine : elle ne sera PAS envoyée, et l'application
        l'affichera comme « non mesurée » — jamais comme un arrêt.
    """
    duree = int((fin - debut).total_seconds())
    if duree <= 0:
        raise ValueError("Fenêtre vide : la fin doit être après le début.")

    capture = open_stream(build_rtsp_playback_url(debut, fin))
    pas = max(1, int(round(FPS_SUPPOSE)))       # une frame gardée/s (machine 1)
    par_minute_m1 = {}
    par_minute_m2 = {}
    precedente_m1 = None
    tampon_m2 = []
    index = 0
    gardees = 0

    if garder_frames:
        os.makedirs(garder_frames, exist_ok=True)

    try:
        while True:
            if not calculer_machine2 and index % pas != 0:
                # Machine 2 désactivée : on peut se permettre de ne décoder
                # qu'1 image/15 (voir grab() plus bas) comme avant son ajout.
                # Avec machine 2 active, TOUTES les images sont nécessaires
                # (elle travaille à pleine cadence) : cette branche ne sert
                # alors jamais (voir NOTES-SESSION.md, correctif 2026-10-01
                # ter — le gain de grab() disparaît quand machine 2 tourne,
                # c'est un compromis assumé, pas un oubli).
                ok = capture.grab()
                if not ok:
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

                if garder_frames:
                    cv2.imwrite(
                        os.path.join(garder_frames, instant.strftime("frame_%H%M%S.jpg")),
                        frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])

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
    finally:
        capture.release()

    return par_minute_m1, par_minute_m2


def classer(par_minute):
    """Transforme les compteurs en lignes prêtes pour l'API. Commun aux deux
    machines (même SEUIL_MINUTE_POURCENT, même format de sortie).

    Une minute dont on a lu TROP PEU de secondes n'est pas classée : avec 5
    secondes analysées, « 40 % de mouvement » ne veut rien dire, et une coupure
    de flux passerait pour un arrêt. Le seuil de 30 secondes (la moitié d'une
    minute) est délibérément prudent : mieux vaut un trou honnête qu'un arrêt
    inventé.
    """
    lignes = []
    for minute in sorted(par_minute):
        avec, total = par_minute[minute]
        if total < 30:
            logger.warning("Minute %s ignorée : seulement %d seconde(s) lue(s).",
                           minute.strftime("%H:%M"), total)
            continue
        pourcentage = 100.0 * avec / total
        lignes.append({
            "minute": minute.strftime("%Y-%m-%dT%H:%M"),
            "etat": "marche" if pourcentage > SEUIL_MINUTE_POURCENT else "arret",
            "pourcentage": round(pourcentage, 1),
            "secondes_analysees": total,
        })
    return lignes


def id_machine1():
    """Identifiant de la machine 1, configurable (MACHINE_ETAT_ID, défaut
    "machine-1") — lu à l'appel, comme config_machine2(). Séparé de
    config_api() pour pouvoir le connaître AVANT de valider SUIVI_API_URL/
    SUIVI_API_JETON (nécessaire pour la fenêtre partagée, voir main)."""
    return (os.getenv("MACHINE_ETAT_ID") or "machine-1").strip()


def config_api():
    url = (os.getenv("SUIVI_API_URL") or "").strip().rstrip("/")
    jeton = (os.getenv("SUIVI_API_JETON") or "").strip()
    if not url or not jeton:
        raise RuntimeError(
            "SUIVI_API_URL et SUIVI_API_JETON sont requis dans .env "
            "(voir .env.example). Sans eux, aucun résultat ne peut être envoyé."
        )
    return url, jeton


def envoyer(lot, url, jeton):
    """POST du lot. Lève en cas d'échec, pour que l'appelant le mette en file."""
    r = requests.post(
        url + "/api/machine-etat",
        headers={"Authorization": f"Bearer {jeton}", "Content-Type": "application/json"},
        json=lot,
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"L'application a refusé l'envoi ({r.status_code}) : {r.text[:200]}")
    return r.json()


def mettre_en_file(lot, machine_id):
    os.makedirs(DOSSIER_FILE, exist_ok=True)
    nom = datetime.now().strftime("lot_%Y%m%d_%H%M%S_%f_") + machine_id + ".json"
    with open(os.path.join(DOSSIER_FILE, nom), "w", encoding="utf-8") as f:
        json.dump(lot, f)
    logger.warning("Envoi impossible : lot mis en attente dans %s", nom)
    _elaguer_file()


def _elaguer_file():
    """Garde les lots les PLUS RÉCENTS : ce sont eux qu'on veut voir revenir en
    premier quand le réseau revient."""
    try:
        fichiers = sorted(os.listdir(DOSSIER_FILE))
    except FileNotFoundError:
        return
    surplus = len(fichiers) - MAX_LOTS_EN_ATTENTE
    for nom in fichiers[:max(0, surplus)]:
        os.remove(os.path.join(DOSSIER_FILE, nom))
        logger.warning("File pleine : lot le plus ancien supprimé (%s).", nom)


def vider_la_file(url, jeton):
    """Réessaie les lots en attente (toutes machines confondues, chaque lot
    porte son propre machine_id), du plus ancien au plus récent."""
    if not os.path.isdir(DOSSIER_FILE):
        return
    for nom in sorted(os.listdir(DOSSIER_FILE)):
        chemin = os.path.join(DOSSIER_FILE, nom)
        try:
            with open(chemin, encoding="utf-8") as f:
                lot = json.load(f)
            envoyer(lot, url, jeton)
            os.remove(chemin)
            logger.info("Lot en attente renvoyé : %s", nom)
        except Exception as e:                                    # noqa: BLE001
            logger.warning("Lot %s toujours non envoyé (%s) — on réessaiera.", nom, e)
            return    # réseau encore coupé : inutile d'insister sur les suivants


def fenetre_a_analyser(machines, maintenant=None):
    """Fenêtre à traiter par un passage AUTOMATIQUE (sans --date/--debut/--fin),
    commune à toutes les machines de `machines` — UNE SEULE lecture vidéo sert
    toujours toutes les machines actives (voir analyser_fenetre).

    Le début est le MINIMUM des curseurs connus (voir lire_curseur) parmi
    `machines` : si une machine est en retard sur une autre (ex. son dernier
    envoi a échoué pendant que l'autre réussissait), la fenêtre repart d'ELLE,
    jamais de la plus avancée — aucune machine n'est jamais sautée. Sans aucun
    curseur connu (premier lancement) : repli sur [T-10min, T-5min].

    Toujours bornée par `fin_securisee` (T-RETARD_MINUTES) : on ne lit jamais
    une fenêtre plus récente que ce que le DVR a eu le temps d'écrire, retard
    accumulé ou non. Et toujours bornée à PLAFOND_RATTRAPAGE_MINUTES de large
    (voir sa docstring) : un passage en retard avance d'un cran sûr plutôt que
    de tout rattraper d'un coup au risque de se faire tuer par le délai
    systemd en pleine fenêtre.

    Retourne (debut, fin, retard_restant). (None, None, None) si rien de
    nouveau n'est encore disponible (déjà à jour avec le DVR) : ne pas
    analyser une fenêtre vide ou négative.
    """
    t = (maintenant or datetime.now()).replace(second=0, microsecond=0)
    fin_securisee = t - timedelta(minutes=RETARD_MINUTES)

    curseurs_connus = [c for c in (lire_curseur(m) for m in machines) if c is not None]
    debut = min(curseurs_connus) if curseurs_connus else fin_securisee - timedelta(minutes=FENETRE_MINUTES)

    if debut >= fin_securisee:
        return None, None, None

    fin = min(fin_securisee, debut + timedelta(minutes=PLAFOND_RATTRAPAGE_MINUTES))
    return debut, fin, fin_securisee - fin


def traiter_envoi_machine(machine_id, lignes, fin_fenetre, url, jeton, mode_auto):
    """Envoie les `lignes` de `machine_id`, et n'avance SON PROPRE curseur
    qu'une fois ces minutes mises en sécurité — envoyées, ou mises en file
    d'attente durablement (voir ecrire_curseur, mettre_en_file) — jamais
    avant (correctif du 2026-10-01 ter : avant, le curseur avançait dès la
    lecture DVR terminée, AVANT même de tenter l'envoi ; un plantage entre
    les deux pouvait perdre la fenêtre pour de bon). Un échec de CETTE
    machine ne touche jamais au curseur d'une autre : chaque appel est
    totalement indépendant.

    Retourne True si la fenêtre a été gérée en sécurité pour cette machine
    (rien à envoyer, envoi réussi, ou mise en file réussie) ; False
    seulement si même la mise en file a échoué (curseur alors NON avancé :
    la fenêtre sera retentée au prochain passage).
    """
    if not lignes:
        logger.warning("%s : aucune minute exploitable, rien à envoyer.", machine_id)
        if mode_auto:
            ecrire_curseur(machine_id, fin_fenetre)
        return True

    lot = {"machine_id": machine_id, "minutes": lignes}
    try:
        r = envoyer(lot, url, jeton)
        logger.info("%s : envoyé, %s minute(s) enregistrée(s).", machine_id, r.get("enregistrees"))
    except Exception as e:                                        # noqa: BLE001
        logger.error("%s : %s", machine_id, e)
        try:
            mettre_en_file(lot, machine_id)
        except Exception as e2:                                    # noqa: BLE001
            logger.error(
                "%s : échec de mise en file (%s) — curseur NON avancé, "
                "fenêtre rejouée au prochain passage.", machine_id, e2)
            return False

    if mode_auto:
        ecrire_curseur(machine_id, fin_fenetre)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="AAAA-MM-JJ (rattrapage d'une plage passée)")
    ap.add_argument("--debut", help="HH:MM")
    ap.add_argument("--fin", help="HH:MM")
    ap.add_argument("--garder-frames", metavar="DOSSIER",
                    help="écrit les frames décodées de la machine 1 (débogage seulement)")
    ap.add_argument("--sans-envoi", action="store_true",
                    help="calcule et affiche (les deux machines), n'envoie rien, n'avance aucun curseur")
    ap.add_argument("--verbeux", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbeux else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")

    machine2_active, machine2_id = config_machine2()
    machine1_id = id_machine1()

    # Mode automatique (timer systemd) = ni --date ni --debut ni --fin : seul
    # ce mode lit/écrit les curseurs de reprise (voir FICHIER_CURSEUR) — un
    # rattrapage manuel pour inspecter une plage passée ne doit jamais faire
    # avancer ou reculer la progression du service.
    mode_auto = not (args.date or args.debut or args.fin)

    if args.date and args.debut and args.fin:
        jour = datetime.strptime(args.date, "%Y-%m-%d").date()
        debut = datetime.combine(jour, datetime.strptime(args.debut, "%H:%M").time())
        fin = datetime.combine(jour, datetime.strptime(args.fin, "%H:%M").time())
        retard_restant = None
    elif args.date or args.debut or args.fin:
        ap.error("--date, --debut et --fin vont ensemble.")
    else:
        machines_actives = [machine1_id] + ([machine2_id] if machine2_active else [])
        debut, fin, retard_restant = fenetre_a_analyser(machines_actives)
        if debut is None:
            logger.info("Rien de nouveau à analyser pour l'instant (déjà à jour avec le DVR).")
            return 0

    logger.info("Analyse des enregistrements de %s à %s (machine 2 %s)",
                debut.strftime("%Y-%m-%d %H:%M"), fin.strftime("%H:%M"),
                "active" if machine2_active else "désactivée")

    debut_calcul = time.time()
    par_minute_m1, par_minute_m2 = analyser_fenetre(
        debut, fin, args.garder_frames, calculer_machine2=machine2_active)
    duree_calcul = time.time() - debut_calcul

    lignes_m1 = classer(par_minute_m1)
    logger.info("Machine 1 : %d minute(s) classée(s) en %.1f s", len(lignes_m1), duree_calcul)
    for l in lignes_m1:
        logger.info("  %s  %s  %-6s  %5.1f %% de mouvement (%d s)",
                    machine1_id, l["minute"], l["etat"], l["pourcentage"], l["secondes_analysees"])

    lignes_m2 = []
    if machine2_active:
        lignes_m2 = classer(par_minute_m2)
        logger.info("Machine 2 (%s) : %d minute(s) classée(s)", machine2_id, len(lignes_m2))
        for l in lignes_m2:
            logger.info("  %s  %s  %-6s  %5.1f %% de secondes en mouvement (%d s)",
                        machine2_id, l["minute"], l["etat"], l["pourcentage"], l["secondes_analysees"])

    if retard_restant and retard_restant > timedelta(0):
        logger.warning(
            "Fenêtre traitée, mais %s de retard restent à rattraper "
            "(repris automatiquement au prochain passage).", retard_restant)

    if args.sans_envoi:
        return 0

    url, jeton = config_api()
    # La file d'abord : sans ça, un long incident remettrait les minutes dans
    # le désordre dans la base — sans conséquence (elles sont horodatées),
    # mais on préfère renvoyer les retards avant le frais. Partagée entre les
    # deux machines (chaque lot porte son propre machine_id).
    vider_la_file(url, jeton)

    # Filtre, par machine : si SON curseur est déjà plus avancé que le début
    # de cette fenêtre partagée (c'est l'AUTRE machine qui était en retard et
    # qui a fait reculer le début commun, voir fenetre_a_analyser), ne pas
    # renvoyer des minutes que cette machine a déjà confirmées. Sans effet en
    # mode manuel (curseur jamais lu), ni au premier lancement (curseur None).
    curseur_m1 = lire_curseur(machine1_id) if mode_auto else None
    lignes_m1_a_envoyer = [
        l for l in lignes_m1
        if curseur_m1 is None or datetime.strptime(l["minute"], "%Y-%m-%dT%H:%M") >= curseur_m1
    ]
    reussite_m1 = traiter_envoi_machine(machine1_id, lignes_m1_a_envoyer, fin, url, jeton, mode_auto)

    reussite_m2 = True
    if machine2_active:
        curseur_m2 = lire_curseur(machine2_id) if mode_auto else None
        lignes_m2_a_envoyer = [
            l for l in lignes_m2
            if curseur_m2 is None or datetime.strptime(l["minute"], "%Y-%m-%dT%H:%M") >= curseur_m2
        ]
        reussite_m2 = traiter_envoi_machine(machine2_id, lignes_m2_a_envoyer, fin, url, jeton, mode_auto)

    return 0 if (reussite_m1 and reussite_m2) else 1


if __name__ == "__main__":
    sys.exit(main())
