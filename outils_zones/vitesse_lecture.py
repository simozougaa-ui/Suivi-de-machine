"""Mesure la vitesse de lecture du DVR par plusieurs méthodes, pour
comprendre pourquoi la production (machine_etat/detecter_marche_arret.py)
reçoit parfois moins de 15 images/s en relecture RTSP (un passage de 5 min
de vidéo dure alors ~6 min, le retard grandit).

Outil LOCAL : n'envoie rien à l'application, n'écrit aucun curseur, ne
modifie rien dans machine_etat/ ni aucun réglage de production. Réutilise
le code de src/camera_stream.py (mêmes URL, mêmes identifiants .env, même
ouverture de flux) pour mesurer EXACTEMENT la méthode de la production.

Les identifiants ne sont JAMAIS affichés : aucune URL contenant un mot de
passe n'est écrite dans la sortie ni dans le fichier (voir masquer()).

Méthodes mesurées (chacune indépendante, délai maximal par méthode) :
  a. référence : lecture de production, sur une fenêtre ANCIENNE puis une
     fenêtre RÉCENTE (la vitesse dépend-elle de l'ancienneté ?)
  b. transport RTSP forcé en TCP, puis en UDP
  c. deux lectures RTSP parallèles (deux moitiés de fenêtre en même temps)
  d. téléchargement fichier via l'interface HTTP du DVR Dahua (loadfile)
  e. relecture accélérée (en-tête Scale RTSP de Dahua)

Usage (voir NOTES-SESSION.md pour arrêter le timer d'abord) :
    python3 outils_zones/vitesse_lecture.py
    python3 outils_zones/vitesse_lecture.py --duree 30 --repetitions 3
"""

import argparse
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _commun  # noqa: E402
from src.camera_stream import build_rtsp_playback_url, open_stream  # noqa: E402

FPS_CIBLE = 15.0
DUREE_DEFAUT = 30          # secondes de vidéo mesurées par méthode
DELAI_MAX_DEFAUT = 90      # secondes réelles maxi par méthode (lecture bloquée)
AGE_ANCIENNE_H = 2         # ancienneté de la fenêtre « ancienne »
RETARD_RECENTE_MIN = 7     # fin de la fenêtre « récente » = maintenant - 7 min

FICHIER_SORTIE = os.path.join(_commun.SORTIES_DIR, "vitesse_lecture.txt")


class Resultat:
    """Mesure d'une méthode. `erreur` non nul => échec (les images par
    seconde ne comptent pas)."""

    def __init__(self, nom):
        self.nom = nom
        self.images = 0
        self.duree_s = 0.0
        self.ouverture_s = float("nan")
        self.erreur = None
        self.note = None

    @property
    def ips(self):
        return self.images / self.duree_s if (self.duree_s > 0 and not self.erreur) else 0.0

    def verdict(self):
        if self.erreur:
            return f"échec : {self.erreur}"
        if self.ips > FPS_CIBLE:
            return "plus rapide que le temps réel"
        return "temps réel"


def masquer(texte, mots_secrets=()):
    """Retire tout identifiant d'un texte avant affichage : la partie
    `user:pass@` d'une URL rtsp/http, et en plus chaque mot de
    `mots_secrets` (ex. le mot de passe .env, au cas où il apparaîtrait
    ailleurs). Ne lève jamais : sert dans des messages d'erreur."""
    import re
    texte = str(texte)
    texte = re.sub(r"(rtsp|rtsps|http|https)://[^/\s]*@", r"\1://***@", texte)
    for mot in mots_secrets:
        if mot:
            texte = texte.replace(mot, "***")
    return texte


def _secrets_env():
    return tuple(v for v in (os.getenv("DVR_PASSWORD"), os.getenv("DVR_USER")) if v)


def mesurer_flux(ouvrir, total_images, delai_max, horloge=time.monotonic):
    """Cœur mesurable, SANS dépendance au DVR réel (testable avec un faux
    lecteur) : ouvre un flux via `ouvrir()` (callable renvoyant un objet
    avec read() -> (ok, frame) et release()), lit jusqu'à `total_images`
    images, l'échec d'une lecture, ou le dépassement de `delai_max`
    secondes. Renvoie (images, duree_s, ouverture_s, interrompu)."""
    t0 = horloge()
    capture = ouvrir()
    t_ouverture = horloge() - t0
    images = 0
    interrompu = False
    try:
        while images < total_images:
            if horloge() - t0 >= delai_max:
                interrompu = True
                break
            ok, _ = capture.read()
            if not ok:
                break
            images += 1
    finally:
        capture.release()
    return images, horloge() - t0, t_ouverture, interrompu


