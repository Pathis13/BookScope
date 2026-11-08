# BookScope
- ANDRÉ Mathis
- VIALE Jean-Jacques

## Initialisation des données

- Télécharger [goodbooks-10k](https://github.com/zygmuntz/goodbooks-10k/releases) et l'extraire dans data/


```bash
python -m pip install -r requirements.txt

cd scripts
python prepare_data.py
python import_data.py
```

Vérifier dans MongoDB Compass que la base de données bookscope est dans la connexion `mongodb://localhost:27017/`

- `prepare_data.py` vérifie les notes (valeurs manquantes, notes hors de 1 à 5, doublons), découpe l'historique de chaque lecteur et supprime les 8 doublons de `book_tags.csv` (`data/processed/book_tags_filtered.csv`).
- `import_data.py` **supprime les collections** avant d'importer, crée les index (dont les deux index couvrants retenus en Part I), puis importe. Relancer l'import remet donc la base à son état initial et efface les notes ajoutées par F6.

Les paramètres partagés (connexion MongoDB, chemins, sélection des utilisateurs, seuils…) sont dans [scripts/config.py](scripts/config.py).


## F1 : livres similaires

```bash
python f1.py 1                     # par book_id
python f1.py "The Hunger Games"    # par titre exact (title ou original_title)
python f1.py 1 --limit 20          # nombre de livres liés (10 par défaut)
python f1.py 1 --trace 2           # preuves du lien entre le livre 1 et le livre 2
```

Si plusieurs livres ont le même titre, le script les liste avec leur `book_id` pour relancer la recherche.
Un argument composé uniquement de chiffres est toujours interprété comme un `book_id`.


## F2 : similarité entre deux lecteurs

```bash
python f2.py 20000 20028           # comparer les similarités entre l'utilisateur 20000 et 20028
```

Les livres en commun ont leurs notes comparées pour définir un score de similarité des notes.

Les livres notés par les utilisateurs sont placés dans des genres en fonction de leurs tags, ce qui permet de donner un score et de calculer la similarité par genre.


## F3 : recommandations pour un lecteur

```bash
python f3.py 20000                 # recommandations pour le lecteur 20000
python f3.py 20000 --limit 20      # nombre de recommandations (10 par défaut)
python f3.py 20000 --truncate 5    # historique réduit aux 5 premières notes du lecteur
```

Les 10 livres les mieux notés du lecteur (note ≥ 4) sont envoyés à F1 ; chaque livre similaire non noté gagne +1 à chaque apparition.
Chaque recommandation indique les livres du lecteur qui l'ont fait apparaître, et la sortie se termine par l'écart à la popularité (part dans le top 100 des livres les plus notés, popularité médiane).


## F4 : connexion entre deux livres

```bash
python f4.py 1 2                   # par book_id
python f4.py "Twilight (Twilight, #1)" 2
python f4.py 1 2 --limit 10        # nombre de chemins
python f4.py 1 2 --max-steps 2     # chemins à 2 étapes au plus (3 par défaut)
```

Chemins A → X → B (ou A → X → Y → B) dont chaque étape est un lectorat partagé : assez de lecteurs ont aimé les deux livres, nettement plus que le hasard. Les livres « carrefours » sont interdits comme intermédiaires, et chaque étape est prouvée par des lecteurs.


## F5 : lien entre deux centres d'intérêt

```bash
python f5.py 11305 26138           # par tag_id (fantasy / romance)
python f5.py fantasy romance       # par nom exact du tag
python f5.py fantasy romance --limit 5
```

Compare les groupes de lecteurs des deux tags au hasard (lift), donne les livres « pont » aimés des deux côtés, puis recalcule le tout sans les livres très populaires ni les lecteurs très actifs pour voir si le lien tient.


## F6 : mise à jour par lot

```bash
python f6.py                       # applique data/processed/ratings_update_F6.csv
```

Les notes du lot sont ajoutées ; une note déjà présente pour le même (lecteur, livre) est remplacée. Le script affiche le nombre de notes insérées, modifiées et identiques : un second passage ne change rien.
Lancer `prepare_data.py` et `import_data.py` avant.


## Mesures (Part I)

```bash
python bench.py                    # index d'origine vs index couvrants sur ratings, 5 répétitions
python bench.py --reps 10 --keep base
```

Résultats dans `results/` ; analyse dans [docs/LocalPrototype.md](docs/LocalPrototype.md).


## Déploiement distribué (Part II)

Cluster MongoDB local construit comme dans le Lab 5 : serveur de configuration (27200), 2 shards (27301, 27302), routeur mongos (27400). Les notes sont réparties par lecteur (`{user_id: "hashed"}`) ; une base alternative les répartit par livre.

```bash
python cluster.py start                                   # lance et initialise le cluster
python import_data.py --uri mongodb://localhost:27400/    # import réparti par lecteur (base bookscope)
python import_data.py --uri mongodb://localhost:27400/ --db bookscope_book --shard-key book_id   # par livre
python cluster.py status                                  # processus, shards, documents par shard
python compare.py                                         # résultats identiques local / cluster ?
python dist_explain.py                                    # shards contactés pour 3 types de requêtes
python cluster.py stop-shard 2                            # perte d'une instance (start-shard 2 pour relancer)
python cluster.py stop                                    # arrêt (reset : arrêt + suppression des données)
```

Pour lancer un service sur le cluster : `BOOKSCOPE_URI=mongodb://localhost:27400/ python f2.py 20000 22534` (PowerShell : `$env:BOOKSCOPE_URI = "mongodb://localhost:27400/"`). `BOOKSCOPE_DB=bookscope_book` choisit la base répartie par livre. Détails et mesures dans [docs/DistributedPrototype.md](docs/DistributedPrototype.md).

Si une instance utile est arrêtée, F2, F3 et F6 échouent explicitement (code de retour 2) au lieu de donner ou d'écrire un résultat partiel.

`compare.py` suppose que les trois bases ont été chargées à partir des mêmes fichiers : après une modification de `prepare_data.py` ou de `import_data.py`, ou après F6 sur une seule base, réimporter les trois.


## Documentation

- [docs/Requetes.md](docs/Requetes.md) : conception des services F1 à F6
- [docs/LocalPrototype.md](docs/LocalPrototype.md) : Part I, modèle, index et mesures
- [docs/DistributedPrototype.md](docs/DistributedPrototype.md) : Part II, cluster, placement, perte d'une instance
- [docs/Pieges.md](docs/Pieges.md) : erreurs rencontrées et corrections


## Info des données

ratings :
- garde les utilisateurs de 20k à 30k
- pour chaque utilisateur, les 80 % premières notes sont dans la base initiale, les 10 % suivantes dans le lot F6 et le reste dans E3

to_read : garde les utilisateurs de 20k à 30k

book_tags : 8 doublons (goodreads_book_id, tag_id) supprimés, la dernière occurrence est gardée
