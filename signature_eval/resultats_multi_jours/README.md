# Validation multi-jours de la signature de couleur — EN PRÉPARATION (2026-09-27)

Objectif : vérifier que la méthode de signature de couleur (validée sur
un seul jour, 2026-09-23, voir `NOTES-SESSION.md`) reste fiable sur
plusieurs jours différents (éclairage variable, tenue du conducteur qui
change). **Statut : outillage prêt, aucune extraction encore lancée**
(pas d'accès réseau au DVR/Jetson depuis cet environnement de
développement, comme pour toutes les étapes précédentes qui en
dépendaient).

## Jours retenus (proposition, à confirmer par `dvr_check/list_recordings.sh`)

La rétention du DVR est d'environ 17 jours ; aujourd'hui (2026-09-27),
la fenêtre disponible va donc approximativement du **2026-09-10** au
2026-09-27. Le 2026-09-23 (mercredi) est déjà testé. Pour maximiser la
diversité (jours de semaine différents, étalés dans la fenêtre plutôt
que regroupés) tout en restant loin du bord de rétention le plus
incertain (2026-09-10/11), 3 jours ouvrés sont proposés :

| Date | Jour | Ancienneté | Pourquoi ce choix |
|---|---|---|---|
| **2026-09-15** | mardi | 12 jours | milieu de fenêtre, large marge de sécurité vs rétention |
| **2026-09-21** | lundi | 6 jours | jour de semaine différent des deux autres, éclairage/saison proche de fin septembre |
| **2026-09-25** | vendredi | 2 jours | le plus récent, quasi certainement disponible, jour de semaine différent |

**Ces dates ne sont PAS vérifiées** (pas d'accès DVR ici) — à confirmer
en premier avec :

```bash
cd ~/suivi-de-machine && git pull
bash dvr_check/list_recordings.sh 2026-09-15
bash dvr_check/list_recordings.sh 2026-09-21
bash dvr_check/list_recordings.sh 2026-09-25
```

Si l'une est indisponible (déjà écrasée, ou pas d'enregistrement ce
jour-là — jour férié, arrêt machine...), la remplacer par le jour ouvré
le plus proche encore disponible.

## Plage horaire retenue

**13:15:00, durée 1800 s (30 min)** pour les 3 jours — même heure de la
journée que le test déjà validé (2026-09-23, 13:21-13:45), pour comparer
dans des conditions d'éclairage proches (même moment de l'après-midi) ;
élargie à 30 min (au lieu de 24 min) pour augmenter les chances de
capturer plusieurs passages au convoyeur. **Hypothèse non vérifiée** : que
le conducteur travaille bien sur cette machine à cette heure-là les autres
jours aussi (le 23/09 c'était le cas). Si `test_fragments_on_recording.py`
(étape 1 de `prepare_day.sh`) ne montre aucune session présente sur cette
plage pour un jour donné, essayer une autre heure (ex. le matin) avant de
conclure à une indisponibilité.

## Marche à suivre (sur le Jetson)

```bash
cd ~/suivi-de-machine && git pull
bash signature_eval/prepare_day.sh 2026-09-15 13:15:00 1800
bash signature_eval/prepare_day.sh 2026-09-21 13:15:00 1800
bash signature_eval/prepare_day.sh 2026-09-25 13:15:00 1800
```

Chaque appel prépare tout (instants convoyeur, frames, détection YOLO,
candidats de signature, référence "machine vide") pour un jour, dans
`signature_eval/resultats_multi_jours/<date>/`, **sans lancer
`signature.py`** — il s'arrête avant, et affiche :
- où sont les images à vérifier visuellement (`<date>/a_verifier/`) ;
- la commande pour les voir depuis le téléphone (`python3 -m http.server
  8001 --directory <date>/a_verifier`, puis `http://100.116.160.30:8001/`) ;
- la commande exacte de `signature.py` à lancer **une fois la
  vérification faite** (prête à copier-coller, non exécutée par le
  script).

## Vérification humaine requise (obligatoire avant de continuer)

Pour **chaque jour**, ouvrir `<date>/a_verifier/candidat_*.jpg` et
confirmer que c'est bien le conducteur suivi par ce projet (pas un autre
ouvrier ni un passant). Retirer du fichier `<date>/candidats_signature.csv`
toute ligne douteuse **avant** de lancer `signature.py`. Voir aussi
`<date>/convoyeur_instants.txt` (instants où la zone convoyeur a été
comptée par la détection de production existante) : un coup d'œil rapide
aux images correspondantes est recommandé mais moins critique (cette
partie réutilise une détection déjà validée, elle ne dépend pas d'une
identification de personne).

## Suivi (à cocher au fur et à mesure)

- [ ] 2026-09-15 : disponibilité DVR vérifiée
- [ ] 2026-09-15 : préparation lancée (`prepare_day.sh`)
- [ ] 2026-09-15 : candidats vérifiés visuellement par Mohamed
- [ ] 2026-09-15 : `signature.py` lancé, résultats copiés ici
- [ ] 2026-09-21 : disponibilité DVR vérifiée
- [ ] 2026-09-21 : préparation lancée (`prepare_day.sh`)
- [ ] 2026-09-21 : candidats vérifiés visuellement par Mohamed
- [ ] 2026-09-21 : `signature.py` lancé, résultats copiés ici
- [ ] 2026-09-25 : disponibilité DVR vérifiée
- [ ] 2026-09-25 : préparation lancée (`prepare_day.sh`)
- [ ] 2026-09-25 : candidats vérifiés visuellement par Mohamed
- [ ] 2026-09-25 : `signature.py` lancé, résultats copiés ici
- [ ] Synthèse des 3 jours + conclusion sur la stabilité, documentée dans
      `NOTES-SESSION.md`
