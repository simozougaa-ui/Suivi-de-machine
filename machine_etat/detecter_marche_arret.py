"""Détection MARCHE / ARRÊT de la machine, minute par minute, pour la production.

MÉTHODE (validée sur plusieurs plages horaires réelles, voir NOTES-SESSION.md) :
deux images consécutives espacées d'une seconde sont comparées dans une ZONE FIXE
de l'image ; si assez de pixels ont changé, cette seconde-là « a du mouvement ».
Une minute est classée « marche » quand plus de 40 % de ses secondes ont du
mouvement, « arrêt » sinon.

    zone            x1=320, y1=200, x2=400, y2=240  (sur une frame 1280x720)
    flou            GaussianBlur 7x7, sur l'image ENTIÈRE avant découpage de la
                     zone (voir zone_grise) — sans lui, le bruit de capteur/
                     compression déclenchait « marche » en continu, y compris
                     machine à l'arrêt (correctif du 2026-10-01)
    seuil pixel     12    (différence d'intensité au-delà de laquelle un pixel
                           est dit « changé »)
    seuil fraction  0.015 (part de pixels changés au-delà de laquelle la seconde
                           est dite « avec mouvement »)
    seuil minute    40 %  (part de secondes avec mouvement → « marche »)

JAMAIS LE FLUX DIRECT. Le calcul sur le flux live donne des faux positifs (bug
non résolu, voir NOTES-SESSION.md). On relit donc les ENREGISTREMENTS du DVR en
RTSP playback, avec un retard volontaire d'au moins RETARD_MINUTES (5 min), ce
qui laisse au DVR le temps d'avoir fini d'écrire.

REPRISE SANS TROU (correctif du 2026-10-01, voir NOTES-SESSION.md). Un passage
automatique ne recalcule PAS sa fenêtre à partir de « maintenant » : il reprend
pile où le précédent s'est arrêté (voir lire_curseur/ecrire_curseur,
machine_etat/curseur.json). Avant ce correctif, un passage en retard sur son
horaire (le timer met ~6,5 min à analyser 5 min de vidéo — plus lent que le
temps réel) décalait silencieusement la fenêtre suivante, sautant les minutes
entre les deux sans jamais les analyser ni les signaler comme « non mesurées ».
Chaque passage traite au plus PLAFOND_RATTRAPAGE_MINUTES de vidéo (voir sa
docstring) : le retard sur le direct peut donc grandir si le Jetson est
durablement plus lent que le temps réel, mais plus aucune minute n'est perdue
— seulement affichée plus tard.

AUCUNE IMAGE N'EST ÉCRITE SUR LE DISQUE. Les frames sont décodées, comparées et
jetées au fil de la lecture. C'est la forme la plus sûre de « nettoyer les images
après calcul » : il n'y a rien à nettoyer, donc rien ne s'accumule si le script
est tué en cours de route. L'option --garder-frames force l'écriture dans un
dossier, uniquement pour inspecter un cas douteux à la main.

ENVOI DES RÉSULTATS : POST vers l'application de suivi de production
(suivi-production-imprimerie), authentifié par un jeton porteur. Si l'envoi
échoue (réseau coupé, application en redéploiement), le lot est déposé dans
file_attente/ et réessayé au tour suivant — l'API est idempotente, un renvoi
remplace au lieu de dupliquer.

Usage (production, via systemd timer toutes les 5 minutes) :
    python3 machine_etat/detecter_marche_arret.py

Usage (rattrapage ou vérification d'une plage passée) :
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

# --- Méthode de détection : valeurs VALIDÉES, à ne pas changer sans revalider --
ZONE = (320, 200, 400, 240)          # x1, y1, x2, y2 sur une frame 1280x720
SEUIL_PIXEL = 12
SEUIL_FRACTION = 0.015
SEUIL_MINUTE_POURCENT = 40.0

# Le mainstream du DVR est confirmé à 15 fps (voir dvr_check/). On ne garde
# qu'une frame par seconde : c'est l'échelle à laquelle la méthode a été validée.
FPS_SUPPOSE = 15.0

# Retard volontaire sur le direct (voir en-tête).
RETARD_MINUTES = 5
FENETRE_MINUTES = 5

DOSSIER_FILE = os.path.join(ROOT, "machine_etat", "file_attente")
# Au-delà, la file ne sert plus à rien : l'application a de toute façon perdu
# ces heures-là, et on ne remplit pas le disque du Jetson indéfiniment.
MAX_LOTS_EN_ATTENTE = 288          # 24 h de tours toutes les 5 minutes

# --- Curseur de reprise (correctif du 2026-10-01) --------------------------
# AVANT : chaque passage recalculait sa fenêtre à partir de « maintenant »
# (fenetre_par_defaut), sans mémoire du passage précédent. Un passage en
# retard (le timer met ~6,5 min à analyser 5 min de vidéo, voir
# TimeoutStartSec ci-dessous et NOTES-SESSION.md) décalait donc la fenêtre
# suivante vers l'avant SANS jamais revenir sur l'intervalle sauté — des
# minutes entières (ex. 03:11-03:13 un jour donné) n'étaient simplement
# jamais analysées, ni envoyées, ni marquées « non mesurées » : elles
# disparaissaient silencieusement.
#
# MAINTENANT : la fin de la DERNIÈRE fenêtre analysée est écrite ici après
# chaque passage automatique réussi. Le passage suivant reprend pile à cet
# instant — ni trou, ni chevauchement — quel que soit son retard. Seul le
# mode automatique (sans --date/--debut/--fin) lit et écrit ce fichier : un
# rattrapage manuel pour inspecter une plage passée ne doit pas perturber la
# progression du service.
FICHIER_CURSEUR = os.path.join(ROOT, "machine_etat", "curseur.json")

# Plafond de rattrapage par passage : volontairement égal à FENETRE_MINUTES,
# pas plus. TimeoutStartSec=480s (voir deploy/suivi-machine-etat.service) et
# la vitesse observée (~6,5 min pour analyser 5 min de vidéo, donc le Jetson
# est PLUS LENT que le temps réel — le flou ajouté par ce correctif l'alourdit
# encore un peu) donnent une marge sûre d'environ 6 min par passage. Un
# plafond plus grand (ex. rattraper une heure d'un coup après une panne)
# risquerait de se faire tuer par systemd en pleine fenêtre, perdant tout son
# travail au lieu d'avancer d'un cran sûr. Avec ce plafond, un passage en
# retard avance quand même systématiquement : le retard ne se résorbe pas en
# un seul passage, mais ne grandit plus jamais sans borne au prix d'une perte
# de données — seulement au prix d'un affichage plus tardif.
PLAFOND_RATTRAPAGE_MINUTES = FENETRE_MINUTES


def lire_curseur():
    """Fin de la dernière fenêtre analysée avec succès, ou None si le service
    n'a jamais tourné (ou si le fichier est illisible : on repart alors du
    comportement par défaut plutôt que de planter)."""
    try:
        with open(FICHIER_CURSEUR, encoding="utf-8") as f:
            valeur = json.load(f)["derniere_fin_analysee"]
        return datetime.strptime(valeur, "%Y-%m-%dT%H:%M")
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        return None


def ecrire_curseur(fin):
    os.makedirs(os.path.dirname(FICHIER_CURSEUR), exist_ok=True)
    with open(FICHIER_CURSEUR, "w", encoding="utf-8") as f:
        json.dump({"derniere_fin_analysee": fin.strftime("%Y-%m-%dT%H:%M")}, f)


def zone_grise(frame):
    """Extrait la zone de travail en niveaux de gris, flouté.

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
    """True si assez de pixels ont changé entre deux images consécutives."""
    diff = cv2.absdiff(precedente, courante)
    changes = int(np.count_nonzero(diff > SEUIL_PIXEL))
    total = diff.size
    return total > 0 and (changes / total) >= SEUIL_FRACTION


