# Pièges rencontrés et corrections

Ce document recense les erreurs rencontrées pendant le développement de BookScope (Part I, Part II, merge de F6). Pour chacune : ce qu'on observe, la cause, la correction appliquée, et la façon de repérer le problème la prochaine fois.

La plupart de ces erreurs **ne provoquent aucun message d'erreur**. Le script s'exécute normalement mais le résultat est faux, incomplet ou lent. Elles n'ont été trouvées que par une mesure (`explain`, `bench.py`) ou par une comparaison (`compare.py`).

| # | Piège | Conséquence | État |
|---|---|---|---|
| 1 | `_id` présent dans la projection | Index couvrant inutilisé | Corrigé (f1, f4) |
| 2 | Jointure F2 sur `work_id` au lieu de `book_id` | Similarité des genres calculée sur ~1 % des notes | **Non corrigé** (attend l'accord du binôme) |
| 3 | `bulk_write(ordered=True)` via `mongos` | Import à ~80 documents/s sur le cluster | Corrigé (import_data) |
| 4 | `_id` créé par les shards lors d'un upsert | Ordre chronologique perdu, F3 différent | Corrigé (import_data), **à reporter dans f6** |
| 5 | Client tué, opération toujours active sur le serveur | Collection recréée non répartie | Procédure ci-dessous |
| 6 | Index unique sans la clé de répartition en tête | Création d'index refusée sur le cluster | Corrigé (import_data) |
| 7 | Doublons de `book_tags` : garder le premier ou le dernier | Deux comptes de tags changent | Documenté |
| 8 | Shard arrêté et `allowPartialResults` | Résultat partiel sans aucune erreur | Évité (availability.py) |
| 9 | Relancer `import_data.py` | Efface les notes ajoutées par F6 | Mise en garde |
| 10 | Données préparées modifiées sans tout réimporter | Comparaison local / cluster faussée | Mise en garde |
| 11 | Deux réécritures parallèles du même fichier | Conflit de merge | Résolu |
| 12 | Encodage de la console Windows | Accents illisibles dans la sortie | Mise en garde |

---

## 1. Un index couvrant inutilisé à cause de `_id`

**Symptôme.** Les index couvrants `book_rating_user (book_id, rating, user_id)` et `user_rating_book (user_id, rating, book_id)` étaient bien créés et bien choisis par MongoDB, mais le temps de F1 et F4 ne baissait presque pas.

**Cause.** MongoDB renvoie `_id` par défaut. Les requêtes projetaient `{"user_id": 1}`, ce qui inclut implicitement `_id`. Ce champ n'est pas dans l'index, donc MongoDB doit quand même lire chaque document (étape `FETCH` dans `explain`).

**Correction.** Exclure `_id` explicitement :

```python
db.ratings.find({"book_id": b, "rating": {"$gte": 4}}, {"_id": 0, "user_id": 1})
```

**Comment le repérer.** Dans `explain("executionStats")`, une requête couverte a `totalDocsExamined: 0` et aucune étape `FETCH`. Si `totalDocsExamined` est égal au nombre de résultats, la requête n'est pas couverte.

---

## 2. F2 : jointure sur le mauvais champ (`work_id`)

**Symptôme.** F2 renvoie un résultat plausible (par exemple 74,56 pour 20000 / 22534), sans erreur. Mais la similarité des genres est calculée sur presque rien : pour le lecteur 20000, **1 note sur 91** trouve son livre.

**Cause.** Dans `get_user_tags`, le `$lookup` relie `ratings.book_id` à `books.work_id`. Les deux sont des entiers, mais ce ne sont pas les mêmes identifiants. Les rares correspondances sont des coïncidences numériques.

```python
"localField": "book_id",
"foreignField": "work_id",    # devrait être "book_id"
```

**Correction proposée (non appliquée).** Remplacer par `"foreignField": "book_id"`. Testée hors du fichier : F2 passe de 0,46 s à 0,09 s, et la similarité des genres de 67,21 à 98,42 pour la paire 20000 / 22534. **f2.py appartient au binôme : à valider avant de modifier.** Il faudra ensuite relancer `bench.py` et `compare.py`.

**Comment le repérer.** Compter les documents juste après le `$unwind` qui suit le `$lookup`, et comparer au nombre de notes du lecteur. Un `$unwind` élimine en silence les documents sans correspondance.

---

## 3. Import très lent sur le cluster : `ordered=True`

**Symptôme.** Le même import prend 1 min 47 s en local, mais sur le cluster il ne charge qu'environ **80 documents par seconde**. Il faudrait des heures pour 2,1 millions de documents.

**Cause.** En mode ordonné, `mongos` doit exécuter les opérations dans l'ordre exact de la liste. Avec une clé hachée, deux notes consécutives sont presque toujours sur des shards différents. `mongos` ne peut donc pas regrouper les opérations par shard : il fait presque un aller-retour réseau par document. En local il n'y a qu'un serveur, donc le problème ne se voit pas.

**Correction.** `bulk_write(operations, ordered=False)` dans `import_data.py`. Le résultat est identique : les collections sont vidées avant l'import et les fichiers sont dédoublonnés, donc l'ordre d'exécution ne change rien. L'import réparti prend alors 5 min 18 s.

**Mise en garde.** `ordered=False` n'est sans risque que si les opérations sont indépendantes. S'il y a deux opérations sur le même document (doublon dans le fichier, ou note modifiée deux fois dans un lot F6), l'ordre n'est plus garanti.

**Comment le repérer.** Mesurer le débit pendant l'import (nombre de documents à deux instants, à 20 s d'intervalle). Tester tout script d'écriture **sur le cluster**, pas seulement en local.

