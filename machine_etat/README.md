# Détection marche / arrêt de la machine

`detecter_marche_arret.py` classe chaque minute en **marche** ou **arrêt** à partir
des enregistrements du DVR, et dépose le résultat dans l'application de suivi de
production (dépôt `suivi-production-imprimerie`), page « État machine (caméra) »,
réservée à l'administrateur.

## La méthode

Deux images consécutives espacées d'une seconde sont comparées dans une zone fixe :

| Réglage | Valeur |
|---|---|
| Zone (frame 1280×720) | x1=320, y1=200, x2=400, y2=240 |
| Seuil de différence d'un pixel | 12 |
| Fraction de pixels changés → « seconde avec mouvement » | 0,015 |
| Part de secondes avec mouvement → « minute en marche » | 40 % |

Ces valeurs sont **validées sur des plages réelles** (voir `NOTES-SESSION.md`) :
15/09 15:00–15:05 en marche à 86 %, 15/09 18:09–18:21 à l'arrêt à 0 %, 28/09
17:24–18:07 à l'arrêt à 0 % avec deux pics courts (13 % et 20 %) correctement
restés sous le seuil — de simples passages du conducteur, vérifiés à l'image.
**Ne pas les changer sans refaire cette validation.**

## Jamais le flux direct

Le calcul sur le flux live donne des faux positifs (bug non résolu). On relit donc
les **enregistrements** en RTSP playback, avec un retard volontaire : à l'instant T
on analyse la fenêtre `[T-10min, T-5min]`, ce qui laisse au DVR le temps d'avoir
fini d'écrire. Les résultats affichés ont donc **5 à 10 minutes de retard** — c'est
le prix d'une mesure fiable.

## Aucune image sur le disque

Les frames sont décodées, comparées et jetées au fil de la lecture : rien ne
s'accumule, et un script tué en cours de route ne laisse aucun fichier derrière
lui. `--garder-frames DOSSIER` force l'écriture, uniquement pour inspecter un cas
douteux à la main.

## Une minute absente n'est pas un arrêt

Une minute dont moins de 30 secondes ont pu être lues n'est **pas** envoyée : avec
5 secondes analysées, « 40 % de mouvement » ne veut rien dire, et une coupure de
flux passerait pour un arrêt. L'application affiche ces minutes en gris
(« non mesuré ») et les compte à part, jamais avec les arrêts.

## Envoi et reprise après coupure

POST `/api/machine-etat` sur l'application, authentifié par un jeton porteur
(`SUIVI_API_JETON`). Le Jetson n'a **pas** les identifiants de la base : le jeton
ne permet que d'écrire des minutes d'état, et se révoque en changeant une variable
d'environnement côté application.

Si l'envoi échoue, le lot part dans `machine_etat/file_attente/` et est rejoué au
tour suivant (l'API est idempotente : un renvoi remplace, ne duplique pas). La file
garde au plus 24 h de lots, les plus récents d'abord.

## Installation sur le Jetson

```bash
sudo cp deploy/suivi-machine-etat.service deploy/suivi-machine-etat.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now suivi-machine-etat.timer
systemctl list-timers suivi-machine-etat.timer      # prochain déclenchement
journalctl -u suivi-machine-etat.service -n 50      # dernières mesures
```

Renseigner d'abord `SUIVI_API_URL`, `SUIVI_API_JETON` et `MACHINE_ETAT_ID` dans
`.env` (voir `.env.example`).

## Vérifier une plage passée

```bash
python3 machine_etat/detecter_marche_arret.py \
    --date 2026-09-28 --debut 17:24 --fin 18:07 --sans-envoi
```
