# Part II : Distributed prototype

Compte rendu de la section 4 (Part II) du sujet. Le déploiement suit le **Lab 5 (Distributed MongoDB : replication and sharding)** : même architecture (serveur de configuration, shards en replica sets à un membre, routeur `mongos`), mêmes ports, même type de clé (hachée), même méthode d'observation (`explain` via `mongos`, Lab 5 T27).

## Ce que demande le sujet

1. Un second déploiement avec **au moins deux instances de données** séparées (ports et stockage distincts). Les notes des lecteurs sont **réparties**, pas copiées ; le catalogue peut être recopié. Une **interface unique** pour l'utilisateur.
2. Une **règle de placement** justifiée par les accès de l'application, et son effet sur une requête centrée lecteur, une centrée livre et un parcours lecteur–livre : instances contactées, travail local, échanges.
3. Une **comparaison automatique** des résultats des deux déploiements (ordre canonique, égalités départagées), y compris sur des lecteurs et livres stockés sur des instances différentes.
4. **Une configuration distribuée alternative** évaluée (autre placement ou autre nombre d'instances).
5. **Perte d'une instance** : F2 puis F3 avec une instance arrêtée, sans jamais présenter un résultat partiel comme complet ; distinguer ce qu'on récupère de ce que récupérerait un système répliqué ; ne pas présenter plusieurs processus sur un poste comme un vrai cluster.

## 1. Déploiement (Lab 5, parties 5 et 6)

| Processus | Rôle | Port | Stockage | Lab 5 |
|---|---|---|---|---|
| `mongod --configsvr` (replica set `configRS`) | métadonnées du cluster : shards, chunks, collections réparties | 27200 | `data/cluster/config` | T19 |
| `mongod --shardsvr` (replica set `shard1RS`) | instance de données 1 | 27301 | `data/cluster/shard1` | T20 |
| `mongod --shardsvr` (replica set `shard2RS`) | instance de données 2 | 27302 | `data/cluster/shard2` | T20 |
| `mongos` | routeur, **seul point d'entrée** de l'application | 27400 | aucun | T21 |

- Chaque processus a son propre port et son propre dossier de données (Lab 5, Q1).
- Comme dans le Lab, chaque shard est un replica set **à un seul membre** : c'est une simplification, il n'y a aucune copie des données (voir la section 5).
- L'interface reste unique : les scripts F1 à F6 sont inchangés, seule l'adresse change (`BOOKSCOPE_URI=mongodb://localhost:27400/`). `mongos` envoie chaque requête aux bons shards (Lab 5, Q11).
- `mongosh` n'étant pas installé, `scripts/cluster.py` envoie depuis Python les mêmes commandes que le Lab (`replSetInitiate` = `rs.initiate`, `addShard` = `sh.addShard`, `shardCollection` = `sh.shardCollection`).
- Versions : MongoDB 9.0.4, sur la même machine que la Part I (Ryzen 7 9800X3D, 31 Go de RAM, Windows 11).

## 2. Règle de placement : notes réparties par lecteur

**Clé de répartition : `{user_id: "hashed"}`** pour `ratings` et `to_read` (activité des lecteurs), comme `{age: "hashed"}` pour `users` dans le Lab 5 (T23). Le catalogue (`books`, `book_tags`, `tags`) n'est pas réparti et reste sur le shard primaire de la base (`shard1RS`).

Pourquoi `user_id` :

- **Accès** : F2, F3, F6 et l'expérience E3 partent d'un lecteur. Le sujet annonce « many personalised requests about individual readers ». Avec cette clé, toutes les notes d'un lecteur sont sur une seule instance : la requête est **ciblée** (Lab 5, Q13).
- **F6** : l'identité d'une note est le couple (lecteur, livre), garanti par un index unique `(user_id, book_id)`. Sur un cluster, un index unique doit commencer par la clé de répartition : c'est le cas.
- **Hachage plutôt que plages** : les lecteurs 20000 à 29999 sont répartis uniformément, sans réglage manuel des plages. On perd l'ordre (Lab 5, Q14), mais aucun service ne cherche une plage de lecteurs.
- **Prix à payer** : toute requête centrée livre (F1, F4, F5) doit interroger toutes les instances.

Répartition observée (`python cluster.py status`, équivalent de `getShardDistribution()`, Lab 5 T25) :

| Collection | shard1RS | shard2RS |
|---|---|---|
| `ratings` | 445 160 notes | 457 873 notes |
| `to_read` | 90 136 | 92 589 |
| catalogue | `books`, `book_tags`, `tags` (shard primaire) | — |

## 3. Effet de la répartition sur trois types de requêtes

Mesuré par `scripts/dist_explain.py` : `explain("executionStats")` exécuté via `mongos` (Lab 5, T27), temps = médiane de 5 exécutions. Les index couvrants de la Part I sont présents : les shards ne lisent jamais les documents, seulement les index.

| Requête | Shards contactés | Travail sur chaque shard | Échangé entre instances | Travail sur `mongos` | Temps |
|---|---|---|---|---|---|
| **1. Centrée lecteur** : notes du lecteur 20000 | **1** (shard2RS), `SINGLE_SHARD` | lecture de 91 clés d'index | 91 notes → `mongos` | transmission | 1 ms |
| **2. Centrée livre** : lecteurs ayant aimé le livre 1 | **2**, `SHARD_MERGE` | shard1 : 1 630 clés ; shard2 : 1 730 clés | 1 630 + 1 730 identifiants → `mongos` | fusion des deux curseurs → 3 360 lecteurs | 5 ms |
| **3. Parcours** : livre 1 → ses lecteurs → les livres qu'ils ont aimés | saut 1 = requête 2 ; saut 2 : **2** | saut 2 : `$match` + `$group` sur ses propres lecteurs (112 319 et 120 033 clés) | **32 Ko envoyés à chaque shard** (la liste des 3 360 lecteurs) ; **8 582 + 8 704 comptes partiels** → `mongos` | second `$group` qui additionne les comptes partiels → 9 407 livres | 156 ms |

Pour le parcours, chaque shard ne connaît qu'une partie des lecteurs de chaque livre : il renvoie des **comptes partiels**. `mongos` doit les additionner, d'où un `$group` de fusion (`mergerPart : $mergeCursors > $group`).

### Effet sur les services complets

Temps d'un appel, mesuré par `compare.py` (une seule exécution, donc indicatif ; l'expérience E2 fera des mesures répétées) :

