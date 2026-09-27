"""Évaluation ISOLÉE d'un détecteur de POSE (points-clés) sur la caméra 15,
en comparaison avec YOLO11n classique (silhouette entière, voir
eval_yolo.py), pour la zone convoyeur spécifiquement.

Contexte (voir NOTES-SESSION.md, "Test YOLO-pose (préparation)") : YOLO11n
classique ne détecte JAMAIS le conducteur dans la zone convoyeur (0/15,
puis 0/57, même à confiance quasi nulle et zone élargie - voir
"Diagnostic 0/57 zone convoyeur"). Cause retenue : le conducteur est
penché, vu de haut, coupé par la poutre/le poteau/la pile de feuilles -
ce n'est pas une silhouette entière reconnaissable. Hypothèse testée ici :
un modèle YOLO-pose, dont chaque point-clé (nez, yeux, oreilles, épaules,
coudes, poignets, hanches, genoux, chevilles) a sa propre confiance, est
plus tolérant à l'occlusion partielle - un seul point-clé de tête ou
d'épaule visible dans la zone pourrait suffire à signaler une présence.

Ne modifie ni eval_yolo.py, ni signature_eval/signature.py, ni aucun
fichier de production. Réutilise SANS LES MODIFIER, par import, les
fonctions déjà existantes d'eval_yolo.py (frames_from_folder, in_zone,
expand_zone, ZONE_CONVOYEUR, ZONE_MACHINE) - voir la remarque du script
demandé : "en appelant la logique existante de eval_yolo.py sans la
modifier".

IMPORTANT (aucun accès DVR/Jetson depuis cet environnement de
développement) : ce script est écrit et testé ICI seulement pour vérifier
qu'il tourne sans erreur, sur les images déjà disponibles localement (pas
les 1440 frames DVR natives de signature_eval/resultats_dvr_reel/
frames_extraites/, gitignorées, qui n'existent que sur le Jetson). Tout
chiffre produit ici est un test de fonctionnement, PAS un résultat DVR -
voir NOTES-SESSION.md pour la marche à suivre exacte sur le Jetson.

Méthode :
1. Chaque image est passée au modèle pose à un seuil de confiance quasi
   nul (--diag-conf, 0.01 par défaut - leçon du diagnostic YOLO
   classique : ne rien filtrer avant d'avoir regardé les scores bruts).
   TOUTES les détections et TOUS leurs points-clés (17, COCO) sont
   loggés bruts dans keypoints_bruts.csv, avec leur position et leur
   confiance individuelle, et un indicateur "dans la zone convoyeur"
   PRÉCALCULÉ pour 3 marges (0, 15, 30 px) - ce fichier permet de refaire
   n'importe quel calcul de seuil/critère SANS relancer l'inférence.
2. detections_pose.csv (une ligne par image) calcule, pour une grille de
   4 seuils de confiance par point-clé (0.05/0.1/0.2/0.3) x 3 marges de
   zone (0/15/30 px) x 4 critères de présence :
     - tete   : >=1 point-clé nez/yeux/oreilles au-dessus du seuil, dans
                la zone
     - epaule : >=1 point-clé épaule (gauche ou droite) au-dessus du
                seuil, dans la zone
     - corps  : >=1 point-clé du haut du corps (tête+épaules+coudes+
                poignets) au-dessus du seuil, dans la zone
     - boite  : centre de la boîte englobante dans la zone (comme YOLO
                classique), boîte filtrée au même seuil
   soit 48 colonnes oui/non, nommées "<critere>_m<marge>_t<seuil>".
3. Comparaison avec YOLO11n classique sur les mêmes images, aux instants
   confirmés (présents ET absents au convoyeur) : réutilise un
   detections.csv déjà produit par eval_yolo.py si fourni
   (--classique-csv), sinon relance sa propre passe d'inférence YOLO11n.pt
   (import du modèle et de ZONE_CONVOYEUR/in_zone d'eval_yolo.py, logique
   inchangée). Produit comparaison_pose_vs_classique.csv (détail par
   instant) et un résumé (taux de détection / faux positifs par variante).

Usage (Jetson, sur les vraies frames DVR - voir yolo_eval/README.md pour
la marche à suivre complète) :
    yolo_eval/.venv/bin/python yolo_eval/eval_pose.py \
        --depuis-dossier signature_eval/resultats_dvr_reel/frames_extraites \
        --instants-presents <fichier, un frame_HHMMSS.jpg par ligne> \
        --instants-absents <fichier, idem> \
        --classique-csv yolo_eval/out/frames_extraites/detections.csv

Usage (test de fonctionnement local, images déjà disponibles) :
    python3 yolo_eval/eval_pose.py --depuis-dossier <dossier de test>
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # pour importer eval_yolo (module frère, non modifié)
from eval_yolo import ZONE_CONVOYEUR, ZONE_MACHINE, expand_zone, frames_from_folder, in_zone  # noqa: E402

# --- Points-clés COCO (ordre Ultralytics, vérifié sur une vraie image) ---
KEYPOINT_NAMES = [
    "nez", "oeil_g", "oeil_d", "oreille_g", "oreille_d",
    "epaule_g", "epaule_d", "coude_g", "coude_d", "poignet_g", "poignet_d",
    "hanche_g", "hanche_d", "genou_g", "genou_d", "cheville_g", "cheville_d",
]
IDX_TETE = [0, 1, 2, 3, 4]
IDX_EPAULE = [5, 6]
IDX_HAUT_CORPS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]  # tete + epaules + coudes + poignets
GROUPES = {"tete": IDX_TETE, "epaule": IDX_EPAULE, "corps": IDX_HAUT_CORPS}

CONF_THRESHOLDS = [0.05, 0.1, 0.2, 0.3]
MARGES_GRID = [0, 15, 30]
DIAG_CONF = 0.01
PERSON_CLASS = 0


def variant_columns():
    """Liste ordonnée des 48 colonnes de critère (+ 'boite'), pour garder
    un ordre stable entre l'écriture et la lecture du CSV."""
    cols = []
    for marge in MARGES_GRID:
        for seuil in CONF_THRESHOLDS:
            for crit in ("tete", "epaule", "corps", "boite"):
                cols.append(f"{crit}_m{marge}_t{seuil}")
    return cols