---

## 4. Ordre chronologique perdu : `_id` créé par les shards

**Symptôme.** Après le merge de F6, `compare.py` donnait 23 résultats identiques sur 26. Les 3 différences concernaient F3, et uniquement sur la base répartie par livre (`bookscope_book`). Elles portaient sur les livres graines (`seeds`), choisis à partir de l'historique du lecteur.

**Cause.** F3 retrouve l'ordre chronologique des notes d'un lecteur en triant sur `_id` (`user_history` dans `f3.py`). Cela suppose que les `_id` suivent l'ordre du fichier.
- Avec `insert_many`, c'est le cas : pymongo crée les `_id` côté client, dans l'ordre.
- Avec un upsert (`UpdateOne(..., upsert=True)`), c'est **le serveur** qui crée le `_id`. Sur le cluster réparti par livre, les notes d'un même lecteur sont sur deux shards, et chaque shard crée ses propres `_id`. Un `ObjectId` contient l'heure à la seconde, puis un identifiant propre à chaque processus : à seconde égale, l'ordre entre deux shards est arbitraire.
- Avec la répartition par lecteur, toutes les notes d'un lecteur sont sur le même shard, ce qui explique pourquoi seule `bookscope_book` différait.

**Correction.** Créer le `_id` côté client dans l'upsert :

```python
UpdateOne(identity, {"$setOnInsert": {"_id": ObjectId(), **document}}, upsert=True)
```

**Vérifié.** Après réimport des trois bases, `compare.py` donne de nouveau 26/26 comparaisons identiques.

**À reporter dans `f6.py`.** F6 laisse aussi le serveur créer les `_id` des nouvelles notes (`$set` avec `upsert=True`). Sur le cluster réparti par livre, l'ordre entre ces nouvelles notes peut être faux, ce qui fausserait F3 lancé après F6. Même correction : `{"$set": document, "$setOnInsert": {"_id": ObjectId()}}`.

