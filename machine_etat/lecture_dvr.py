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

# Délais (2026-10-15). Nuit du 14/10 : le service de téléchargement du DVR
# s'est figé (connexion acceptée, puis plus aucune donnée). Avec l'ancien
# réglage (2 essais, 120 s d'attente sans données chacun), un passage perdait
# ~4 min 30 avant de passer au secours RTSP, qui était alors tué par
# TimeoutStartSec=480 : boucle sans fin, rien compté pendant des heures.
# Désormais : 30 s sans recevoir un octet = DVR figé (normalement le premier
# octet arrive en < 1 s), un seul essai, et 120 s au total pour une fenêtre
# lente mais vivante (mesuré : 5,6 Mo/30 s en 0,8 à 7 s, soit 8 à 70 s
# pour 5 min).
DELAI_INACTIVITE_S = 30
DELAI_TOTAL_S = 120
TENTATIVES_HTTP = 1

# Disjoncteur : après un échec HTTP, les passages suivants lisent en RTSP
# pendant PAUSE_HTTP_MIN au lieu de reperdre du temps sur un DVR figé, puis
# le HTTP est retenté. Fichier d'état à côté de curseur.json (gitignoré).
# 10 min (30 avant le 15/10) : le 2e échec, qui déclenche le redémarrage
# automatique du DVR, arrive ainsi 10 min après le premier et non 30.
FICHIER_DISJONCTEUR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "http_en_panne.json")
PAUSE_HTTP_MIN = 10
FMT_ETAT = "%Y-%m-%dT%H:%M:%S"

# Redémarrage AUTOMATIQUE du DVR (2026-10-15, après le 2e blocage en deux
# jours) : au ECHECS_AVANT_REDEMARRAGE-ième échec HTTP consécutif, le passage
# envoie lui-même l'ordre de redémarrage (sans attendre le retour du DVR,
# trop long pour un passage), puis les passages attendent
# PAUSE_APRES_REDEMARRAGE_MIN sans rien lire avant de retenter le HTTP.
# Garde-fous : jamais deux redémarrages automatiques à moins de
# ECART_MIN_REDEMARRAGES_MIN, au plus MAX_REDEMARRAGES_AUTO_PAR_JOUR par
# jour (un redémarrage coupe l'enregistrement de TOUTES les caméras ~2 min),
# et désactivable par DVR_REDEMARRAGE_AUTO=0 dans .env. Historique dans un
# fichier séparé du disjoncteur (qui est supprimé dès que le HTTP revient).
ECHECS_AVANT_REDEMARRAGE = 2
PAUSE_APRES_REDEMARRAGE_MIN = 8
ECART_MIN_REDEMARRAGES_MIN = 60
MAX_REDEMARRAGES_AUTO_PAR_JOUR = 4
FICHIER_REDEMARRAGES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "redemarrages_auto.json")

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


def telecharger_fenetre(debut, fin, delai_max=DELAI_TOTAL_S, tentatives=TENTATIVES_HTTP,
                        session_get=None, delai_inactivite=DELAI_INACTIVITE_S):
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
            octets = _ecrire_flux(fd, url, auth, delai_max, session_get, delai_inactivite)
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


def _ecrire_flux(fd, url, auth, delai_max, session_get, delai_inactivite=DELAI_INACTIVITE_S):
    """Écrit la réponse HTTP dans le descripteur `fd` (fermé ici). Renvoie le
    nombre d'octets. Lève ErreurTelechargement sur HTTP != 200 ou délai.
    `delai_inactivite` borne l'attente SANS données (DVR figé) ; `delai_max`
    borne la durée totale (DVR lent)."""
    t0 = time.monotonic()
    octets = 0
    reponse = None
    fd_pris = False
    try:
        reponse = session_get(url, auth, 10, delai_inactivite)
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


# --- Disjoncteur « HTTP en panne » -------------------------------------------

def _ecrire_json_atomique(chemin, donnees, prefixe):
    import json
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(chemin), prefix=prefixe, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(donnees, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, chemin)
    except BaseException:
        _supprimer(tmp)
        raise


def _lire_etat_disjoncteur():
    import json
    try:
        with open(FICHIER_DISJONCTEUR, encoding="utf-8") as f:
            etat = json.load(f)
        return {"depuis": datetime.strptime(etat["depuis"], FMT_ETAT),
                "jusqua": datetime.strptime(etat["jusqua"], FMT_ETAT),
                "cause": str(etat.get("cause", "")),
                # Absents d'un fichier écrit avant le 15/10 : 1 échec, pas
                # de redémarrage en cours.
                "echecs": int(etat.get("echecs", 1)),
                "redemarrage": bool(etat.get("redemarrage", False))}
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return None


