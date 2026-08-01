"""Zone de travail rectangulaire et vérification de présence.

Une détection de personne est considérée "dans la zone" si sa bounding box
chevauche suffisamment le rectangle de zone (et non seulement si elle le
touche), afin d'éviter les faux positifs quand une personne ne fait que
passer en bord de zone.
"""

from dataclasses import dataclass

DEFAULT_MIN_OVERLAP_RATIO = 0.3


@dataclass
class Zone:
    """Rectangle définissant une zone de travail, en coordonnées pixels."""

    x1: int
    y1: int
    x2: int
    y2: int

    def __post_init__(self):
        if self.x1 >= self.x2 or self.y1 >= self.y2:
            raise ValueError(
                "Coordonnées de zone invalides: il faut x1 < x2 et y1 < y2 "
                f"(reçu x1={self.x1}, y1={self.y1}, x2={self.x2}, y2={self.y2})."
            )

    def intersects_bbox(self, bbox, min_overlap_ratio=DEFAULT_MIN_OVERLAP_RATIO):
        """Vérifie si une bounding box chevauche suffisamment la zone.

        Args:
            bbox: tuple (x1, y1, x2, y2) de la détection.
            min_overlap_ratio: fraction minimale de l'aire de la bbox devant
                se trouver dans la zone pour considérer la personne "présente".

        Returns:
            True si le taux de recouvrement atteint le seuil, False sinon.
        """
        bx1, by1, bx2, by2 = bbox

        inter_x1 = max(self.x1, bx1)
        inter_y1 = max(self.y1, by1)
        inter_x2 = min(self.x2, bx2)
        inter_y2 = min(self.y2, by2)

        if inter_x1 >= inter_x2 or inter_y1 >= inter_y2:
            return False

        inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
        bbox_area = (bx2 - bx1) * (by2 - by1)
        if bbox_area <= 0:
            return False

        overlap_ratio = inter_area / bbox_area
        return overlap_ratio >= min_overlap_ratio