def analyser_fenetre(debut, fin, garder_frames=None):
    """Lit les enregistrements entre deux instants et classe chaque minute.

    Returns:
        dict minute (datetime tronqué à la minute) -> (secondes_avec_mouvement,
        secondes_analysees). Une minute absente du dict n'a tout simplement pas
        été lue : elle ne sera PAS envoyée, et l'application l'affichera comme
        « non mesurée » — jamais comme un arrêt.
    """
    duree = int((fin - debut).total_seconds())
    if duree <= 0:
        raise ValueError("Fenêtre vide : la fin doit être après le début.")

    capture = open_stream(build_rtsp_playback_url(debut, fin))
    pas = max(1, int(round(FPS_SUPPOSE)))       # une frame gardée par seconde
    par_minute = {}
    precedente = None
    index = 0
    gardees = 0

    if garder_frames:
        os.makedirs(garder_frames, exist_ok=True)

    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if index % pas != 0:
                index += 1
                continue

            instant = debut + timedelta(seconds=gardees)
            if instant >= fin:
                break
            courante = zone_grise(frame)

            if garder_frames:
                cv2.imwrite(
                    os.path.join(garder_frames, instant.strftime("frame_%H%M%S.jpg")),
                    frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])

            if precedente is not None:
                minute = instant.replace(second=0, microsecond=0)
                avec, total = par_minute.get(minute, (0, 0))
                if seconde_avec_mouvement(precedente, courante):
                    avec += 1
                par_minute[minute] = (avec, total + 1)

            precedente = courante
            gardees += 1
            index += 1
    finally:
        capture.release()

    return par_minute