def http_en_pause(maintenant=None):
    """État du disjoncteur s'il est ENCORE actif (dict depuis/jusqua/cause/
    echecs/redemarrage), sinon None. Un fichier illisible compte comme « pas
    de pause ». redemarrage=True : pause d'attente après un redémarrage
    automatique du DVR (rien ne doit être lu pendant ce temps)."""
    etat = _lire_etat_disjoncteur()
    if etat and (maintenant or datetime.now()) < etat["jusqua"]:
        return etat
    return None


def _ecrire_disjoncteur(depuis, jusqua, cause, echecs, redemarrage):
    _ecrire_json_atomique(FICHIER_DISJONCTEUR, {
        "depuis": depuis.strftime(FMT_ETAT), "jusqua": jusqua.strftime(FMT_ETAT),
        "cause": cause, "echecs": echecs, "redemarrage": redemarrage}, ".http_en_panne.")
    return {"depuis": depuis, "jusqua": jusqua, "cause": cause,
            "echecs": echecs, "redemarrage": redemarrage}


def armer_disjoncteur(cause, maintenant=None):
    """Met le HTTP en pause PAUSE_HTTP_MIN minutes et compte les échecs
    CONSÉCUTIFS (« echecs », remis à zéro seulement quand le HTTP revient ou
    qu'un redémarrage est lancé). Garde l'heure du PREMIER échec (« depuis »)
    tant que le HTTP n'a pas été rétabli, pour que le journal dise depuis
    quand le DVR ne répond plus. Écriture atomique."""
    from datetime import timedelta
    t = (maintenant or datetime.now()).replace(microsecond=0)
    ancien = _lire_etat_disjoncteur()
    depuis = ancien["depuis"] if ancien else t
    echecs = (ancien["echecs"] if ancien else 0) + 1
    return _ecrire_disjoncteur(depuis, t + timedelta(minutes=PAUSE_HTTP_MIN),
                               masquer(cause)[:200], echecs, False)


def armer_pause_redemarrage(maintenant=None):
    """Après l'envoi d'un ordre de redémarrage : aucune lecture pendant
    PAUSE_APRES_REDEMARRAGE_MIN (le DVR est éteint, puis démarre), compteur
    d'échecs remis à zéro. Renvoie l'état écrit."""
    from datetime import timedelta
    t = (maintenant or datetime.now()).replace(microsecond=0)
    ancien = _lire_etat_disjoncteur()
    depuis = ancien["depuis"] if ancien else t
    return _ecrire_disjoncteur(depuis, t + timedelta(minutes=PAUSE_APRES_REDEMARRAGE_MIN),
                               "redémarrage automatique du DVR", 0, True)


def liberer_disjoncteur():
    """Supprime le disjoncteur. Renvoie True s'il existait (HTTP rétabli)."""
    existait = os.path.exists(FICHIER_DISJONCTEUR)
    _supprimer(FICHIER_DISJONCTEUR)
    return existait


# --- Historique et limites des redémarrages automatiques ---------------------

def _lire_redemarrages():
    import json
    try:
        with open(FICHIER_REDEMARRAGES, encoding="utf-8") as f:
            return sorted(datetime.strptime(x, FMT_ETAT) for x in json.load(f))
    except (FileNotFoundError, ValueError, TypeError):
        return []


def redemarrage_auto_permis(maintenant=None):
    """(True, "") si un redémarrage automatique du DVR est permis maintenant,
    sinon (False, raison lisible)."""
    from datetime import timedelta
    if (os.getenv("DVR_REDEMARRAGE_AUTO") or "1").strip() == "0":
        return False, "désactivé par DVR_REDEMARRAGE_AUTO=0 dans .env"
    t = maintenant or datetime.now()
    passes = _lire_redemarrages()
    if passes and t - passes[-1] < timedelta(minutes=ECART_MIN_REDEMARRAGES_MIN):
        return False, (f"déjà redémarré automatiquement à {passes[-1].strftime('%H:%M')} "
                       f"(un seul par {ECART_MIN_REDEMARRAGES_MIN} min)")
    du_jour = [x for x in passes if x.date() == t.date()]
    if len(du_jour) >= MAX_REDEMARRAGES_AUTO_PAR_JOUR:
        return False, (f"déjà {len(du_jour)} redémarrages automatiques aujourd'hui "
                       f"(maximum {MAX_REDEMARRAGES_AUTO_PAR_JOUR}) — vérifier le DVR sur place")
    return True, ""


