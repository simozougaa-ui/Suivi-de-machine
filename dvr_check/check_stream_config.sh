#!/usr/bin/env bash
# Vérification ISOLÉE, lecture seule : quel flux (mainstream/substream) le
# DVR Dahua enregistre réellement pour la caméra 15, et à quelle
# résolution/bitrate. Ne modifie AUCUN réglage du DVR, ni aucun fichier de
# production, yolo_eval/ ou signature_eval/.
#
# À lancer depuis le Jetson (seul endroit avec accès réseau au DVR via
# Tailscale) : bash dvr_check/check_stream_config.sh
#
# Nécessite les identifiants DVR déjà utilisés en production (.env à la
# racine du dépôt, jamais commité) : DVR_IP, DVR_USER, DVR_PASSWORD.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a; source .env; set +a
fi
: "${DVR_IP:?DVR_IP manquant (voir .env.example)}"
: "${DVR_USER:?DVR_USER manquant}"
: "${DVR_PASSWORD:?DVR_PASSWORD manquant}"
: "${CAMERA_CHANNEL:?CAMERA_CHANNEL manquant}"

# Le CGI Dahua indexe les canaux à partir de 0 : canal DMSS 15 -> index 14.
INDEX=$((CAMERA_CHANNEL - 1))
BASE="http://${DVR_IP}"
AUTH=(--digest -u "${DVR_USER}:${DVR_PASSWORD}")

echo "=== Profils d'encodage (résolution/bitrate configurés pour chaque flux) ==="
echo "Canal DMSS ${CAMERA_CHANNEL} -> index CGI ${INDEX}"
curl -sS "${AUTH[@]}" "${BASE}/cgi-bin/configManager.cgi?action=getConfig&name=Encode" \
  | grep -E "^table\.Encode\[${INDEX}\]\.(MainFormat\[0\]|ExtraFormat\[0\])\.Video\.(Width|Height|BitRate|FPS|Compression)="
echo
echo "MainFormat[0] = flux principal (mainstream, HD)."
echo "ExtraFormat[0] = premier flux secondaire (substream, SD)."
echo

echo "=== Réglage du flux réellement enregistré (planning d'enregistrement) ==="
echo "Le CGI de planning varie selon le firmware ; si la commande ci-dessous"
echo "ne renvoie rien d'exploitable, vérifier via l'interface web (chemin"
echo "donné dans dvr_check/README.md)."
curl -sS "${AUTH[@]}" "${BASE}/cgi-bin/configManager.cgi?action=getConfig&name=RecordMode" || true
echo
curl -sS "${AUTH[@]}" "${BASE}/cgi-bin/configManager.cgi?action=getConfig&name=Record" \
  | grep -E "^table\.Record\[${INDEX}\]\." || true
