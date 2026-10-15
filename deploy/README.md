# Installation sur le Jetson

Copier les deux fichiers `.service` vers `/etc/systemd/system/`, puis :

```bash
sudo systemctl daemon-reload
sudo systemctl enable suivi-presence suivi-dashboard
sudo systemctl start suivi-presence suivi-dashboard
```

`suivi-presence` tourne en arrière-plan (détection + enregistrement dans
`sessions.csv`). `suivi-dashboard` sert la page web sur le port 8000,
accessible depuis n'importe quel appareil sur le même réseau Tailscale
(`http://<ip-tailscale-du-jetson>:8000`).

Accès à distance (hors réseau local) : Tailscale doit être installé et
connecté (`sudo tailscale up`) — voir la documentation Tailscale pour
l'authentification.

## Remise à jour quotidienne de la détection marche/arrêt (8 h)

`suivi-machine-rattrapage.timer` + `suivi-machine-rattrapage.service` :
chaque jour à 08:00, saute le retard accumulé par
`suivi-machine-etat` (voir NOTES-SESSION.md, « Remise à jour quotidienne à
8 h », pour l'installation en une ligne et le fonctionnement).

## Redémarrage du DVR (chaque dimanche 06:30, ou à la main)

`suivi-dvr-redemarrage.timer` + `suivi-dvr-redemarrage.service` : arrête la
détection, redémarre le DVR, vérifie qu'un téléchargement passe, puis relance
la détection. Voir NOTES-SESSION.md, « DVR figé la nuit du 14/10 », pour
l'installation en une ligne.

En plus, depuis le 15/10, la détection redémarre elle-même le DVR si le
téléchargement HTTP se fige deux fois de suite (au plus 1 fois par heure et
4 fois par jour ; `DVR_REDEMARRAGE_AUTO=0` dans `.env` pour désactiver).
Voir NOTES-SESSION.md, « 2e blocage le 15/10 ».
