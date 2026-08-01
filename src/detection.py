"""Détection de personnes dans une image via YOLOv8n (ultralytics).

YOLOv8n est utilisé car c'est le modèle le plus léger de la famille YOLOv8
(nano), gratuit et pré-entraîné sur COCO (qui inclut la classe "person"),
ce qui le rend adapté à un boîtier embarqué comme le Jetson Orin Nano Super.
"""

import logging

from ultralytics import YOLO

logger = logging.getLogger(__name__)

MODEL_NAME = "yolov8n.pt"
PERSON_CLASS_ID = 0  # classe "person" dans le jeu COCO utilisé par YOLOv8
DEFAULT_CONFIDENCE_THRESHOLD = 0.5

_model = None


def load_model():
    """Charge (et met en cache) le modèle YOLOv8n.

    Au premier appel, ultralytics télécharge automatiquement les poids
    pré-entraînés si le fichier yolov8n.pt n'est pas déjà présent localement.
    """
    global _model
    if _model is None:
        logger.info("Chargement du modèle %s...", MODEL_NAME)
        _model = YOLO(MODEL_NAME)
        logger.info("Modèle chargé.")
    return _model


def detect_persons(frame, confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD):
    """Détecte les personnes présentes dans une image.

    Args:
        frame: image au format attendu par ultralytics (numpy array BGR,
            typiquement issue de cv2.VideoCapture.read()).
        confidence_threshold: seuil de confiance minimum pour retenir une
            détection.

    Returns:
        Une liste de dicts: {"bbox": (x1, y1, x2, y2), "confidence": float}
        avec des coordonnées en pixels dans le repère de l'image d'entrée.
    """
    model = load_model()
    results = model.predict(
        frame,
        verbose=False,
        classes=[PERSON_CLASS_ID],
        conf=confidence_threshold,
    )

    persons = []
    for result in results:
        for box in result.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            confidence = float(box.conf[0])
            persons.append(
                {
                    "bbox": (int(x1), int(y1), int(x2), int(y2)),
                    "confidence": confidence,
                }
            )
    return persons
