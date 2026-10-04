"""Téléchargement HTTP d'une fenêtre d'enregistrement du DVR Dahua vers un
fichier temporaire, pour la lire ensuite localement (bien plus vite que la
relecture RTSP, qui rejoue à vitesse réelle — voir NOTES-SESSION.md).

Réservé à machine_etat/detecter_marche_arret.py. Mesure du 03/10 (via
outils_zones/vitesse_lecture.py) : ~5,6 Mo pour 30 s de vidéo en ~1,1 s
(~25x le temps réel) contre ~12,6 img/s en RTSP (plus lent que le direct).

Les identifiants et l'adresse du DVR viennent du .env (DVR_IP, DVR_USER,
DVR_PASSWORD, CAMERA_CHANNEL, DVR_HTTP_PORT). Ils ne sont JAMAIS affichés :
l'authentification HTTP digest les garde hors de l'URL, et masquer() nettoie
tout message avant journalisation.
"""

import contextlib
import logging
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime

logger = logging.getLogger("machine_etat")

# Dossier des fichiers temporaires téléchargés. Sur /tmp (souvent un tmpfs,
# rapide) ; droits 0700 (le fichier peut contenir des images de l'atelier).
DOSSIER_TMP = "/tmp/suivi-machine-dvr"

# Estimation du débit d'enregistrement pour la vérification d'espace disque,
# volontairement AU-DESSUS du mesuré (~0,19 Mo/s) pour garder une marge.
OCTETS_PAR_SECONDE_VIDEO = 250_000
# Marge d'espace libre exigée en plus du fichier attendu (le DVR peut
# dépasser l'estimation ; on ne veut jamais remplir le disque du Jetson).
FACTEUR_ESPACE = 3
OCTETS_LIBRES_MIN = 50 * 1024 * 1024        # 50 Mo de plancher absolu

TAILLE_MIN_FICHIER = 64 * 1024              # en dessous : fichier vide/erreur HTML

FMT_DAHUA = "%Y-%m-%d %H:%M:%S"


class ErreurTelechargement(Exception):
    """Le téléchargement HTTP a échoué (réseau, HTTP != 200, disque, taille)."""


class ErreurLectureFichier(Exception):
    """Le fichier téléchargé n'a pas pu être ouvert/décodé localement."""


def masquer(texte, mots_secrets=None):
    """Retire les identifiants d'un message avant journalisation : la partie
    `user:pass@` d'une URL, et chaque mot de `mots_secrets` (mot de passe et
    utilisateur du .env). Ne lève jamais."""
    import re
    if mots_secrets is None:
        mots_secrets = _secrets_env()
    texte = str(texte)
    texte = re.sub(r"(rtsp|rtsps|http|https)://[^/\s]*@", r"\1://***@", texte)
    for mot in mots_secrets:
        if mot:
            texte = texte.replace(mot, "***")
    return texte


def _secrets_env():
    return tuple(v for v in (os.getenv("DVR_PASSWORD"), os.getenv("DVR_USER")) if v)


def _config_http():
    ip = os.getenv("DVR_IP")
    user = os.getenv("DVR_USER")
    mdp = os.getenv("DVR_PASSWORD")
    canal = os.getenv("CAMERA_CHANNEL")
    port = (os.getenv("DVR_HTTP_PORT") or "80").strip()
    manquants = [n for n, v in (("DVR_IP", ip), ("DVR_USER", user),
                                ("DVR_PASSWORD", mdp), ("CAMERA_CHANNEL", canal)) if not v]
    if manquants:
        raise ErreurTelechargement("identifiants DVR incomplets dans .env : "
                                   + ", ".join(manquants))
    return ip, user, mdp, canal, port


def construire_url_http(debut, fin):
    """URL loadfile.cgi SANS identifiants (l'auth digest les fournit à part).
    Renvoie (url, (user, mdp))."""
    ip, user, mdp, canal, port = _config_http()
    url = (f"http://{ip}:{port}/cgi-bin/loadfile.cgi?action=startLoad"
           f"&channel={canal}&startTime={debut.strftime(FMT_DAHUA)}"
           f"&endTime={fin.strftime(FMT_DAHUA)}")
    return url, (user, mdp)


def estimer_octets(duree_s):
    return max(1, int(duree_s)) * OCTETS_PAR_SECONDE_VIDEO


def _verifier_espace(octets_attendus):
    os.makedirs(DOSSIER_TMP, exist_ok=True)
    libres = shutil.disk_usage(DOSSIER_TMP).free
    requis = octets_attendus * FACTEUR_ESPACE + OCTETS_LIBRES_MIN
    if libres < requis:
        raise ErreurTelechargement(
            f"espace disque insuffisant ({libres // (1024*1024)} Mo libres, "
            f"{requis // (1024*1024)} Mo requis) — pas de téléchargement.")


def nettoyer_restes():
    """Supprime tout fichier resté dans DOSSIER_TMP (passage précédent tué
    avant le nettoyage normal). À appeler au démarrage du script."""
    if not os.path.isdir(DOSSIER_TMP):
        return
    for nom in os.listdir(DOSSIER_TMP):
        _supprimer(os.path.join(DOSSIER_TMP, nom))


def _supprimer(chemin):
    try:
        os.remove(chemin)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Impossible de supprimer %s : %s", chemin, masquer(e))


