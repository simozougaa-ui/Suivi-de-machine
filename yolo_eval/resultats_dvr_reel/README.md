# Résultats YOLO sur flux DVR réel — EN ATTENTE (2026-09-26)

Objectif : refaire le test YOLO (voir `../README.md` et
`NOTES-SESSION.md`) sur des images extraites **directement du flux
mainstream réel du DVR** (RTSP), et non plus sur les 57 captures d'écran
DMSS (doublement compressées) utilisées jusqu'ici. Comparer le taux de
détection au convoyeur (0/15 sur DMSS) et la qualité générale.

**Statut : n'a pas pu être exécuté** — l'environnement de développement
n'a pas d'accès réseau au DVR/Jetson (Tailscale injoignable, revérifié le
2026-09-26). Voir `NOTES-SESSION.md`, section « YOLO et signature sur
flux DVR réel », pour le détail et le raisonnement sur le choix de plage
horaire (le 2026-09-05 11:48-12:00 est très probablement déjà écrasé,
21 jours > 17 jours de rétention — utiliser plutôt le 2026-09-23).

## À lancer depuis le Jetson

```bash
cd ~/suivi-de-machine && git pull

# 1. Confirmer la plage disponible (rétention 17 jours)
bash dvr_check/list_recordings.sh 2026-09-05   # pour verifier (tres probablement vide)
bash dvr_check/list_recordings.sh 2026-09-23   # plage de repli, deja testee avec yolo_eval

# 2. Lancer eval_yolo.py DIRECTEMENT sur le flux DVR (pas besoin d'etape
#    d'extraction separee : le script lit deja le flux RTSP mainstream)
yolo_eval/.venv/bin/python yolo_eval/eval_yolo.py \
    --date 2026-09-23 --debut 13:21:00 --duree 1500 \
    --moments "13:21:07,13:21:10,13:21:13,13:21:16"

# 3. Copier les resultats pertinents ici (sans ecraser resultats_2026-09-26/)
cp yolo_eval/out/2026-09-23_132100/resume.txt yolo_eval/resultats_dvr_reel/
cp yolo_eval/out/2026-09-23_132100/img_132107.jpg yolo_eval/resultats_dvr_reel/  # etc.
```

Comparer `resume.txt` (taux de détection, moments clés OUI/non) à celui
déjà obtenu sur les captures DMSS (`NOTES-SESSION.md`, section
« Évaluation YOLO isolée ») pour voir si la meilleure qualité source
change le résultat.