def _avec_delai(fn, delai, resultat):
    """Exécute fn() dans un thread démon et attend au plus `delai` secondes.
    Si fn ne rend pas la main (lecture C bloquée dans ffmpeg), marque le
    résultat « délai dépassé » et laisse le thread mourir avec le process —
    les méthodes suivantes continuent."""
    erreur = {}

    def cible():
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 — tout échec reste isolé
            erreur["exc"] = exc

    th = threading.Thread(target=cible, daemon=True)
    th.start()
    th.join(delai + 5)   # marge au-delà du délai interne de la méthode
    if th.is_alive():
        resultat.erreur = resultat.erreur or f"délai dépassé (> {delai} s, lecture bloquée)"
    elif "exc" in erreur:
        resultat.erreur = resultat.erreur or masquer(erreur["exc"], _secrets_env())


def _fenetres(duree, maintenant=None):
    t = (maintenant or datetime.now()).replace(microsecond=0)
    fin_recente = t - timedelta(minutes=RETARD_RECENTE_MIN)
    debut_recente = fin_recente - timedelta(seconds=duree)
    fin_ancienne = t - timedelta(hours=AGE_ANCIENNE_H)
    debut_ancienne = fin_ancienne - timedelta(seconds=duree)
    return (debut_ancienne, fin_ancienne), (debut_recente, fin_recente)


def _mesure_rtsp(nom, debut, fin, duree, delai_max, options_ffmpeg=None):
    """Une mesure RTSP (production, ou transport forcé). `options_ffmpeg` :
    valeur de OPENCV_FFMPEG_CAPTURE_OPTIONS appliquée le temps de l'ouverture
    (puis restaurée), pour forcer le transport ; notée dans le résultat."""
    r = Resultat(nom)
    total = int(duree * FPS_CIBLE)

    def faire():
        ancienne = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
        if options_ffmpeg is not None:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = options_ffmpeg
            r.note = f"OPENCV_FFMPEG_CAPTURE_OPTIONS={options_ffmpeg}"
        try:
            url = build_rtsp_playback_url(debut, fin)
            r.images, r.duree_s, r.ouverture_s, interrompu = mesurer_flux(
                lambda: open_stream(url), total, delai_max)
            if interrompu and r.images < total:
                r.note = (r.note + " ; " if r.note else "") + "arrêté au délai maxi"
        finally:
            if options_ffmpeg is not None:
                if ancienne is None:
                    os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
                else:
                    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = ancienne

    _avec_delai(faire, delai_max, r)
    return r