| Service | Local | Cluster, par lecteur | Cluster, par livre (alternative) |
|---|---|---|---|
| F1 (livre 1) | 1,51 s | 9,24 s | 4,95 s |
| F1 (livre 9000) | 0,67 s | 1,03 s | 1,07 s |
| F2 (20000 / 22534), après correction de la jointure | 0,11 s | 0,15 s | 0,16 s |
| F3 (lecteur 20000) | 8,84 s | 26,82 s | 19,26 s |
| F3 (lecteur 20000, 5 notes) | 4,12 s | 15,52 s | 9,96 s |
| F4 (1 → 5000) | 0,45 s | 0,90 s | 0,91 s |
| F5 (fantasy / romance) | 1,58 s | 2,93 s | 3,14 s |

- Tout est plus lent que sur le serveur unique. Les processus partagent la même machine : on ajoute des allers-retours réseau et des fusions, sans ajouter de matériel.
- **F1 est le plus touché** (×6 sur le livre 1). Pour chaque livre candidat, il compte les lecteurs qui l'ont aimé avec un `$lookup` sur `ratings`. En placement par lecteur, chacun de ces milliers de `$lookup` est envoyé aux deux shards. F3 appelle F1 dix fois et hérite du même coût.

## 4. Résultats identiques sur les deux déploiements

`scripts/compare.py` lance F1 à F5 avec les mêmes entrées sur le serveur local (référence), sur le cluster en placement par lecteur et sur le cluster en placement par livre. Les trois bases sont chargées par `import_data.py` à partir des mêmes CSV.

- **Forme canonique** : les nombres réels sont arrondis à 10⁻⁹, parce qu'un `$group` réparti additionne dans un autre ordre (écarts de l'ordre de 10⁻¹⁵). Les listes sont triées par score puis par identifiant, ce qui départage les égalités de façon déterministe.
- **Entrées réparties sur plusieurs instances** :
  - F2 sur 20000 (shard2RS) et 22534 (shard1RS), plus une paire sur un même shard ;
  - F3 sur un lecteur de chaque shard, plus un historique tronqué à 5 notes ;
  - F1 et F4 sur des livres de chaque shard en placement par livre (1, 4000, 5000, 1129 sur shard1RS ; 9000 sur shard2RS) ;
  - F5 sur deux paires de tags.
- Le shard de chaque lecteur ou livre est affiché, d'après l'`explain` de la requête ciblée.
- **Résultat : 26/26 comparaisons identiques** (13 cas × 2 déploiements). Rapport dans `results/compare_report.csv` et `results/compare_log.txt`. Le script renvoie le code 1 s'il trouve une différence et indique alors le premier champ qui diffère.

## 5. Configuration alternative : notes réparties par livre

