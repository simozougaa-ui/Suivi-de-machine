# Vérification du flux d'enregistrement DVR (caméra 15)

Vérification **isolée, lecture seule** : le DVR Dahua enregistre-t-il en
continu la caméra 15 en **mainstream** (HD) ou **substream** (SD) ? Ce
réglage se fait dans la configuration du DVR (planning d'enregistrement),
indépendamment du flux demandé en lecture (le code du projet demande
toujours `subtype=0`, le flux principal, pour le direct comme pour la
lecture des enregistrements — voir `src/camera_stream.py`). **Aucun
réglage du DVR n'est modifié par ces scripts.**

## À lancer depuis le Jetson (seul accès réseau au DVR)

```bash
cd ~/suivi-de-machine && git pull
bash dvr_check/check_stream_config.sh
```

Affiche la résolution/bitrate configurés pour `MainFormat[0]` (mainstream)
et `ExtraFormat[0]` (substream), puis tente d'afficher le flux
effectivement programmé pour l'enregistrement (le CGI exact varie selon
le firmware — si la deuxième partie ne renvoie rien d'exploitable, vérifier
à la main dans l'interface web du DVR : **Réglages → Stockage → Planning
d'enregistrement (Record)**, sélectionner la caméra 15 et regarder la
colonne/le menu déroulant "Flux d'enregistrement" ou "Record Stream" —
valeurs possibles : Principal/Main, Secondaire/Sub, ou les deux).

## Résolution réelle d'un enregistrement déjà téléchargé

```bash
# Sur une image déjà générée par le projet (ex. la référence de production) :
python3 dvr_check/check_recording_resolution.py --image reference_fragments.png

# Ou en lisant directement quelques secondes d'enregistrement :
python3 dvr_check/check_recording_resolution.py --date 2026-09-23 --debut 13:21:00 --duree 5
```

Comparer le résultat aux valeurs `MainFormat[0].Video.Width/Height` et
`ExtraFormat[0].Video.Width/Height` obtenues ci-dessus pour savoir laquelle
correspond.

## Si un changement est souhaité (mainstream non utilisé pour l'enregistrement)

**Ne rien changer sans validation explicite de Mohamed.** Si besoin, le
réglage se change dans l'interface web du DVR : **Réglages → Stockage →
Planning d'enregistrement**, sélectionner la caméra 15, changer le flux
programmé de "Secondaire/Sub" vers "Principal/Main" pour les plages
horaires concernées, puis appliquer/enregistrer. Impact à anticiper avant
de valider : le flux principal est nettement plus volumineux (HD vs SD),
donc plus d'espace disque utilisé et une durée de rétention des
enregistrements réduite sur le DVR.

## Résultats de cette vérification

Voir `NOTES-SESSION.md`, section « Vérification du flux d'enregistrement
DVR ». Cette vérification nécessite un accès réseau au DVR (Tailscale),
indisponible depuis l'environnement de développement : les résultats
concrets (mainstream ou substream, résolution) doivent être renvoyés
après exécution des commandes ci-dessus depuis le Jetson.