def _mesure_parallele(debut, fin, duree, delai_max):
    """Deux sessions RTSP simultanées sur les deux moitiés de [debut, fin].
    Le temps total réel est le mur d'horloge des deux threads en parallèle ;
    s'il approche la moitié du temps séquentiel, le DVR sert bien deux flux
    à la fois. Détecte le refus de la 2e session (ouverture en échec)."""
    r = Resultat("c. deux lectures parallèles")
    milieu = debut + (fin - debut) / 2
    segments = [(debut, milieu), (milieu, fin)]
    total_demi = int((duree / 2) * FPS_CIBLE)
    sous = [Resultat("seg1"), Resultat("seg2")]

    def lire(i):
        d, f = segments[i]
        url = build_rtsp_playback_url(d, f)
        try:
            sous[i].images, sous[i].duree_s, sous[i].ouverture_s, _ = mesurer_flux(
                lambda: open_stream(url), total_demi, delai_max)
        except Exception as exc:  # noqa: BLE001
            sous[i].erreur = masquer(exc, _secrets_env())

    def faire():
        t0 = time.monotonic()
        threads = [threading.Thread(target=lire, args=(i,), daemon=True) for i in (0, 1)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(delai_max)
        r.duree_s = time.monotonic() - t0
        r.images = sous[0].images + sous[1].images
        if sous[1].erreur:
            r.erreur = f"2e session refusée ({sous[1].erreur})"
        elif sous[0].erreur:
            r.erreur = sous[0].erreur
        else:
            r.note = (f"seg1 {sous[0].images} img, seg2 {sous[1].images} img "
                      f"en parallèle ; équivaut à {r.ips:.1f} img/s sur la fenêtre")

    _avec_delai(faire, delai_max, r)
    return r


def _mesure_http(debut, fin, delai_max):
    """Téléchargement fichier via l'interface HTTP Dahua (loadfile.cgi), en
    authentification digest (jamais d'identifiants dans l'URL). Mesure le
    débit. Échec propre si l'API n'existe pas ou refuse."""
    r = Resultat("d. téléchargement HTTP (fichier)")

    def faire():
        try:
            import requests
            from requests.auth import HTTPDigestAuth
        except ImportError:
            r.erreur = "module requests absent"
            return
        ip = os.getenv("DVR_IP")
        user = os.getenv("DVR_USER")
        mdp = os.getenv("DVR_PASSWORD")
        port_http = os.getenv("DVR_HTTP_PORT", "80")
        canal = os.getenv("CAMERA_CHANNEL")
        if not all((ip, user, mdp, canal)):
            r.erreur = "identifiants DVR incomplets dans .env"
            return
        fmt = "%Y-%m-%d %H:%M:%S"
        url = (f"http://{ip}:{port_http}/cgi-bin/loadfile.cgi?action=startLoad"
               f"&channel={canal}&startTime={debut.strftime(fmt)}"
               f"&endTime={fin.strftime(fmt)}")
        t0 = time.monotonic()
        octets = 0
        try:
            with requests.get(url, auth=HTTPDigestAuth(user, mdp), stream=True,
                              timeout=(10, delai_max)) as rep:
                if rep.status_code != 200:
                    r.erreur = f"HTTP {rep.status_code} (loadfile non supporté ?)"
                    return
                for bloc in rep.iter_content(65536):
                    octets += len(bloc)
                    if time.monotonic() - t0 >= delai_max:
                        r.note = "arrêté au délai maxi"
                        break
        except Exception as exc:  # noqa: BLE001
            r.erreur = masquer(exc, _secrets_env())
            return
        r.duree_s = time.monotonic() - t0
        mo = octets / (1024 * 1024)
        debit = mo / r.duree_s if r.duree_s else 0
        r.note = f"{mo:.1f} Mo en {r.duree_s:.1f} s = {debit:.2f} Mo/s"
        # Pas d'images décodées : on ne peut pas donner d'img/s fiable, mais
        # un fichier de N minutes téléchargé en T < N*60 s est « plus rapide
        # que le temps réel ». On le signale dans la note.

    _avec_delai(faire, delai_max, r)
    # r.images reste 0 : le tableau affichera le débit via la note.
    return r


def _mesure_scale(debut, fin, duree, delai_max):
    """Relecture accélérée (en-tête Scale de la requête PLAY, propre à
    Dahua). OpenCV/ffmpeg n'expose pas cet en-tête : on tente l'option
    ffmpeg, mais c'est généralement non supporté — signalé sans bloquer."""
    r = Resultat("e. relecture accélérée (Scale)")
    r.erreur = ("non supporté via OpenCV (l'en-tête Scale RTSP n'est pas "
                "exposé par ffmpeg) — à tester avec le SDK Dahua si besoin")
    return r


def mesurer_tout(duree, delai_max, maintenant=None):
    """Enchaîne les méthodes, affiche la progression, renvoie la liste des
    Resultat (ordre d'affichage)."""
    (deb_anc, fin_anc), (deb_rec, fin_rec) = _fenetres(duree, maintenant)
    resultats = []

    def etape(r):
        print(f"  -> {r.nom} : {r.verdict()}"
              + (f" ({r.ips:.1f} img/s)" if r.ips else "")
              + (f" [{r.note}]" if r.note else ""), flush=True)
        resultats.append(r)

    print("a. référence (méthode de production)", flush=True)
    etape(_mesure_rtsp("a. réf. fenêtre ancienne (~2 h)", deb_anc, fin_anc, duree, delai_max))
    etape(_mesure_rtsp("a. réf. fenêtre récente (~7 min)", deb_rec, fin_rec, duree, delai_max))

    print("b. transport RTSP forcé", flush=True)
    etape(_mesure_rtsp("b. RTSP sur TCP", deb_rec, fin_rec, duree, delai_max,
                       options_ffmpeg="rtsp_transport;tcp"))
    etape(_mesure_rtsp("b. RTSP sur UDP", deb_rec, fin_rec, duree, delai_max,
                       options_ffmpeg="rtsp_transport;udp"))

    print("c. deux lectures parallèles", flush=True)
    etape(_mesure_parallele(deb_rec, fin_rec, duree, delai_max))

    print("d. téléchargement HTTP", flush=True)
    etape(_mesure_http(deb_rec, fin_rec, delai_max))

    print("e. relecture accélérée", flush=True)
    etape(_mesure_scale(deb_rec, fin_rec, duree, delai_max))

    return resultats


def construire_tableau(resultats, duree, timer_actif):
    """Tableau final étroit (téléphone) + conclusion."""
    lignes = []
    if timer_actif:
        lignes.append("!! suivi-machine-etat.service ACTIF pendant la mesure :")
        lignes.append("   les chiffres sont faussés. Arrêter le timer (voir NOTES).")
        lignes.append("")
    lignes.append(f"Vitesse de lecture DVR ({duree} s de vidéo/méthode, cible {FPS_CIBLE:.0f} img/s)")
    lignes.append("-" * 44)
    for r in resultats:
        lignes.append(r.nom)
        detail = f"  {r.ips:5.1f} img/s  {r.verdict()}" if r.ips else f"  {r.verdict()}"
        lignes.append(detail)
        if r.note:
            lignes.append(f"  ({r.note})")
    lignes.append("-" * 44)

    succes = [r for r in resultats if not r.erreur and r.ips > 0]
    if succes:
        meilleur = max(succes, key=lambda r: r.ips)
        secondes_5min = 5 * 60 * FPS_CIBLE / meilleur.ips
        lignes.append(f"Meilleure : {meilleur.nom} ({meilleur.ips:.1f} img/s).")
        lignes.append(f"Fenêtre de 5 min traitée en ~{secondes_5min:.0f} s "
                      f"({'< 300 s, OK' if secondes_5min < 300 else '> 300 s, retard'}).")
    else:
        lignes.append("Aucune méthode n'a donné d'img/s exploitable (voir ci-dessus).")
    return "\n".join(lignes) + "\n"


def timer_de_detection_actif():
    """True si suivi-machine-etat.service tourne (ses lectures fausseraient
    la mesure). Ne l'arrête PAS : se contente d'avertir."""
    try:
        sortie = subprocess.run(["systemctl", "is-active", "suivi-machine-etat.service"],
                                capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return sortie.stdout.strip() == "active"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--duree", type=int, default=DUREE_DEFAUT,
                        help=f"secondes de vidéo par méthode (défaut {DUREE_DEFAUT})")
    parser.add_argument("--delai-max", type=int, default=DELAI_MAX_DEFAUT,
                        help=f"secondes réelles maxi par méthode (défaut {DELAI_MAX_DEFAUT})")
    parser.add_argument("--repetitions", type=int, default=1,
                        help="répète la mesure pour voir la variabilité (défaut 1)")
    args = parser.parse_args(argv)

    actif = timer_de_detection_actif()
    if actif:
        print("!! suivi-machine-etat.service est ACTIF : mesures faussées.")
        print("   Voir NOTES-SESSION.md pour arrêter le timer d'abord.\n")

    tableaux = []
    for n in range(1, args.repetitions + 1):
        if args.repetitions > 1:
            print(f"\n===== Répétition {n}/{args.repetitions} =====", flush=True)
        resultats = mesurer_tout(args.duree, args.delai_max)
        tableau = construire_tableau(resultats, args.duree, actif)
        print("\n" + tableau, flush=True)
        tableaux.append(tableau if args.repetitions == 1 else f"--- Répétition {n} ---\n{tableau}")

    os.makedirs(_commun.SORTIES_DIR, exist_ok=True)
    with open(FICHIER_SORTIE, "w", encoding="utf-8") as f:
        f.write("\n".join(tableaux))
    print(f"Tableau écrit dans {FICHIER_SORTIE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
