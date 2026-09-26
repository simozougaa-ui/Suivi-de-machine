#!/usr/bin/env bash
# Vérification ISOLÉE, lecture seule : quelles plages horaires sont encore
# disponibles sur le DVR pour la caméra 15 (rétention 17 jours, confirmée
# le 2026-09-26). Ne modifie aucun réglage du DVR, ni aucun fichier de
# production/yolo_eval/signature_eval.
#
# À lancer depuis le Jetson : bash dvr_check/list_recordings.sh AAAA-MM-JJ
#
# Nécessite les identifiants DVR déjà utilisés en production (.env à la
# racine du dépôt, jamais commité).
set -euo pipefail
cd "$(dirname "$0")/.."

DAY="${1:?Usage: list_recordings.sh AAAA-MM-JJ}"

if [ -f .env ]; then
  set -a; source .env; set +a
fi
: "${DVR_IP:?DVR_IP manquant (voir .env.example)}"
: "${DVR_USER:?DVR_USER manquant}"
: "${DVR_PASSWORD:?DVR_PASSWORD manquant}"
: "${CAMERA_CHANNEL:?CAMERA_CHANNEL manquant}"

INDEX=$((CAMERA_CHANNEL - 1))
BASE="http://${DVR_IP}"
AUTH=(--digest -u "${DVR_USER}:${DVR_PASSWORD}")
JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT

echo "=== Enregistrements caméra ${CAMERA_CHANNEL} (index ${INDEX}) pour le ${DAY} ==="

# Séquence CGI Dahua mediaFileFind : create -> findFile -> close.
OBJ=$(curl -sS -c "$JAR" -b "$JAR" "${AUTH[@]}" \
  "${BASE}/cgi-bin/mediaFileFind.cgi?action=factory.create" \
  | sed -n 's/.*=\s*\([0-9]\+\).*/\1/p')
if [ -z "$OBJ" ]; then
  echo "Impossible de créer l'objet de recherche (vérifier identifiants/IP)."
  exit 1
fi

curl -sS -c "$JAR" -b "$JAR" "${AUTH[@]}" \
  "${BASE}/cgi-bin/mediaFileFind.cgi?action=findFile&object=${OBJ}&condition.Channel=${INDEX}&condition.StartTime=${DAY}%2000:00:00&condition.EndTime=${DAY}%2023:59:59&condition.Types[0]=dav" \
  > /dev/null

curl -sS -c "$JAR" -b "$JAR" "${AUTH[@]}" \
  "${BASE}/cgi-bin/mediaFileFind.cgi?action=findNextFile&object=${OBJ}&count=100" \
  | grep -E "StartTime=|EndTime=|Length="

curl -sS -c "$JAR" -b "$JAR" "${AUTH[@]}" \
  "${BASE}/cgi-bin/mediaFileFind.cgi?action=close&object=${OBJ}" > /dev/null
curl -sS -c "$JAR" -b "$JAR" "${AUTH[@]}" \
  "${BASE}/cgi-bin/mediaFileFind.cgi?action=destroy&object=${OBJ}" > /dev/null

echo
echo "Si la liste est vide : aucun enregistrement pour ce jour (hors rétention"
echo "ou disque plein/écrasé). Rappel : rétention 17 jours -> le 2026-09-05"
echo "(21 jours avant le 2026-09-26) est très probablement déjà écrasé ;"
echo "utiliser une plage plus récente (ex. 2026-09-23, dans la fenêtre)."
