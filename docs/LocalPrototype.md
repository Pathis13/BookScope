# Part I : Local prototype

Compte rendu de la section 4 (Part I) du sujet : ce qui est demandé, ce qui a été fait et les mesures obtenues.

## Ce que demande le sujet

1. Les six services tournent sur **un serveur de données local**, c'est-à-dire un processus séparé interrogé par le réseau.
2. Le rapport **documente** la technologie choisie, les relations et attributs gardés, les requêtes fréquentes et ce qui les rend efficaces.
3. **Au moins un compromis de modélisation** est justifié par des mesures réelles.
4. **Une représentation alternative** est implémentée et mesurée : modèle de données, relations précalculées ou index. Elle porte sur les mêmes données et sur au moins deux requêtes représentatives, avec une conclusion : on la garde ou on la rejette.
5. La préparation et le chargement des données sont **reproductibles**.

## 1. Serveur de données

MongoDB tourne comme processus `mongod` séparé. Les scripts Python l'interrogent avec pymongo sur `mongodb://localhost:27017/`, base `bookscope`.

| Élément | Version / valeur |
|---|---|
| Machine | Windows 11 Pro, AMD Ryzen 7 9800X3D (8 cœurs, 16 threads), 31 Go de RAM |
| MongoDB | 9.0.4, moteur WiredTiger, cache de 15,0 Go |
| Python | 3.13.14 |
| pymongo / pandas | 4.19.0 / 3.0.6 (figés dans `requirements.txt`) |

Toute la base tient dans le cache de MongoDB : 15,8 Mo de données compressées pour `ratings`. Les mesures se font donc base « chaude », voir les limites plus bas.

## 2. Modèle de données

### Collections et attributs gardés

Il y a une collection par fichier source. Les notes restent **à plat**, un document par couple (lecteur, livre).

| Collection | Contenu | Documents | Attributs utilisés |
|---|---|---|---|
| `ratings` | notes des lecteurs 20000 à 29999, les 80 % les plus anciennes de chaque lecteur | 903 033 | `user_id`, `book_id`, `rating` |
| `books` | catalogue complet | 10 000 | `book_id`, `goodreads_book_id`, `title`, `original_title`, `authors`, `original_publication_year`, `average_rating`, `ratings_count` |
| `book_tags` | nombre de fois où un tag a été posé sur un livre | 999 904 | `goodreads_book_id`, `tag_id`, `count` |
| `tags` | noms des tags | 34 252 | `tag_id`, `tag_name` |
| `to_read` | listes de lecture des mêmes lecteurs | 182 725 | `user_id`, `book_id` |