Même cluster à deux shards, base `bookscope_book`, clé `{book_id: "hashed"}`. L'index unique devient `(book_id, user_id)`, parce qu'il doit commencer par la clé de répartition. Répartition : 478 256 notes sur shard1RS et 424 777 sur shard2RS ; le catalogue est sur shard2RS.

| Requête | Par lecteur | Par livre |
|---|---|---|
| 1. Notes du lecteur 20000 | 1 shard, 1 ms | **2 shards** (49 + 42 notes), 1 ms |
| 2. Lecteurs ayant aimé le livre 1 | 2 shards, 5 ms | **1 shard** (`SINGLE_SHARD`, 3 360 clés), 4 ms |
| 3. Parcours, saut 2 | 2 shards, 8 582 + 8 704 comptes **partiels**, second `$group` sur `mongos`, 156 ms | 2 shards, 4 682 + 4 725 comptes **complets** (chaque livre est entier sur un shard), `mongos` concatène seulement, 136 ms |

En placement par livre, les comptes par livre sont terminés sur les shards : il n'y a plus de `$group` de fusion. Le `$lookup` de F1 (lecteurs d'un livre candidat) devient ciblé. F1 et F3 sont donc nettement plus rapides (4,95 s contre 9,24 s pour F1 ; 19,3 s contre 26,8 s pour F3). F2, F4 et F5 ne changent presque pas.

**Décision provisoire : on garde la répartition par lecteur, mais les mesures sont plutôt en faveur du placement par livre.**

- **Pour le placement par livre** : il est plus rapide sur les services d'exploration (F1, F3), parce que leurs requêtes coûteuses sont centrées livre.
- **Pour le placement par lecteur** :
  - il est le seul à garder les requêtes centrées lecteur sur une seule instance ;
  - il limite les dégâts d'une panne (section 6) : avec un shard perdu, la moitié des lecteurs garde son historique complet, alors qu'en placement par livre l'historique de **tous** les lecteurs est amputé ;
  - un lot F6 se répartit par lecteur.
- L'expérience E2 (charge « personnalisée » contre charge « exploration ») doit trancher. La prédiction à déposer avant les mesures est que le placement par lecteur gagne sur la charge personnalisée et le placement par livre sur l'exploration.

Changer le nombre d'instances (3 shards, comme le Lab 5 partie 7) est possible avec `python cluster.py start --shards 3`, mais n'a **pas été testé** ni mesuré.

## 6. Perte d'une instance

### Ce que fait MongoDB seul

Après `python cluster.py stop-shard 2`, une requête qui a besoin de shard2RS attend environ 15 s, puis `mongos` renvoie l'erreur `FailedToSatisfyReadPreference` (« Could not find host matching read preference { mode: "primary" } for set shard2RS »). Sans traitement, l'utilisateur voit une trace Python. Le résultat n'est pas faux, mais l'échec n'est pas explicite.

L'option `allowPartialResults` montre le danger inverse : `find` des lecteurs ayant aimé le livre 1 renvoie **1 630 lecteurs au lieu de 3 360, sans aucune erreur**. C'est exactement le résultat partiel présenté comme complet que le sujet interdit. BookScope ne l'utilise pas.

### Ce que fait BookScope (`scripts/availability.py`)

Avant de calculer, F2 et F3 demandent au serveur de configuration, qui reste joignable, quels shards répondent et où sont les notes des lecteurs demandés. Le shard d'un lecteur se retrouve à partir du hachage de `user_id`, calculé par `mongos` avec `$toHashedIndexKey`, et des plages de chunks de `config.chunks`.

- **Si une donnée nécessaire est sur un shard arrêté**, le service échoue explicitement en disant ce qui manque, en environ 2 s au lieu de 15 s, avec le code de retour 2.
- **Si tout ce qu'il faut est joignable**, le service calcule et l'indique.
- **Si un shard tombe pendant le calcul**, l'erreur MongoDB devient aussi un échec explicite. F3 prépare tout son rapport avant de l'afficher, donc rien de partiel n'est imprimé.

Résultats avec shard2RS arrêté (placement par lecteur) :