**Leçon plus générale.** S'appuyer sur `_id` pour l'ordre est fragile. Un champ explicite (position dans l'historique, ou date) serait plus robuste. Goodbooks-10k n'a pas de date, donc l'ordre des lignes est la seule information chronologique disponible.

**Comment le repérer.** Seule la comparaison local / cluster l'a montré. Il faut lancer `compare.py` **après chaque changement de l'import**, sur les deux répartitions.

---

## 5. Client tué, opération toujours active sur le serveur

**Symptôme.** Après l'arrêt d'un import trop lent, chaque nouvel import échouait avec `collection ratings already exists`. `drop()` semblait sans effet, et `config.collections` ne connaissait plus `bookscope.ratings`. Pourtant shard1 contenait une collection `ratings` **non répartie**, dont le nombre de documents augmentait.

**Cause.** Tuer le script Python n'arrête pas ce qu'il a déjà envoyé. pymongo envoie les écritures par lots (jusqu'à 100 000 opérations), et `mongos` continuait d'exécuter le dernier lot reçu. Après le `drop`, chaque upsert recréait la collection, cette fois non répartie, sur le shard primaire. `shard_activity` échouait ensuite sur `create_collection`.

**Correction.** Trouver l'opération sur `mongos` et l'arrêter :

```python
ops = client.admin.aggregate([{"$currentOp": {"allUsers": True, "localOps": True}},
                              {"$match": {"ns": {"$regex": "^bookscope"}}}])
for o in ops:
    client.admin.command("killOp", op=o["opid"])
```

Ensuite, relancer l'import.

**Comment le repérer.** Avant de relancer un import interrompu, vérifier `$currentOp`. Le nombre de documents de chaque shard en connexion directe (`directConnection=True`, ports 27301 / 27302) montre aussi ce qui se passe vraiment.

---

## 6. Index unique et clé de répartition

**Règle.** Sur la base répartie par livre (`--shard-key book_id`), MongoDB refuserait de créer l'index unique `(user_id, book_id)`, et l'import échouerait.

**Cause.** Sur une collection répartie, MongoDB n'accepte un index unique que s'il **commence par la clé de répartition**. Chaque shard ne vérifie l'unicité que sur ses propres données. Ce n'est correct que si deux documents identiques tombent forcément sur le même shard.

**Correction.** Dans `import_data.py`, l'index unique est `(book_id, user_id)` quand la clé est `book_id`, et `(user_id, book_id)` sinon. L'unicité de la paire est la même.

---

## 7. Doublons de `book_tags` : lequel garder ?

**Constat.** `book_tags.csv` contient 999 912 lignes, dont 8 doublons `(goodreads_book_id, tag_id)`. Pour deux d'entre eux, les comptes diffèrent :

| goodreads_book_id | tag_id | comptes |
|---|---|---|
| 3 | 22743 | 6066 et 6065 |
| 77449 | 15169 | 8 et 7 |

**Ce qui a changé.**
- Avant le merge, l'index unique rejetait le second doublon à l'import, donc on gardait le **premier**.
- Depuis F6, `prepare_data.py` supprime les doublons en gardant le **dernier** (`book_tags_filtered.csv`).

Les deux choix se défendent, mais ils ne donnent pas exactement les mêmes données. Toute base importée avant ce changement doit être réimportée (voir le point 10).

**Leçon.** Un nettoyage de données est un choix qui doit être écrit quelque part (ici dans `prepare_data.py` et dans `LocalPrototype.md`). Il ne doit pas être un effet de bord d'un index.

---

## 8. Shard arrêté : échec lent, ou pire, résultat partiel silencieux

**Constat** (shard2 arrêté avec `python cluster.py stop-shard 2`) :
- **Sans précaution**, une requête qui a besoin de shard2 échoue après environ 15 s avec `FailedToSatisfyReadPreference`, suivie d'une trace Python.
- **Avec `allowPartialResults=True`**, la même requête renvoie **1 630 lecteurs au lieu de 3 360**, sans erreur ni avertissement. C'est le pire cas : un résultat faux présenté comme complet.

**Correction.** `availability.py` vérifie **avant le calcul**, à partir de `config.shards` et `config.chunks`, si les données nécessaires sont sur des shards joignables. Selon le cas, le service :
- calcule normalement, quand toutes les données nécessaires sont joignables ;
- ou échoue explicitement en environ 2 s, en indiquant ce qui manque.

Une erreur qui arrive pendant le calcul est aussi transformée en échec explicite (`run_or_fail`). **Ne pas utiliser `allowPartialResults`** dans les services.

---

## 9. Relancer `import_data.py` efface F6

`import_data.py` supprime les collections avant d'importer, pour que chaque import donne exactement le même état. Conséquence : les notes ajoutées ou modifiées par `f6.py` sont perdues. L'ordre normal est `prepare_data.py`, puis `import_data.py`, puis `f6.py`. Après un réimport, il faut relancer F6.

---

## 10. Changer les données préparées sans tout réimporter

`compare.py` vérifie que local et cluster donnent les mêmes résultats. Il suppose que les trois bases (`local`, `cluster`, `cluster_book`) ont été chargées **à partir des mêmes fichiers et avec le même import**. Après toute modification de `prepare_data.py` ou de `import_data.py`, il faut réimporter les trois :

```bash
python prepare_data.py
python import_data.py
python import_data.py --uri mongodb://localhost:27400/
python import_data.py --uri mongodb://localhost:27400/ --db bookscope_book --shard-key book_id
python compare.py
```

Sinon, une différence peut venir des données et pas du déploiement, ou au contraire masquer un vrai problème.

---

## 11. Deux réécritures parallèles du même fichier

**Ce qui s'est passé.** `import_data.py` a été restructuré en même temps de deux façons :
- côté Part II : options en ligne de commande, sharding, index couvrants ;
- côté F6 : fonction `create_indexes`, import par upsert, `book_tags_filtered.csv`.

Git ne pouvait pas fusionner automatiquement.

**Résolution.** On a gardé les deux. `create_indexes(db, pair)` reçoit l'ordre de l'index unique, et l'import par upsert est conservé avec les corrections 3 et 4. Les autres fichiers (`f2.py`, `prepare_data.py`, `config.py`) ont été fusionnés automatiquement et vérifiés.

**Conseil.** Prévenir le binôme avant de restructurer un fichier partagé, et faire de petits commits. Un merge réussi sans conflit n'est pas forcément correct : le point 4 venait d'une combinaison des deux versions, pas d'un conflit. Il faut relancer les tests après chaque merge.

---

## 12. Encodage de la console Windows

Sous Windows, la console n'utilise pas UTF-8 par défaut. Les accents apparaissent alors mal (« V�rification »), et un `print` peut même échouer avec `UnicodeEncodeError`. Les scripts commencent par :

```python
sys.stdout.reconfigure(encoding="utf-8")
```

`prepare_data.py` ne le fait pas encore.

---

## Méthode qui a permis de trouver ces erreurs

1. **`explain` avant de conclure** sur une performance. Par exemple, on pensait que le `$lookup` de F2 relisait `books` pour chaque note ; `explain` a montré une seule jointure par hachage.
2. **Comparer plutôt que relire.** `compare.py` (local / cluster / autre répartition) a trouvé l'erreur 4, que la relecture du code ne montrait pas.
3. **Compter à chaque étape** d'un pipeline (erreur 2) et pendant un import (erreur 3).
4. **Tester les cas de panne** volontairement (`stop-shard`), plutôt que supposer le comportement de MongoDB (erreur 8).