Les autres colonnes de `books.csv` (ISBN, URLs d'images, détail des notes de 1 à 5…) sont importées mais pas utilisées.

### Relations

- `ratings.book_id` et `to_read.book_id` renvoient à `books.book_id`.
- `book_tags.goodreads_book_id` renvoie à `books.goodreads_book_id`, **pas** à `book_id`. Pour relier un tag aux notes, il faut passer par `books` (un `$lookup` dans F1, une table de correspondance dans F5).
- `book_tags.tag_id` renvoie à `tags.tag_id`.

### Requêtes fréquentes et index qui les servent

| Requête | Services | Index utilisé (vérifié par `explain`) |
|---|---|---|
| Lecteurs ayant aimé un livre (`book_id`, `rating >= 4`) | F1, F4, F5 | `book_rating_user` (couvrant) |
| Livres aimés par un ensemble de lecteurs (`user_id $in`, `rating >= 4`, `$group` par livre) | F1, F4, F5 | `user_rating_book` (couvrant) |
| Notes d'un lecteur, livres communs à deux lecteurs | F2, F3 | `user_rating_book` (couvrant) |
| Notes d'un lecteur → livres → tags (`$lookup`) | F2 | `$lookup` sur `books.work_id` : **pas d'index**, jointure par hachage (`HashJoin`) après lecture complète de `books` ; voir l'anomalie de F2 plus bas |
| Unicité d'une note (lecteur, livre) | import, F6 | `user_id_1_book_id_1` (unique) |
| Livres d'un tag | F1, F5 | `book_tags.tag_id` |
| Tags d'un livre | F1 | `book_tags.(goodreads_book_id, tag_id)` (unique) |
| Livre par identifiant | tous | `books.book_id` (unique), `books.goodreads_book_id` |
| Agrégations globales (popularité des livres, activité des lecteurs) | F3, F4, F5 | aucun : lecture complète de `ratings` (`COLLSCAN`, 903 033 documents, environ 0,27 s) |
| Recherche d'un titre (`$regex` non ancrée, insensible à la casse) | F1, F4 | aucun : lecture complète de `books` (`COLLSCAN`, 10 000 documents, 8 ms) |

L'index `books.title` créé à l'import ne sert donc pas : une `$regex` qui cherche une partie du titre, sans `^`, ne peut pas l'exploiter. Ce n'est pas gênant sur 10 000 livres.

## 3. Alternative mesurée : index couvrants sur `ratings`

### Hypothèse

Les requêtes les plus fréquentes de F1, F4 et F5 ne lisent que `user_id`, `book_id` et `rating`. Avec les index d'origine, `(user_id, book_id)` et `(book_id)`, MongoDB trouve les notes par l'index mais doit ensuite **lire chaque document** (étape `FETCH`) pour connaître `rating`. Deux index qui contiennent les trois champs permettent de répondre sans lire les documents (`PROJECTION_COVERED`) :

- `book_rating_user` = `(book_id, rating, user_id)` : lecteurs ayant aimé un livre ;
- `user_rating_book` = `(user_id, rating, book_id)` : livres aimés par des lecteurs.

Il a fallu une petite modification du code pour en profiter : les projections de `f1.similar_books` et `f4.neighbors` excluent maintenant `_id` (`{"_id": 0, ...}`), sinon MongoDB doit quand même lire le document.

### Protocole (`scripts/bench.py`)

- Les deux configurations portent sur les mêmes données. `base` correspond aux index d'origine, `covering` à `base` plus les deux index couvrants.
- Entrées fixes : livre 1 (le plus aimé, donc le cas le plus lourd), lecteur 20000, F2 entre les lecteurs 20000 et 22534 (28 livres en commun, le maximum pour 20000), F4 entre les livres 1 et 5000, F5 entre fantasy (11305) et romance (26138).
- Pour chaque requête, le premier appel après le changement d'index est noté « froid », puis viennent 5 appels « chauds » (médiane et écart-type).
- `explain("executionStats")` donne le plan, les clés et les documents examinés pour les requêtes élémentaires.
- Coût en écriture : insertion du lot F6 (112 863 notes) dans une collection temporaire portant les mêmes index, 3 fois.
- Les résultats des requêtes et services sont comparés entre les deux configurations : **ils sont identiques** pour les 8 requêtes.
- Mesures brutes dans `results/bench_raw.csv`, résumé dans `results/bench_summary.csv`, environnement dans `results/bench_env.txt`, sortie complète dans `results/bench_log.txt`.

### Résultats (médiane à chaud, 5 répétitions, 8 novembre 2026)

**Requêtes élémentaires**

| Requête | base | covering | Plan base → covering | Documents lus base → covering |
|---|---|---|---|---|
| Lecteurs ayant aimé le livre 1 | 5 ms | 3 ms | `FETCH > IXSCAN` → `PROJECTION_COVERED > IXSCAN` | 3 998 → **0** |
| Notes du lecteur 20000 | < 1 ms | < 1 ms | `FETCH > IXSCAN` → `PROJECTION_COVERED > IXSCAN` | 91 → **0** |
| Livres aimés par les lecteurs du livre 1 | 242 ms | **71 ms** (−71 %) | `GROUP > FETCH > IXSCAN` → `GROUP > PROJECTION_COVERED > IXSCAN` | 308 065 → **0** |

Le nombre de clés examinées baisse aussi (de 3 998 à 3 360, et de 313 614 à 230 690). Comme `rating` fait partie de l'index, la condition `rating >= 4` limite directement la plage lue au lieu de filtrer après coup.

**Services complets**

| Service | base | covering | Gain | Écart-type base / covering |
|---|---|---|---|---|
| F1 (livre 1) | 2,78 s | 1,61 s | −42 % | 0,06 / 0,06 s |
| F2 (20000 / 22534) | 0,47 s | 0,46 s | −3 % | 0,005 / 0,003 s |
| F3 (lecteur 20000) | 14,79 s | 9,41 s | −36 % | 0,09 / 0,04 s |
| F4 (1 → 5000) | 1,10 s | 0,43 s | −62 % | 0,02 / 0,009 s |
| F5 (fantasy / romance) | 4,47 s | 1,61 s | −64 % | 0,11 / 0,03 s |

F2 ne gagne presque rien : sa lecture de `ratings` (livres communs, 7 ms) était déjà rapide. Son temps est presque entièrement dans `get_user_tags`, la jointure notes → livres → tags, que les index de `ratings` ne touchent pas.

**Coûts**

| Coût | base | covering |
|---|---|---|
| Taille totale des index de `ratings` | 23,3 Mo | 43,2 Mo (+85 %, +19,9 Mo) |
| Création des deux index | — | 2,1 s |
| Insertion du lot F6 (112 863 notes) | 0,86 s | 1,21 s (+41 %) |

### Compromis et décision : **retenue**

C'est le compromis classique entre la lecture d'un côté, l'écriture et l'espace disque de l'autre. Chaque note est maintenant écrite dans 5 index au lieu de 3.

- **Les lectures** gagnent 36 à 64 % sur F1, F3, F4 et F5. Ce sont des services interactifs, utilisés pendant la démonstration. F2 n'est ni accéléré ni ralenti.
- **Les écritures** ne se font que par lots, avec F6 et l'import : 0,35 s de plus pour un lot de 113 000 notes.
- **L'espace** supplémentaire est de 20 Mo, négligeable ici.

Les deux index sont donc ajoutés à `import_data.py`. `bench.py` laisse aussi cette configuration en place à la fin, sauf avec `--keep base`.

Conséquence : `book_id_1` devient redondant, puisque c'est le début de `book_rating_user`. On pourrait le supprimer pour économiser 5,5 Mo et un peu de temps d'écriture, mais ce n'est pas mesuré ici. `user_id_1_book_id_1` reste nécessaire pour garantir l'unicité d'une note (lecteur, livre), dont F6 a besoin.

### Limites des mesures

- **Base toujours en mémoire.** Les données tiennent dans le cache de 15 Go. Le premier appel après un changement d'index (« froid ») n'est donc presque pas plus lent qu'un appel « chaud ». Une vraie mesure à froid demanderait de redémarrer `mongod` avant chaque série.
- **Client et serveur sur la même machine.** Ils partagent processeur, mémoire et disque.
- **Une seule entrée par service.** Le livre 1 est le cas le plus lourd pour F1 et F4 ; un livre peu aimé donnerait des gains absolus plus faibles.
- **Le reste du temps n'est pas dans les index.** Ce qui reste dans F3 (9,4 s) vient surtout des 10 appels à F1 et des agrégations globales, qui lisent toute la collection quels que soient les index.

### Anomalie trouvée dans F2 : jointure sur `work_id`

`get_user_tags` ([f2.py](../scripts/f2.py)) relie `ratings.book_id` à `books.work_id`. Or `ratings.book_id` renvoie à `books.book_id`, et `work_id` est un autre identifiant. Pour le lecteur 20000, **seule 1 de ses 91 notes** trouve un livre par `work_id`, et ce n'est probablement pas le bon. La similarité par tags de F2 est donc calculée sur presque rien.

Mesure de `get_user_tags` sur les deux lecteurs de référence, jointure actuelle contre jointure corrigée (`foreignField: "book_id"`, testée hors du fichier, médiane de 5 appels) :

| Jointure | Temps (2 lecteurs) | Tags trouvés (20000 / 22534) | Similarité par tags |
|---|---|---|---|
| `work_id` (actuel) | 0,46 s | 100 / 284 | 67,21 |
| `book_id` (corrigé) | **0,09 s** | 1 420 / 1 583 | 98,42 |

La correction rend F2 environ 5 fois plus rapide, parce que `books.book_id` est indexé : il n'y a plus de lecture complète de `books`. Surtout, elle **change le résultat**. Cette correction relève de la personne qui écrit F2 ; les mesures de F2 du tableau ci-dessus sont celles de la version actuelle.

### Alternatives écartées sans implémentation

- **Notes imbriquées par lecteur** (`users { user_id, ratings: [...] }`). Les requêtes centrées lecteur (F2, F3) liraient un seul document. Mais celles centrées livre (F1, F4, F5) devraient parcourir tous les tableaux (`$unwind`) ou dépendre d'un index multiclé, et une mise à jour F6 réécrirait tout le document du lecteur. Comme la plupart des services sont centrés livre, ce modèle n'a pas été retenu. Il redevient intéressant en Part II pour placer toutes les notes d'un lecteur sur la même instance.
- **Liens livre → livre précalculés.** Le gain serait probablement plus grand pour F1 et F4, mais F6 devrait maintenir cette collection à chaque import (exigence du sujet). Écarté tant que F6 n'est pas stabilisé.

## 4. Reproductibilité

Procédure complète (voir aussi le README) :

```bash
python -m pip install -r requirements.txt   # versions figées
cd scripts
python prepare_data.py   # sélection 20000-29999, découpage chronologique 80 / 10 / 10 %
python import_data.py    # supprime les collections, crée les index, importe
python bench.py          # mesures base vs covering -> results/
```

- `prepare_data.py` n'utilise aucun hasard : le découpage suit l'ordre des lignes (l'ordre chronologique d'après la documentation), donc il est identique à chaque exécution.
- `import_data.py` **supprime maintenant les collections** avant d'importer. Relancer l'import donne toujours le même état, au lieu de s'appuyer sur les index uniques pour ignorer les doublons.
- L'import **affiche les lignes rejetées**. Le fichier `book_tags.csv` source contient 999 912 lignes, dont **8 doublons** `(goodreads_book_id, tag_id)`, refusés par l'index unique ; 999 904 documents sont gardés. C'est un des problèmes de données à documenter en annexe.

Attention pour F6 : relancer `import_data.py` efface les notes ajoutées par F6 et remet la base à son état initial.

## Ce qu'il reste à faire

- [ ] Rédiger la section du rapport à partir de ce document : modèle, tableau des requêtes et index, mesures, décision.
- [ ] Prévenir la personne qui fait F6 : l'insertion est environ 40 % plus lente avec les index couvrants, et l'import remet la base à zéro.
- [ ] Corriger la jointure `work_id` → `book_id` de F2, puis relancer `bench.py`.
- [ ] (Optionnel) Mesurer la suppression de `book_id_1`.
- [ ] (Optionnel) Refaire une série à froid en redémarrant `mongod`, et mesurer F1 et F4 sur un livre peu aimé.