def evaluer_variantes(detections):
    """detections : liste de (box_conf, x1,y1,x2,y2, keypoints[17][3]).
    Retourne {colonne: 'oui'/'non'} pour toutes les combinaisons de la
    grille, à partir des détections BRUTES d'une seule image (permet de
    tout recalculer depuis keypoints_bruts.csv sans réinférer)."""
    resultat = {}
    for marge in MARGES_GRID:
        zone = expand_zone(ZONE_CONVOYEUR, marge)
        for seuil in CONF_THRESHOLDS:
            flags = {"tete": False, "epaule": False, "corps": False, "boite": False}
            for box_conf, x1, y1, x2, y2, kpts in detections:
                if box_conf >= seuil and in_zone((x1, y1, x2, y2), zone):
                    flags["boite"] = True
                for crit, idxs in GROUPES.items():
                    for i in idxs:
                        kx, ky, kconf = kpts[i]
                        if kconf >= seuil and in_zone((kx, ky, kx, ky), zone):
                            flags[crit] = True
                            break
            for crit in ("tete", "epaule", "corps", "boite"):
                resultat[f"{crit}_m{marge}_t{seuil}"] = "oui" if flags[crit] else "non"
    return resultat


def annotate_pose(frame, detections, timestamp, marge_officielle):
    out = frame.copy()
    zx1, zy1, zx2, zy2 = ZONE_MACHINE
    cv2.rectangle(out, (zx1, zy1), (zx2, zy2), (255, 255, 0), 1)
    cx1, cy1, cx2, cy2 = expand_zone(ZONE_CONVOYEUR, marge_officielle)
    cv2.rectangle(out, (int(cx1), int(cy1)), (int(cx2), int(cy2)), (255, 0, 255), 2)
    for box_conf, x1, y1, x2, y2, kpts in detections:
        cv2.rectangle(out, (int(x1), int(y1)), (int(x2), int(y2)), (0, 220, 255), 1)
        for kx, ky, kconf in kpts:
            if kconf <= 0:
                continue
            # Couleur selon la confiance : rouge (faible) -> vert (forte).
            g = int(255 * min(1.0, kconf))
            r = int(255 * (1 - min(1.0, kconf)))
            cv2.circle(out, (int(kx), int(ky)), 3, (0, g, r), -1)
    label = f"{timestamp:%H:%M:%S}  detections: {len(detections)}  (marge zone={marge_officielle}px)"
    cv2.rectangle(out, (0, 0), (out.shape[1], 24), (0, 0, 0), -1)
    cv2.putText(out, label, (6, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    return out


def charger_liste(chemin):
    if not chemin:
        return set()
    with open(chemin) as f:
        return {l.strip() for l in f if l.strip()}


def nom_attendu(timestamp):
    return f"frame_{timestamp:%H%M%S}.jpg"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--depuis-dossier", required=True)
    ap.add_argument("--modele", default=os.path.join(HERE, "yolo11n-pose.pt"))
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--diag-conf", type=float, default=DIAG_CONF,
                    help="seuil d'inference (quasi nul par defaut) : le filtrage reel se fait "
                         "ensuite en Python sur les points-cles/la boite bruts (voir docstring)")
    ap.add_argument("--marge-convoyeur-px", type=int, default=0,
                    help="marge utilisee pour les images annotees et le resume 'officiel' "
                         "(la grille complete 0/15/30px est de toute facon calculee dans le CSV)")
    ap.add_argument("--instants-presents", help="fichier .txt, un frame_HHMMSS.jpg par ligne (verite terrain : present)")
    ap.add_argument("--instants-absents", help="fichier .txt, un frame_HHMMSS.jpg par ligne (verite terrain : absent)")
    ap.add_argument("--classique-csv", help="detections.csv deja produit par eval_yolo.py (reutilise si fourni)")
    ap.add_argument("--out-dir")
    args = ap.parse_args()

    tag = os.path.basename(args.depuis_dossier.rstrip("/")) + "_pose"
    out_dir = args.out_dir or os.path.join(HERE, "out", tag)
    os.makedirs(out_dir, exist_ok=True)

    presents = charger_liste(args.instants_presents)
    absents = charger_liste(args.instants_absents)
    instants_confirmes = presents | absents

    from ultralytics import YOLO
    import torch
    device = 0 if torch.cuda.is_available() else "cpu"
    model = YOLO(args.modele)

    kp_rows = []       # keypoints_bruts.csv
    frame_rows = {}     # heure -> dict(colonnes variantes + meta)
    infer_times = []
    warmed = False
    n_images_confirmees_annotees = 0

    for timestamp, frame in frames_from_folder(os.path.abspath(args.depuis_dossier)):
        if not warmed:
            model.predict(frame, imgsz=args.imgsz, conf=args.diag_conf, device=device, verbose=False)
            warmed = True
        t0 = time.perf_counter()
        res = model.predict(frame, imgsz=args.imgsz, conf=args.diag_conf, device=device, verbose=False)[0]
        infer_times.append(time.perf_counter() - t0)

        detections = []
        n_det = len(res.boxes) if res.boxes is not None else 0
        for i in range(n_det):
            x1, y1, x2, y2 = [float(v) for v in res.boxes.xyxy[i].tolist()]
            box_conf = float(res.boxes.conf[i])
            kpts = res.keypoints.data[i].tolist() if res.keypoints is not None else [[0, 0, 0]] * 17
            detections.append((box_conf, x1, y1, x2, y2, kpts))
            for k_idx, (kx, ky, kconf) in enumerate(kpts):
                dans_zone = {m: ("oui" if in_zone((kx, ky, kx, ky), expand_zone(ZONE_CONVOYEUR, m)) else "non")
                             for m in MARGES_GRID}
                kp_rows.append([
                    f"{timestamp:%H:%M:%S}", i, f"{box_conf:.4f}",
                    f"{x1:.1f}", f"{y1:.1f}", f"{x2:.1f}", f"{y2:.1f}",
                    KEYPOINT_NAMES[k_idx], f"{kx:.1f}", f"{ky:.1f}", f"{kconf:.4f}",
                    dans_zone[0], dans_zone[15], dans_zone[30],
                ])

        variantes = evaluer_variantes(detections)
        hhmmss = f"{timestamp:%H:%M:%S}"
        frame_rows[hhmmss] = {
            "heure": hhmmss, "nb_detections": len(detections),
            "conf_max": max((d[0] for d in detections), default=0.0),
            **variantes,
        }
        print(f"[{hhmmss}] detections={len(detections)} "
              f"tete_m0_t0.1={variantes['tete_m0_t0.1']} "
              f"corps_m0_t0.1={variantes['corps_m0_t0.1']} "
              f"({infer_times[-1] * 1000:.0f} ms)", flush=True)

        nom = nom_attendu(timestamp)
        if nom in instants_confirmes and n_images_confirmees_annotees < 200:
            n_images_confirmees_annotees += 1
            suffixe = "present" if nom in presents else "absent"
            cv2.imwrite(
                os.path.join(out_dir, f"pose_{suffixe}_{timestamp:%H%M%S}.jpg"),
                annotate_pose(frame, detections, timestamp, args.marge_convoyeur_px),
                [cv2.IMWRITE_JPEG_QUALITY, 90])

    if not frame_rows:
        print("ECHEC : aucune image analysee.", flush=True)
        return

    # --- keypoints_bruts.csv ---
    with open(os.path.join(out_dir, "keypoints_bruts.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["heure", "detection_id", "box_conf", "x1", "y1", "x2", "y2",
                    "keypoint", "kx", "ky", "kconf",
                    "dans_zone_m0", "dans_zone_m15", "dans_zone_m30"])
        w.writerows(kp_rows)

    # --- detections_pose.csv ---
    colonnes_variantes = variant_columns()
    with open(os.path.join(out_dir, "detections_pose.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["heure", "nb_detections", "conf_max"] + colonnes_variantes)
        for hhmmss in sorted(frame_rows):
            r = frame_rows[hhmmss]
            w.writerow([r["heure"], r["nb_detections"], f"{r['conf_max']:.3f}"]
                        + [r[c] for c in colonnes_variantes])

    mean_ms = 1000 * sum(infer_times) / len(infer_times)
    n = len(frame_rows)
    lignes_resume = [
        f"modele            : {os.path.basename(args.modele)}",
        f"appareil          : {'GPU (CUDA)' if device == 0 else 'CPU'}",
        f"imgsz / diag-conf : {args.imgsz} / {args.diag_conf}",
        f"images analysees  : {n}",
        f"inference moyenne : {mean_ms:.0f} ms/image -> {1000 / mean_ms:.1f} FPS (modele seul)",
        f"images avec >=1 detection : {sum(1 for r in frame_rows.values() if r['nb_detections'])}/{n}",
        "",
        "Taux 'presence oui' par variante (toutes images, PAS le taux de detection reel - "
        "voir comparaison_pose_vs_classique.txt pour ca) :",
    ]
    for c in colonnes_variantes:
        taux = sum(1 for r in frame_rows.values() if r[c] == "oui") / n
        lignes_resume.append(f"  {c:20s} : {taux:.1%}")
    with open(os.path.join(out_dir, "resume_pose.txt"), "w") as f:
        f.write("\n".join(lignes_resume) + "\n")
    print("\n".join(lignes_resume[:9]), flush=True)
    print(f"... (detail complet dans {out_dir}/resume_pose.txt)", flush=True)

    # --- Comparaison avec YOLO classique, sur les instants confirmes ---
    if instants_confirmes:
        _comparer_classique(args, out_dir, frame_rows, colonnes_variantes, presents, absents)
    else:
        print("Pas d'--instants-presents/--instants-absents fournis : pas de tableau de "
              "comparaison produit (voir yolo_eval/README.md pour comment les obtenir).", flush=True)

    print(f"Resultats dans {out_dir}", flush=True)


def _classique_depuis_csv(chemin):
    """Lit un detections.csv d'eval_yolo.py (colonnes heure/presence_convoyeur,
    voir sa docstring) : reutilise sans reinferer."""
    presence = {}
    with open(chemin) as f:
        for row in csv.DictReader(f):
            presence[row["heure"]] = row.get("presence_convoyeur", "")
    return presence


def _classique_par_inference(args, heures_voulues):
    """Relance YOLO11n.pt (silhouette entiere) sur les memes frames, pour
    les seules heures demandees, si aucun detections.csv n'est fourni.
    Reutilise ZONE_CONVOYEUR/in_zone d'eval_yolo.py (import, non modifie)."""
    from ultralytics import YOLO
    import torch
    device = 0 if torch.cuda.is_available() else "cpu"
    modele_classique = os.path.join(HERE, "yolo11n.pt")
    if not os.path.exists(modele_classique):
        print(f"[classique] {modele_classique} absent : impossible de comparer sans "
              f"--classique-csv (voir README).", flush=True)
        return {}
    model = YOLO(modele_classique)
    presence = {}
    for timestamp, frame in frames_from_folder(os.path.abspath(args.depuis_dossier)):
        hhmmss = f"{timestamp:%H:%M:%S}"
        if hhmmss not in heures_voulues:
            continue
        res = model.predict(frame, imgsz=args.imgsz, conf=0.30, classes=[PERSON_CLASS],
                             device=device, verbose=False)[0]
        oui = any(in_zone([float(v) for v in b.xyxy[0].tolist()], ZONE_CONVOYEUR) for b in res.boxes)
        presence[hhmmss] = "oui" if oui else "non"
    return presence


def _comparer_classique(args, out_dir, frame_rows, colonnes_variantes, presents, absents):
    heures_presentes = {n.replace("frame_", "").replace(".jpg", "") for n in presents}
    heures_presentes = {f"{h[0:2]}:{h[2:4]}:{h[4:6]}" for h in heures_presentes}
    heures_absentes = {n.replace("frame_", "").replace(".jpg", "") for n in absents}
    heures_absentes = {f"{h[0:2]}:{h[2:4]}:{h[4:6]}" for h in heures_absentes}
    heures_voulues = heures_presentes | heures_absentes

    if args.classique_csv and os.path.exists(args.classique_csv):
        presence_classique = _classique_depuis_csv(args.classique_csv)
        source = args.classique_csv
    else:
        presence_classique = _classique_par_inference(args, heures_voulues)
        source = "inference locale (yolo11n.pt)" if presence_classique else "indisponible"

    lignes_detail = [["heure", "verite_terrain", "classique"] + colonnes_variantes]
    for heure in sorted(heures_voulues):
        if heure not in frame_rows:
            continue
        verite = "present" if heure in heures_presentes else "absent"
        classique = presence_classique.get(heure, "")
        lignes_detail.append([heure, verite, classique] + [frame_rows[heure][c] for c in colonnes_variantes])

    with open(os.path.join(out_dir, "comparaison_pose_vs_classique.csv"), "w", newline="") as f:
        csv.writer(f).writerows(lignes_detail)

    def taux(colonne_index, verite_voulue):
        lignes = [l for l in lignes_detail[1:] if l[1] == verite_voulue]
        if not lignes:
            return None
        return sum(1 for l in lignes if l[colonne_index] == "oui") / len(lignes)

    resume = [f"Source YOLO classique : {source}",
              f"Instants confirmes PRESENTS : {len(heures_presentes)}  |  ABSENTS : {len(heures_absentes)}",
              "",
              f"{'variante':22s} {'detection (presents)':>22s} {'faux positifs (absents)':>25s}"]
    resume.append(f"{'classique (boite)':22s} "
                  f"{_fmt_pct(taux(2, 'present')):>22s} {_fmt_pct(taux(2, 'absent')):>25s}")
    for idx, col in enumerate(colonnes_variantes, start=3):
        resume.append(f"{col:22s} {_fmt_pct(taux(idx, 'present')):>22s} {_fmt_pct(taux(idx, 'absent')):>25s}")

    with open(os.path.join(out_dir, "comparaison_pose_vs_classique.txt"), "w") as f:
        f.write("\n".join(resume) + "\n")
    print("\n".join(resume[:6]), flush=True)
    print(f"... (tableau complet dans {out_dir}/comparaison_pose_vs_classique.txt)", flush=True)


def _fmt_pct(v):
    return "n/a" if v is None else f"{v:.0%}"


if __name__ == "__main__":
    main()
