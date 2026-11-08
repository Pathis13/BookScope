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

Les paramètres partagés (connexion MongoDB, chemins, sélection des utilisateurs, seuils…) sont dans [scripts/config.py](scripts/config.py).


## F1 : livres similaires

```bash
python f1.py 1                     # par book_id
python f1.py "The Hunger Games"    # par titre exact (title ou original_title)
```

Si plusieurs livres ont le même titre, le script les liste avec leur `book_id` pour relancer la recherche.
Un argument composé uniquement de chiffres est toujours interprété comme un `book_id`.


## F2



## F3 : recommandations pour un lecteur

```bash
python f3.py 20000                 # recommandations pour le lecteur 20000
python f3.py 20000 --limit 20      # nombre de recommandations (10 par défaut)
python f3.py 20000 --truncate 5    # historique réduit aux 5 premières notes du lecteur
```

Les 10 livres les mieux notés du lecteur (note ≥ 4) sont envoyés à F1 ; chaque livre similaire non noté gagne +1 à chaque apparition.
Chaque recommandation indique les livres du lecteur qui l'ont fait apparaître, et la sortie se termine par l'écart à la popularité (part dans le top 100 des livres les plus notés, popularité médiane).


## Mesures (Part I)

```bash
python bench.py                    # index d'origine vs index couvrants sur ratings, 5 répétitions
python bench.py --reps 10 --keep base
```

Résultats dans `results/` ; analyse dans [docs/LocalPrototype.md](docs/LocalPrototype.md).


## Déploiement distribué (Part II)

Cluster MongoDB local construit comme dans le Lab 5 : serveur de configuration (27200), 2 shards (27301, 27302), routeur mongos (27400). Les notes sont réparties par lecteur (`{user_id: "hashed"}`).

```bash
python cluster.py start                                   # lance et initialise le cluster
python import_data.py --uri mongodb://localhost:27400/    # import réparti
python compare.py                                         # résultats identiques local / cluster ?
python dist_explain.py                                    # shards contactés pour 3 types de requêtes
python cluster.py stop-shard 2                            # perte d'une instance (start-shard 2 pour relancer)
python cluster.py stop
```

Pour lancer un service sur le cluster : `BOOKSCOPE_URI=mongodb://localhost:27400/ python f2.py 20000 22534` (PowerShell : `$env:BOOKSCOPE_URI = "mongodb://localhost:27400/"`). Détails et mesures dans [docs/DistributedPrototype.md](docs/DistributedPrototype.md).


## Info des données

ratings : 
- garde les utilsateurs de 20k à 30k
- pour chaque utilisateur 80% des premeières notes sont dans initial, 10% d'apres dans F6 et le restant dans E3

to_read : garde les utilsateurs de 20k à 30k