def _requests_get(url, auth, delai_connexion, delai_lecture):
    import requests
    from requests.auth import HTTPDigestAuth
    return requests.get(url, auth=HTTPDigestAuth(*auth), stream=True,
                        timeout=(delai_connexion, delai_lecture))


def telecharger_fenetre(debut, fin, delai_max=120, tentatives=2, session_get=None):
    """Télécharge [debut, fin] vers un fichier temporaire (flux direct sur
    disque, jamais tout en mémoire). Renvoie (chemin, octets). Lève
    ErreurTelechargement en cas d'échec après `tentatives` essais.

    `session_get(url, auth, delai_connexion, delai_lecture)` est injectable
    pour les tests ; par défaut requests.get en digest."""
    session_get = session_get or _requests_get
    duree = int((fin - debut).total_seconds())
    if duree <= 0:
        raise ErreurTelechargement("fenêtre vide (fin <= début).")
    _verifier_espace(estimer_octets(duree))
    url, auth = construire_url_http(debut, fin)
    secrets = _secrets_env()

    derniere = None
    for essai in range(1, tentatives + 1):
        os.makedirs(DOSSIER_TMP, exist_ok=True)
        fd, chemin = tempfile.mkstemp(dir=DOSSIER_TMP, suffix=".dav")
        try:
            os.chmod(chemin, 0o600)
            octets = _ecrire_flux(fd, url, auth, delai_max, session_get)
            if octets < TAILLE_MIN_FICHIER:
                raise ErreurTelechargement(
                    f"fichier trop petit ({octets} o) : réponse vide ou erreur DVR.")
            return chemin, octets
        except ErreurTelechargement as e:
            _supprimer(chemin)
            derniere = masquer(e, secrets)
            logger.warning("Téléchargement DVR échec (essai %d/%d) : %s",
                           essai, tentatives, derniere)
        except Exception as e:  # noqa: BLE001 — tout reste masqué et isolé
            _supprimer(chemin)
            derniere = masquer(e, secrets)
            logger.warning("Téléchargement DVR échec (essai %d/%d) : %s",
                           essai, tentatives, derniere)
    raise ErreurTelechargement(derniere or "échec inconnu")


def _ecrire_flux(fd, url, auth, delai_max, session_get):
    """Écrit la réponse HTTP dans le descripteur `fd` (fermé ici). Renvoie le
    nombre d'octets. Lève ErreurTelechargement sur HTTP != 200 ou délai."""
    t0 = time.monotonic()
    octets = 0
    reponse = None
    fd_pris = False
    try:
        reponse = session_get(url, auth, 10, delai_max)
        code = getattr(reponse, "status_code", 200)
        if code != 200:
            raise ErreurTelechargement(f"HTTP {code} (loadfile refusé/non supporté ?)")
        fd_pris = True
        with os.fdopen(fd, "wb") as sortie:   # prend possession de fd (fermé ici)
            for bloc in reponse.iter_content(65536):
                if time.monotonic() - t0 >= delai_max:
                    raise ErreurTelechargement(f"délai dépassé (> {delai_max} s).")
                if bloc:
                    sortie.write(bloc)
                    octets += len(bloc)
            sortie.flush()
            os.fsync(sortie.fileno())
    finally:
        if not fd_pris:
            try:
                os.close(fd)
            except OSError:
                pass
        if reponse is not None and hasattr(reponse, "close"):
            try:
                reponse.close()
            except Exception:  # noqa: BLE001
                pass
    return octets


@contextlib.contextmanager
def fenetre_telechargee(debut, fin, **kw):
    """Télécharge la fenêtre, donne (chemin, octets), et SUPPRIME le fichier
    à la sortie quoi qu'il arrive (succès, exception, interruption)."""
    chemin, octets = telecharger_fenetre(debut, fin, **kw)
    try:
        yield chemin, octets
    finally:
        _supprimer(chemin)


def ouvrir_fichier(chemin, ouvrir_capture=None):
    """Ouvre le fichier téléchargé avec OpenCV. Si OpenCV n'y arrive pas et
    que ffmpeg est installé, remuxe en MP4 (copie des flux, sans réencodage)
    et réessaie. Lève ErreurLectureFichier si rien ne marche.

    `ouvrir_capture(chemin)` est injectable pour les tests (défaut
    cv2.VideoCapture)."""
    if ouvrir_capture is None:
        import cv2
        ouvrir_capture = lambda c: cv2.VideoCapture(c)  # noqa: E731

    capture = ouvrir_capture(chemin)
    if capture is not None and capture.isOpened():
        return capture, chemin
    if capture is not None:
        capture.release()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ErreurLectureFichier(
            "OpenCV ne sait pas lire le fichier et ffmpeg est absent "
            "(installer ffmpeg, ou garder la lecture RTSP).")
    chemin_mp4 = chemin + ".mp4"
    try:
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", chemin,
                        "-c", "copy", chemin_mp4],
                       check=True, capture_output=True, timeout=120)
    except Exception as e:  # noqa: BLE001
        _supprimer(chemin_mp4)
        raise ErreurLectureFichier(f"remuxage ffmpeg échoué : {masquer(e)}")
    capture = ouvrir_capture(chemin_mp4)
    if capture is None or not capture.isOpened():
        if capture is not None:
            capture.release()
        _supprimer(chemin_mp4)
        raise ErreurLectureFichier("fichier illisible même après remuxage ffmpeg.")
    return capture, chemin_mp4
