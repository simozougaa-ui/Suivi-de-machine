"""Détection MARCHE / ARRÊT de la machine, minute par minute, pour la production.

MÉTHODE (validée sur plusieurs plages horaires réelles, voir NOTES-SESSION.md) :
deux images consécutives espacées d'une seconde sont comparées dans une ZONE FIXE
de l'image ; si assez de pixels ont changé, cette seconde-là « a du mouvement ».
Une minute est classée « marche » quand plus de 40 % de ses secondes ont du
mouvement, « arrêt » sinon.

    zone            x1=320, y1=200, x2=400, y2=240  (sur une frame 1280x720)
    seuil pixel     12    (différence d'intensité au-delà de laquelle un pixel
                           est dit « changé »)
    seuil fraction  0.015 (part de pixels changés au-delà de laquelle la seconde
                           est dite « avec mouvement »)
    seuil minute    40 %  (part de secondes avec mouvement → « marche »)

JAMAIS LE FLUX DIRECT. Le calcul sur le flux live donne des faux positifs (bug
non résolu, voir NOTES-SESSION.md). On relit donc les ENREGISTREMENTS du DVR en
RTSP playback, avec un retard volontaire : à l'instant T on analyse la fenêtre
[T-10min, T-5min], ce qui laisse au DVR le temps d'avoir fini d'écrire. Les
résultats affichés dans l'application ont donc 5 à 10 minutes de retard sur le
direct — c'est le prix d'une mesure fiable, et c'est assumé.

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


def zone_grise(frame):
    """Extrait la zone de travail en niveaux de gris.

    Le gris suffit : on mesure un CHANGEMENT d'intensité, pas une couleur — et
    il divise par trois le travail de comparaison.
    """
    x1, y1, x2, y2 = ZONE
    return cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)


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


def fenetre_par_defaut(maintenant=None):
    """[T-10min, T-5min], bornée à la minute pleine."""
    t = (maintenant or datetime.now()).replace(second=0, microsecond=0)
    fin = t - timedelta(minutes=RETARD_MINUTES)
    return fin - timedelta(minutes=FENETRE_MINUTES), fin


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

    if args.date and args.debut and args.fin:
        jour = datetime.strptime(args.date, "%Y-%m-%d").date()
        debut = datetime.combine(jour, datetime.strptime(args.debut, "%H:%M").time())
        fin = datetime.combine(jour, datetime.strptime(args.fin, "%H:%M").time())
    elif args.date or args.debut or args.fin:
        ap.error("--date, --debut et --fin vont ensemble.")
    else:
        debut, fin = fenetre_par_defaut()

    logger.info("Analyse des enregistrements de %s à %s",
                debut.strftime("%Y-%m-%d %H:%M"), fin.strftime("%H:%M"))

    debut_calcul = time.time()
    par_minute = analyser_fenetre(debut, fin, args.garder_frames)
    lignes = classer(par_minute)
    logger.info("%d minute(s) classée(s) en %.1f s", len(lignes), time.time() - debut_calcul)
    for l in lignes:
        logger.info("  %s  %-6s  %5.1f %% de mouvement (%d s)",
                    l["minute"], l["etat"], l["pourcentage"], l["secondes_analysees"])

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
