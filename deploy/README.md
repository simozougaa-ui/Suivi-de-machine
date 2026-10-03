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
