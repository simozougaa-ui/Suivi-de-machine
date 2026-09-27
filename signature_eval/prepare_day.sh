#!/usr/bin/env bash
# Prépare UNE journée pour la validation multi-jours de la signature de
# couleur (voir NOTES-SESSION.md, section "Validation multi-jours").
# N'exécute PAS signature.py : s'arrête juste avant, en attente de la
# vérification VISUELLE humaine des candidats (voir la fin du script).
#
# Isolé : ne modifie aucun fichier de production (main.py, src/,
# contact_sheet.py, dashboard.py), ni yolo_eval/eval_yolo.py,
# signature_eval/signature.py, signature_eval/build_reference.py.
# Réutilise test_fragments_on_recording.py tel quel (script de test déjà
# existant, en lecture seule vis-à-vis de la production - il ne fait que
# lire l'enregistrement et afficher un journal, comme documenté dans son
# propre en-tête).
#
# Usage (depuis la racine du dépôt, sur le Jetson) :
#   bash signature_eval/prepare_day.sh 2026-09-15 13:15:00 1800
set -euo pipefail
cd "$(dirname "$0")/.."

DATE="${1:?Usage: prepare_day.sh AAAA-MM-JJ [HH:MM:SS] [duree_secondes]}"
DEBUT="${2:-13:15:00}"
DUREE="${3:-1800}"
COMPACT="${DEBUT//:/}"
TAG="${DATE}_${COMPACT}"

OUT="signature_eval/resultats_multi_jours/${DATE}"
FRAMES_DIR="signature_eval/frames_dvr_${TAG}"   # gitignore: signature_eval/frames_dvr_*/
# eval_yolo.py --depuis-dossier nomme son dossier de sortie d'apres le
# basename EXACT de FRAMES_DIR (donc avec le prefixe "frames_dvr_") :
YOLO_TAG="$(basename "$FRAMES_DIR")"
mkdir -p "$OUT"

echo "=== [$DATE] 1/5 : instants convoyeur (test_fragments_on_recording.py, reutilise tel quel) ==="
rm -f sessions_test_fragments.csv   # evite d'accumuler avec un run precedent (le script APPEND)
.venv/bin/python3 test_fragments_on_recording.py --date "$DATE" --debut "$DEBUT" --duree "$DUREE" \
  | tee "$OUT/fragments_log.txt"
[ -f sessions_test_fragments.csv ] && mv sessions_test_fragments.csv "$OUT/sessions_fragments.csv" || true
python3 signature_eval/extract_convoyeur_instants.py \
  --log "$OUT/fragments_log.txt" --sortie "$OUT/convoyeur_instants.txt" \
  --motif-nom "frame_{hhmmss}.jpg"

echo "=== [$DATE] 2/5 : extraction des frames brutes depuis le flux DVR ==="
python3 signature_eval/extract_frames_dvr.py \
  --date "$DATE" --debut "$DEBUT" --duree "$DUREE" --pas 1 --out-dir "$FRAMES_DIR"

echo "=== [$DATE] 3/5 : detection YOLO (eval_yolo.py, non modifie) sur ces frames ==="
yolo_eval/.venv/bin/python yolo_eval/eval_yolo.py --depuis-dossier "$FRAMES_DIR" --controle 60 --moments ""
# --moments "" : le defaut d'eval_yolo.py est calé sur les heures du
# 2026-09-23 (13:21:07 etc.), sans rapport avec les autres jours ici.
cp "yolo_eval/out/${YOLO_TAG}/resume.txt" "$OUT/resume_yolo.txt"

echo "=== [$DATE] 4/5 : selection automatique des candidats 'conducteur ailleurs' ==="
python3 signature_eval/select_candidates.py \
  --detections "yolo_eval/out/${YOLO_TAG}/detections.csv" \
  --n 8 --sortie "$OUT/candidats_signature.csv" --motif-nom "frame_{hhmmss}.jpg"

echo "=== [$DATE] 5/5 : reference 'machine vide' (build_reference.py, non modifie) ==="
python3 signature_eval/build_reference.py \
  --frames-dir "$FRAMES_DIR" --sortie "$OUT/reference.png" --lot 100

# Copie legere des images a verifier visuellement (candidats + quelques
# instants convoyeur), pour que la revue puisse aussi se faire depuis le
# depot si besoin (en plus du serveur http ci-dessous).
mkdir -p "$OUT/a_verifier"
while IFS=, read -r nom x1 y1 x2 y2; do
  hhmmss="${nom#frame_}"; hhmmss="${hhmmss%.jpg}"
  src="yolo_eval/out/${YOLO_TAG}/img_${hhmmss}.jpg"
  [ -f "$src" ] && cp "$src" "$OUT/a_verifier/candidat_${hhmmss}.jpg"
done < "$OUT/candidats_signature.csv"

echo
echo "############################################################"
echo "[$DATE] PREPARATION TERMINEE - NE PAS LANCER signature.py"
echo "############################################################"
echo "A verifier VISUELLEMENT par Mohamed avant de continuer :"
echo "  - Les candidats 'conducteur ailleurs' : $OUT/a_verifier/candidat_*.jpg"
echo "    (issus de $OUT/candidats_signature.csv)"
echo "  - Quelques instants convoyeur : voir $OUT/convoyeur_instants.txt,"
echo "    images correspondantes dans yolo_eval/out/${YOLO_TAG}/ (img_HHMMSS.jpg"
echo "    si sauvegardees, sinon regarder frame_HHMMSS.jpg dans $FRAMES_DIR)"
echo
echo "Pour visualiser depuis le telephone (Tailscale) :"
echo "  python3 -m http.server 8001 --directory ${OUT}/a_verifier"
echo "  puis http://100.116.160.30:8001/"
echo
echo "Une fois CHAQUE candidat confirme comme etant bien le conducteur suivi"
echo "(pas un autre ouvrier/passant), retirer du fichier $OUT/candidats_signature.csv"
echo "toute ligne douteuse, puis lancer (commande prete, NON executee ici) :"
echo
echo "  python3 signature_eval/signature.py \\"
echo "    --frames-dir $FRAMES_DIR \\"
echo "    --reference $OUT/reference.png \\"
echo "    --signature-manifest $OUT/candidats_signature.csv \\"
echo "    --occluded-list $OUT/convoyeur_instants.txt \\"
echo "    --out-dir $OUT/out_signature"