def classer(par_minute):
    """Transforme les compteurs en lignes prêtes pour l'API.

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


def config_api():
    url = (os.getenv("SUIVI_API_URL") or "").strip().rstrip("/")
    jeton = (os.getenv("SUIVI_API_JETON") or "").strip()
    machine = (os.getenv("MACHINE_ETAT_ID") or "machine-1").strip()
    if not url or not jeton:
        raise RuntimeError(
            "SUIVI_API_URL et SUIVI_API_JETON sont requis dans .env "
            "(voir .env.example). Sans eux, aucun résultat ne peut être envoyé."
        )
    return url, jeton, machine


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


def mettre_en_file(lot):
    os.makedirs(DOSSIER_FILE, exist_ok=True)
    nom = datetime.now().strftime("lot_%Y%m%d_%H%M%S_%f.json")
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
    """Réessaie les lots en attente, du plus ancien au plus récent."""
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


def fenetre_a_analyser(maintenant=None):
    """Fenêtre à traiter par un passage AUTOMATIQUE (sans --date/--debut/--fin).

    Reprend exactement après la fin du DERNIER passage réussi (voir
    lire_curseur) : ni trou ni chevauchement, quel que soit le retard
    accumulé. Sans curseur (premier lancement, ou fichier absent/illisible) :
    se rabat sur [T-10min, T-5min], le comportement d'avant ce correctif.

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

    curseur = lire_curseur()
    debut = curseur if curseur is not None else fin_securisee - timedelta(minutes=FENETRE_MINUTES)

    if debut >= fin_securisee:
        return None, None, None

    fin = min(fin_securisee, debut + timedelta(minutes=PLAFOND_RATTRAPAGE_MINUTES))
    return debut, fin, fin_securisee - fin


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="AAAA-MM-JJ (rattrapage d'une plage passée)")
    ap.add_argument("--debut", help="HH:MM")
    ap.add_argument("--fin", help="HH:MM")
    ap.add_argument("--garder-frames", metavar="DOSSIER",
                    help="écrit les frames décodées (débogage seulement)")
    ap.add_argument("--sans-envoi", action="store_true",
                    help="calcule et affiche, n'envoie rien")
    ap.add_argument("--verbeux", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbeux else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")

    # Mode automatique (timer systemd) = ni --date ni --debut ni --fin : seul
    # ce mode lit/écrit le curseur de reprise (voir FICHIER_CURSEUR) — un
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
        debut, fin, retard_restant = fenetre_a_analyser()
        if debut is None:
            logger.info("Rien de nouveau à analyser pour l'instant (déjà à jour avec le DVR).")
            return 0

    logger.info("Analyse des enregistrements de %s à %s",
                debut.strftime("%Y-%m-%d %H:%M"), fin.strftime("%H:%M"))

    debut_calcul = time.time()
    par_minute = analyser_fenetre(debut, fin, args.garder_frames)
    lignes = classer(par_minute)
    logger.info("%d minute(s) classée(s) en %.1f s", len(lignes), time.time() - debut_calcul)
    for l in lignes:
        logger.info("  %s  %-6s  %5.1f %% de mouvement (%d s)",
                    l["minute"], l["etat"], l["pourcentage"], l["secondes_analysees"])

    if mode_auto:
        # Écrit APRÈS un analyser_fenetre() qui n'a pas levé d'exception, donc
        # après une lecture DVR terminée normalement (voir sa docstring) —
        # qu'il y ait ou non des minutes exploitables dedans (classer() peut
        # toutes les rejeter pour trop peu de secondes lues). La fenêtre a été
        # TENTÉE : on avance, un trou honnête ne se retente pas indéfiniment.
        # Indépendant de l'envoi à l'API (ci-dessous) : la file d'attente gère
        # déjà les échecs réseau séparément, pas la peine de relire le DVR en
        # plus si l'application est injoignable.
        ecrire_curseur(fin)
        if retard_restant and retard_restant > timedelta(0):
            logger.warning(
                "Fenêtre traitée, mais %s de retard restent à rattraper "
                "(repris automatiquement au prochain passage).", retard_restant)

    if args.sans_envoi:
        return 0
    if not lignes:
        logger.warning("Aucune minute exploitable : rien à envoyer.")
        return 0

    url, jeton, machine = config_api()
    lot = {"machine_id": machine, "minutes": lignes}
    # La file d'abord : sans ça, un long incident remettrait les minutes dans le
    # désordre dans la base — sans conséquence (elles sont horodatées), mais on
    # préfère renvoyer les retards avant le frais.
    vider_la_file(url, jeton)
    try:
        r = envoyer(lot, url, jeton)
        logger.info("Envoyé : %s minute(s) enregistrée(s).", r.get("enregistrees"))
    except Exception as e:                                        # noqa: BLE001
        logger.error("%s", e)
        mettre_en_file(lot)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
