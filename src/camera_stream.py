"""Connexion au flux RTSP du DVR Hikvision et lecture continue des images.

Les identifiants et l'adresse du DVR sont lus depuis les variables
d'environnement (voir .env.example) — jamais en dur dans le code.
"""

import logging
import os
import time

import cv2
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_RECONNECT_DELAY_SECONDS = 5


def build_rtsp_url():
    """Construit l'URL RTSP à partir des variables d'environnement.

    Lève une RuntimeError explicite si une variable requise est absente,
    plutôt que de laisser cv2 échouer silencieusement avec une URL invalide.
    """
    ip = os.getenv("DVR_IP")
    user = os.getenv("DVR_USER")
    password = os.getenv("DVR_PASSWORD")
    port = os.getenv("DVR_PORT", "554")
    channel = os.getenv("CAMERA_CHANNEL")

    missing = [
        name
        for name, value in (
            ("DVR_IP", ip),
            ("DVR_USER", user),
            ("DVR_PASSWORD", password),
            ("CAMERA_CHANNEL", channel),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Variables d'environnement manquantes: "
            f"{', '.join(missing)}. Copiez .env.example vers .env et "
            "renseignez les valeurs (voir README.md)."
        )

    return f"rtsp://{user}:{password}@{ip}:{port}/Streaming/Channels/{channel}"


def _safe_url_for_logs(url):
    """Retourne l'URL RTSP sans les identifiants, pour les logs."""
    return url.split("@")[-1] if "@" in url else url


def open_stream(rtsp_url=None):
    """Ouvre une connexion au flux RTSP et retourne le cv2.VideoCapture.

    Lève une ConnectionError si l'ouverture échoue.
    """
    url = rtsp_url or build_rtsp_url()
    capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not capture.isOpened():
        capture.release()
        raise ConnectionError(
            f"Impossible d'ouvrir le flux RTSP ({_safe_url_for_logs(url)}). "
            "Vérifiez le réseau, l'adresse IP, le port et les identifiants du DVR."
        )
    return capture


def frames(rtsp_url=None, reconnect_delay=DEFAULT_RECONNECT_DELAY_SECONDS):
    """Générateur qui retourne les images du flux RTSP en continu.

    Se reconnecte automatiquement (avec un délai) en cas de coupure réseau,
    de flux indisponible ou d'erreur de lecture — le boîtier Jetson devant
    tourner sans surveillance, ce générateur ne s'arrête jamais de lui-même.
    """
    capture = None
    while True:
        try:
            if capture is None:
                logger.info("Connexion au flux RTSP...")
                capture = open_stream(rtsp_url)
                logger.info("Flux RTSP connecté.")

            ret, frame = capture.read()
            if not ret or frame is None:
                logger.warning(
                    "Lecture d'image échouée, reconnexion dans %ss...",
                    reconnect_delay,
                )
                capture.release()
                capture = None
                time.sleep(reconnect_delay)
                continue

            yield frame

        except (ConnectionError, RuntimeError, cv2.error) as exc:
            logger.error("Erreur sur le flux RTSP: %s", exc)
            if capture is not None:
                capture.release()
                capture = None
            time.sleep(reconnect_delay)