| Commande | Ce que reçoit l'utilisateur |
|---|---|
| `python f2.py 20000 22534` (20000 sur shard2RS, 22534 sur shard1RS) | **Échec explicite** : « notes du lecteur 20000 : stockées sur shard2RS -> INACCESSIBLES ; notes du lecteur 22534 : accessibles ; ÉCHEC de F2 : aucun résultat n'est affiché, il serait calculé sur une partie seulement des données. » |
| `python f2.py 22534 29999` (les deux sur shard1RS) | Résultat **complet**, annoncé comme tel. Il est identique au déploiement local (similarité 80,21, 29 livres communs, après la correction de F2). |
| `python f3.py 29999` (historique sur shard1RS, complet) | **Échec explicite** : l'historique du lecteur est accessible, mais les livres similaires (F1) ont besoin des notes de tous les lecteurs, dont la moitié est sur shard2RS. |
| `python f3.py 20000` (historique sur shard2RS) | **Échec explicite** : historique inaccessible, et F1 aussi. |
| `python f6.py` (le lot touche des lecteurs des deux shards) | **Échec explicite, rien n'est écrit**, en 2 s (code de retour 2). Sans cette vérification, avec `ordered=False`, les notes destinées à shard1RS seraient écrites et les autres non : un lot appliqué à moitié. |
| Placement par livre : `f2.py 22534 29999` | **Échec explicite** : les notes de chaque lecteur sont sur les deux shards, et le catalogue est sur shard2RS. |

F3 aurait pu renvoyer des recommandations calculées sur la moitié des lecteurs avec un avertissement. Nous avons préféré l'échec explicite, plus simple à vérifier et sans risque de mauvaise interprétation.

### Ce qu'on récupère, et ce que récupérerait un système de production

- **Notre déploiement ne récupère rien** : chaque shard est un replica set à un seul membre, sans copie. Tant que le shard est arrêté, ses notes sont inaccessibles. Après `python cluster.py start-shard 2` (même dossier de données), tout revient, sans perte puisque les données étaient sur disque, et F2 sur 22534 / 29999 redonne le même résultat. Si le disque de shard2 était perdu, les notes de la moitié des lecteurs seraient perdues.
- **Un système de production** ferait de chaque shard un replica set de 3 membres sur 3 machines (Lab 5, partie 8, Q18). Si un membre tombe, les deux autres élisent un nouveau primaire en quelques secondes (Lab 5, partie 4, T15 et Q7), et F2 et F3 répondent complètement, sans intervention.
- **Plusieurs processus sur un poste ne valent pas un cluster de machines indépendantes.** Les cinq processus partagent le processeur, la mémoire, le disque et le système. Une panne de la machine les arrête tous. L'« arrêt d'un shard » simule une panne de processus, pas une panne de machine, de disque ou de réseau. Les temps mesurés ne disent rien d'un vrai réseau entre machines.

## 7. Reproduire

```bash
cd scripts
python cluster.py start                                   # config + 2 shards + mongos (ports du Lab 5)
python import_data.py --uri mongodb://localhost:27400/    # placement par lecteur (base bookscope)
python import_data.py --uri mongodb://localhost:27400/ --db bookscope_book --shard-key book_id   # alternative
python cluster.py status                                  # shards, chunks, documents par shard
python compare.py                                         # local vs cluster vs alternative -> results/
python dist_explain.py                                    # 3 requêtes, explain via mongos -> results/

# services sur le cluster (PowerShell : $env:BOOKSCOPE_URI = "mongodb://localhost:27400/")
BOOKSCOPE_URI=mongodb://localhost:27400/ python f2.py 20000 22534

# perte d'une instance
python cluster.py stop-shard 2
BOOKSCOPE_URI=mongodb://localhost:27400/ python f2.py 20000 22534
BOOKSCOPE_URI=mongodb://localhost:27400/ python f3.py 29999
python cluster.py start-shard 2

python cluster.py stop                                    # arrêt ; reset = arrêt + suppression des données
```

## Ce qu'il reste à faire

- [x] F6 sur le cluster. `f6.py` utilise déjà `MONGO_URI` et `DATABASE_NAME` de `config.py`, donc il accepte `BOOKSCOPE_URI` (et `BOOKSCOPE_DB`). Testé sur les trois bases (local, cluster par lecteur, cluster par livre) :
  - premier passage : les 112 863 notes du lot sont insérées, la base passe à 1 015 896 notes ;
  - second passage : 0 insérée, 0 modifiée, 112 863 identiques (le lot peut être rejoué sans effet) ;
  - avec shard2RS arrêté : échec explicite avant toute écriture (section 6).

  Modifications de `f6.py` : `_id` créé côté client pour garder l'ordre chronologique utilisé par F3 (`docs/Pieges.md`, point 4), `ordered=False` (point 3), vérification de disponibilité, compteur « notes déjà identiques » corrigé (il comptait aussi les notes modifiées), sortie en UTF-8.
- [x] Corriger F2 (`work_id` → `book_id`), puis relancer `compare.py`.
- [ ] (Optionnel) Tester et mesurer la configuration à 3 shards (Lab 5, partie 7), en observant les migrations de chunks.
- [ ] Rédiger la section du rapport ; les mesures répétées (moyenne, variabilité, état du cache) relèvent de l'expérience E2.