def noter_redemarrage_auto(maintenant=None):
    """Ajoute un redémarrage automatique à l'historique (7 derniers jours
    gardés). Renvoie le nombre de redémarrages automatiques du jour."""
    from datetime import timedelta
    t = (maintenant or datetime.now()).replace(microsecond=0)
    passes = [x for x in _lire_redemarrages() if t - x < timedelta(days=7)] + [t]
    _ecrire_json_atomique(FICHIER_REDEMARRAGES, [x.strftime(FMT_ETAT) for x in passes],
                          ".redemarrages_auto.")
    return sum(1 for x in passes if x.date() == t.date())


# --- Redémarrage du DVR à distance -------------------------------------------

def _url_base():
    ip, _, _, _, port = _config_http()
    return f"http://{ip}:{port}"


def _dvr_repond(session_get, delai=5):
    """True si le serveur web du DVR répond (n'importe quel code HTTP)."""
    _, user, mdp, _, _ = _config_http()
    try:
        rep = session_get(_url_base() + "/cgi-bin/magicBox.cgi?action=getSystemInfo",
                          (user, mdp), delai, delai)
        try:
            rep.close()
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception:  # noqa: BLE001
        return False


def envoyer_ordre_redemarrage(session_get=None):
    """Envoie SEULEMENT l'ordre de redémarrage (magicBox.cgi?action=reboot,
    auth digest), sans attendre le retour du DVR (utilisé tel quel par le
    redémarrage automatique, dont le passage ne peut pas attendre ~5 min).
    Renvoie (envoye, message). Ne lève jamais."""
    session_get = session_get or _requests_get
    try:
        _, user, mdp, _, _ = _config_http()
        rep = session_get(_url_base() + "/cgi-bin/magicBox.cgi?action=reboot",
                          (user, mdp), 10, 20)
        code = getattr(rep, "status_code", 200)
        try:
            rep.close()
        except Exception:  # noqa: BLE001
            pass
        if code != 200:
            return False, f"le DVR a refusé l'ordre de redémarrage (HTTP {code})."
    except Exception as e:  # noqa: BLE001
        return False, f"ordre de redémarrage non transmis : {masquer(e)}"
    return True, "ordre de redémarrage accepté par le DVR."


def redemarrer_dvr(session_get=None, attente_arret_s=90, attente_retour_s=300,
                   pas_s=5, dormir=time.sleep, horloge=time.monotonic, verifier=None):
    """Redémarre le DVR (magicBox.cgi?action=reboot, auth digest), attend
    qu'il s'arrête puis revienne, puis vérifie qu'un VRAI téléchargement
    passe (le module figé la nuit du 14/10 répondait encore au ping).
    Libère le disjoncteur en cas de succès. Renvoie (succes, message).

    Tout est injectable (session_get, dormir, horloge, verifier) pour les
    tests : aucune attente réelle n'y est faite."""
    session_get = session_get or _requests_get
    envoye, message = envoyer_ordre_redemarrage(session_get)
    if not envoye:
        return False, message
    logger.info("Ordre de redémarrage accepté par le DVR, attente de son arrêt...")

    t0 = horloge()
    vu_arrete = False
    while horloge() - t0 < attente_arret_s:
        if not _dvr_repond(session_get):
            vu_arrete = True
            break
        dormir(pas_s)
    if not vu_arrete:
        logger.warning("Le DVR n'a pas été vu arrêté en %d s (redémarrage très rapide "
                       "ou ignoré) — on vérifie quand même.", attente_arret_s)

    t1 = horloge()
    while horloge() - t1 < attente_retour_s:
        if _dvr_repond(session_get):
            break
        dormir(pas_s)
    else:
        return False, f"le DVR n'est pas revenu en {attente_retour_s} s — vérifier sur place."
    logger.info("Le DVR répond de nouveau, vérification d'un téléchargement...")
    dormir(20)   # le serveur web répond avant que l'enregistreur soit prêt

    if verifier is None:
        def verifier():
            from datetime import timedelta
            fin = datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=10)
            chemin, octets = telecharger_fenetre(fin - timedelta(seconds=30), fin,
                                                 session_get=session_get)
            _supprimer(chemin)
            return octets
    try:
        octets = verifier()
    except Exception as e:  # noqa: BLE001
        return False, f"DVR revenu mais téléchargement toujours en échec : {masquer(e)}"
    liberer_disjoncteur()
    return True, f"DVR redémarré, téléchargement OK ({octets / (1024 * 1024):.1f} Mo pour 30 s)."
