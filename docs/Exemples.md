# Exemples réels pour le rapport (F1 à F6)

Exécutés le 8 novembre 2026 sur la base locale (`mongodb://localhost:27017/`, base `bookscope`), **avant F6** : 903 033 notes, lecteurs 20000 à 29999.
Rappel : un lecteur "aime" un livre s'il lui a donné 4 ou 5.

Petit lexique pour les lecteurs du rapport :

- **lecteurs communs** de deux livres : les lecteurs qui ont aimé les deux ;
- **lift** ("x2,3 vs hasard") : nombre de lecteurs communs divisé par le nombre qu'on attendrait si les deux livres étaient aimés au hasard. x1 = rien de plus que le hasard ; x2 = deux fois plus ;
- **tag spécifique** : tag porté par moins de la moitié des livres ; un tag **très fréquent** (to-read, favorites, owned...) est affiché mais ne compte pas ;
- **poids thématique** : part des tags d'un livre, pondérés par leur rareté (idf), qu'on retrouve dans l'autre livre.

---

## F1 : explorer un livre et ses liens

### Exemple principal : `python f1.py 1 --limit 5`

Fiche : *The Hunger Games* (Suzanne Collins, 2008), 3 998 notes dans la base, moyenne 4,33 ; 3 360 lecteurs l'ont aimé.

| # | Livre lié | Calcul du score | Score | Nature du lien | Lecteurs communs | Lift |
|---|---|---|---|---|---|---|
| 1 | [17] *Catching Fire* | 0,5 x 0,860 + 0,5 x 1,000 + 0,30 + 0,08 | 1,313 | même série, même auteur, lectorat partagé, thème spécifique | 1 516 | x2,3 |
| 2 | [20] *Mockingjay* | 0,5 x 0,819 + 0,5 x 0,882 + 0,30 + 0,07 | 1,217 | idem | 1 212 | x2,2 |
| 3 | [507] *Hunger Games Trilogy Boxset* | 0,5 x 0,761 + 0,5 x 0,188 + 0,30 + 0,07 | 0,841 | idem | 81 | x1,6 |
| 4 | [12] *Divergent* (V. Roth) | 0,5 x 0,663 + 0,5 x 0,640 + 0 + 0,05 | 0,701 | lectorat partagé, thème spécifique | 765 | x1,8 |
| 5 | [2] *Harry Potter 1* (J.K. Rowling) | 0,5 x 0,271 + 0,5 x 0,939 + 0 + 0 | 0,605 | lectorat partagé, thème spécifique | 1 860 | x1,7 |

Score = 0,5 x score thématique + 0,5 x score des lecteurs + bonus auteur (0,3) + bonus année (<= 0,1).

À dire :

- Les tomes de la série sont en tête, même le coffret, qui a peu de lecteurs (81).
- **Harry Potter** est faible sur les thèmes (0,271 : tags communs surtout génériques comme "favourite-books", "read-more-than-once") mais fort sur les lecteurs (0,939). F1 distingue bien un lien par les lecteurs d'un lien par le thème.
- **Divergent** a des tags précis communs : "ya-dystopian" (142 livres), "dystopian-fiction" (166 livres).
- Les tags très fréquents communs ("series", "own-it", "favourites"... 22 pour Catching Fire) sont listés à part et **non pris en compte**.
- Calcul du lift pour Catching Fire : attendu au hasard = 3 360 x 1 992 / 10 000 = 669 environ ; observé 1 516 ; lift = 1 516 / 669 = 2,3 environ.

### Titre ambigu : `python f1.py Arcadia`

```
2 livres correspondent à "Arcadia", relancez avec un book_id :
  [6105] Arcadia - Lauren Groff (2012)
  [6481] Arcadia - Tom Stoppard (1993)
```

L'ambiguïté est montrée, pas résolue en silence. 33 titres du catalogue sont portés par plusieurs livres (69 livres en tout).

### Traçabilité : `python f1.py 1 --trace 12`

Liste **les 765 lecteurs** qui ont aimé *The Hunger Games* et *Divergent*, avec leurs deux notes (ex. user 20016 : 5 / 5 ; user 20039 : 5 / 4), puis **chaque tag commun** avec son `count` sur chaque livre, son nombre de livres et son idf. Fin de la liste :

| tag_id | tag | count livre 1 | count livre 12 | nb livres | idf |
|---|---|---|---|---|---|
| 11557 | favorites | 50 755 | 28 771 | 9 881 | 0,01 (très fréquent) |
| 30574 | to-read | 11 314 | 18 813 | 9 983 | 0,00 (très fréquent) |

---

## F2 : comparer deux lecteurs

Méthode :

- les notes des livres communs sont comparées. Écart de 0 -> 100 %, 1 -> 75 %, 2 -> 50 %, 3 -> 25 %, 4 -> 0 % ;
- la similarité par genre vient d'une similarité cosinus entre les profils de genres des deux lecteurs ;
- similarité globale = 0,6 x notes + 0,4 x genres ;
- en dessous de 5 livres communs, F2 refuse la comparaison.

### Exemple principal : `python f2.py 20000 22534`

```
common_books         : 28
similarity           : 87.05
rating_similarity    : 79.46
genre_similarity     : 98.42
```

28 livres communs (le maximum pour le lecteur 20000). Calcul : 0,6 x 79,46 + 0,4 x 98,42 = 47,68 + 39,37 = **87,05**.
Ces deux lecteurs sont stockés sur des shards différents dans le déploiement distribué (20000 sur shard2RS, 22534 sur shard1RS), ce qui en fait aussi un bon exemple pour la Part II.

### Juste au-dessus du seuil : `python f2.py 20000 20001`

```
common_books         : 5
similarity           : 83.5
rating_similarity    : 75.0
genre_similarity     : 96.24
```

### Trop peu de livres communs : `python f2.py 20000 20005`

```
similarity           : None
message              : Pas assez de livres communs pour une comparaison pertinente.
common_books         : 4
```

Le refus est explicite, comme le demande le sujet. Autres cas : 20000 / 20003 (1 livre commun), 20000 / 20004 (0).

### Points d'attention F2 (par rapport au sujet)

- **Livres justificatifs** : le sujet demande de "retourner les livres qui appuient la comparaison". La sortie actuelle ne donne que le nombre de livres communs, pas leur liste ni les notes de chacun.
- **Accords et désaccords** : ils ne sont pas listés séparément. Le sujet parle de "similarités et désaccords significatifs".
- **Livres très populaires** : ils comptent autant que les autres dans la similarité des notes. Le sujet demande d'en tenir compte.
- **Échelle des genres** : la similarité par genre vaut (cosinus + 1) / 2. Les profils de genres ne sont jamais négatifs, donc le cosinus est entre 0 et 1, et ce score reste entre **50 et 100**. Les 96 à 98 % observés exagèrent la ressemblance.

---

## F3 : recommander des livres à un lecteur

Méthode : on prend les 10 livres les mieux notés du lecteur (note >= 4), appelés **graines**, et on envoie chacun à F1. Chaque livre similaire non lu gagne 1 point par graine qui le propose.

### Exemple principal : `python f3.py 20000` (91 notes, environ 9 s)

Graines : *Unbroken*, *Let's Pretend This Never Happened*, *Cat's Cradle*, *The Red Tent*, *Cannery Row*, *The Handmaid's Tale*, *This Is How You Lose Her*, *Rules of Civility*, *Behind the Beautiful Forevers*, *The Brief Wondrous Life of Oscar Wao* (toutes notées 5).

| # | Recommandation | Score | Explication (graine -> lecteurs communs, lift) |
|---|---|---|---|
| 1 | [190] *Wild* (Cheryl Strayed) | 3 | *Unbroken* : 78 lecteurs x4,9 ; *Let's Pretend...* : 21 x5,4 ; *Behind the Beautiful Forevers* : 15 x6,1 |
| 2 | [320] *Where'd You Go, Bernadette* | 3 | *Let's Pretend...* : 23 x6,8 ; *This Is How You Lose Her* : 12 x8,9 ; *Rules of Civility* : 26 x9,0 |
| 3 | [1009] *The Interestings* | 3 | 3 graines, lifts x9,9 à x13,5 |
| 4 | [3911] *Drown* (Junot Diaz) | 2 | même auteur que 2 graines (*This Is How You Lose Her* : 11 lecteurs x81,5 ; *Oscar Wao* : 14 x26,7) |
| 5 | [638] *A Visit from the Goon Squad* | 2 | *Rules of Civility*, *Oscar Wao* |
| 6 | [482] *The Kitchen House* | 2 | *Unbroken* (85 x5,8), *Rules of Civility* (20 x6,6) |
| 7 | [689] *Beautiful Ruins* | 2 | |
| 8 | [1810] *Furiously Happy* (Jenny Lawson) | 1 | même auteur que *Let's Pretend...* |
| 9 | [3079] *Tortilla Flat* (Steinbeck) | 1 | même auteur que *Cannery Row* |
| 10 | [405] *Breakfast of Champions* (Vonnegut) | 1 | même auteur que *Cat's Cradle*, 141 lecteurs x10,5 |

**Écart à la popularité** (exigé par le sujet) :

- 0 recommandation sur 10 parmi les 100 livres les plus notés ;
- popularité médiane des recommandations : **236 notes**, contre **2 606** pour une recommandation "par popularité" (les livres les plus notés non lus par le lecteur) et 35 pour le catalogue.

F3 ne reproduit donc pas le classement de popularité : les livres proposés sont environ 11 fois moins notés que ceux d'une recommandation par popularité.

### Historique tronqué : `python f3.py 20000 --truncate 5`

```
Lecteur 20000 (historique TRONQUÉ aux 5 premières notes du lecteur réel 20000) : 5 notes utilisées
```

- 4 graines seulement : *The Emperor of All Maladies* (5), *The Hunger Games* (4), *The Night Circus* (4), *Tai-Pan* (4).
- Recommandations : *Divergent* (score 2), *Catching Fire*, *Mockingjay*, *Noble House* et *Shogun* (même série que *Tai-Pan*), *The Help*, *Gone Girl*...
- Écart à la popularité : **5 sur 10** dans le top 100, médiane **917** notes (contre 2 642 pour la popularité).

À dire : avec peu de notes, les recommandations sont **plus populaires** (5/10 contre 0/10), parce qu'une graine très populaire comme *The Hunger Games* domine. C'est un bon point pour la discussion de E3 (historiques courts). Un lecteur sans note >= 4 n'a aucune graine, et F3 le dit au lieu de proposer les best-sellers.

Tracer une recommandation : `python f1.py 1190 --trace 320` (*Rules of Civility* -> *Where'd You Go, Bernadette*).

---

## F4 : tracer une connexion entre deux livres

Règles affichées à chaque exécution :

- les 100 livres les plus aimés (>= 660 lecteurs) sont des **carrefours**, interdits comme intermédiaires ;
- les 499 lecteurs les plus actifs (5 %, >= 100 livres aimés) sont ignorés ;
- une étape est **convaincante** si elle a au moins 3 lecteurs communs et un lift d'au moins 1,5 ;
- la force d'une étape est sa similarité cosinus entre 0 et 1. Un chemin vaut son étape la plus faible ;
- un chemin est solide si son étape la plus faible a au moins 10 lecteurs, moyen à partir de 5, fragile en dessous.

### Exemple principal : `python f4.py 1 5000 --limit 3`

*The Hunger Games* -> *Passion Unleashed* (Demonica #3, romance paranormale). 1 023 voisins convaincants pour [1], 197 pour [5000]. **29 chemins à 2 étapes** trouvés.

1. **[1] -> [1070] *Darkfever* (Fever #1) -> [5000]**, score 0,094, *moyen*
   - [1] -> [1070] : 63 lecteurs communs, x1,5, force 0,101 (ex. user 20180 : 5 / 5)
   - [1070] -> [5000] : 6 lecteurs communs, x13,9, force 0,094 ; tags communs "paranormal-urban-fantasy", "romance-paranormal"
2. [1] -> [384] *Frostbite* (Vampire Academy #2) -> [5000], score 0,079 (7 lecteurs sur l'étape faible)
3. [1] -> [491] *Untamed* (House of Night #4) -> [5000], score 0,079

1 autre chemin a été écarté car il passait par un livre carrefour.

À dire : les chemins passent par des séries young adult ou paranormales, ce qui est cohérent. Les tags communs sont affichés **à titre de contexte**, mais ne comptent pas dans le score.

### Connexion fragile : `python f4.py 9000 4000 --limit 3`

*Pretend You Don't See Her* (Mary Higgins Clark, thriller) -> *The Kings of Clonmel* (Ranger's Apprentice #8, fantasy jeunesse). Seulement 9 voisins pour [9000]. 4 chemins, **tous à 3 étapes et fragiles** (3 lecteurs sur l'étape la plus faible) :

1. [9000] -> [5801] *While My Pretty One Sleeps* (même auteur, 3 lecteurs, x633) -> [219] *Chronicles of Narnia* (3 lecteurs, x3,3) -> [4000] (4 lecteurs, x2,7), score 0,033

À dire : les trois premiers chemins commencent par les **mêmes 3 lecteurs** (20581, 21803, 28586). Ce ne sont pas des preuves indépendantes. Un lift énorme (x633) sur 3 lecteurs n'est pas une preuve forte, d'où la qualification "fragile". Avec `--max-steps 2`, il n'y a aucune connexion.

### Aucune connexion : `python f4.py 1129 1`

```
Voisins convaincants : 0 pour [1129], 1023 pour [1]
Aucune connexion convaincante trouvée.
  [1129] n'a aucun voisin convaincant : trop peu de lecteurs l'ont aimé, ou ses lecteurs
  n'aiment pas d'autres livres plus que le hasard.
```

*The History of the Hobbit, Part One* n'est aimé que par un lecteur de la sélection. F4 explique pourquoi il n'y a pas de lien au lieu d'en inventer un.

---

## F5 : lien entre deux centres d'intérêt

Méthode :

- un lecteur fait partie du **groupe** d'un tag s'il a aimé au moins 3 livres portant ce tag, et plus que la moyenne des lecteurs ;
- le lien est mesuré par le lift du recoupement des deux groupes ;
- la **similarité de Jaccard** est donnée en complément : taille de l'intersection divisée par la taille de l'union ;
- on recalcule ensuite le tout sans livres populaires ni lecteurs actifs.

### Exemple : `python f5.py fantasy romance --limit 5`

Catalogue : 4 259 livres "fantasy" [11305], 4 251 "romance" [26138], dont **2 007 portent les deux**. Ce chiffre est affiché comme contexte mais n'entre pas dans le score.

| | Groupe fantasy | Groupe romance | Dans les deux | Lift | Jaccard |
|---|---|---|---|---|---|
| Brut | 4 503 lecteurs | 4 170 | 2 276 | x1,21 | 0,36 |
| Sans les 100 livres les plus aimés ni les 5 % de lecteurs les plus actifs | 3 932 | 3 596 | 1 890 | x1,27 | 0,34 |
| Contrôle : sans les 2 007 livres portant les deux tags | | | 929 | **x0,59** | |

**Conclusion affichée : lien faible** (lift < 1,5).

À dire :

- Le grand nombre de livres portant les deux tags (2 007) pourrait faire croire à un lien fort. Les lecteurs, eux, ne se recoupent pas nettement plus que le hasard (x1,21).
- Une fois retirés les livres classés dans les deux thèmes, le recoupement tombe **sous le hasard** (x0,59). Le peu de lien qui existe vient des livres à double étiquette, pas de lecteurs qui aimeraient les deux genres séparément.
- **Livres pont bruts** : *The Hunger Games* (aimé par 1 822 lecteurs du groupe fantasy et 1 899 du groupe romance), puis 4 tomes de *Harry Potter*. Ce sont les best-sellers.
- **Livres pont sans livres populaires ni lecteurs actifs** : *City of Bones*, *City of Glass*, *City of Ashes* (Mortal Instruments), *Eragon*, *The Last Olympian*. **Aucun des 5 livres pont bruts ne reste** dans le top 5 : les premiers étaient là seulement parce qu'ils sont populaires.
- Le groupe qui aime les deux compte 2 276 lecteurs. Exemple : user 29072 a aimé 113 livres fantasy et 108 romance (dont *Twilight* et *The Iron King*).

---

## F6 : mise à jour de l'activité des lecteurs

Exécuté dans une base de test (`bookscope_f6test`, importée à l'identique puis supprimée), pour ne pas modifier la base `bookscope` qui sert aux autres exemples. Les mêmes résultats avaient déjà été obtenus sur le cluster, avec les deux placements (voir [DistributedPrototype.md](DistributedPrototype.md)).

- Commande : `python f6.py` (lot `data/processed/ratings_update_F6.csv`, 112 863 notes, prises après les 80 % de la base initiale dans l'ordre chronologique).
- Identité d'une note : le couple **(user_id, book_id)**, garanti par un index unique. Chaque note du lot est écrite par un **upsert**, une opération qui met à jour la note si le couple existe déjà et l'insère sinon.
- Politique en cas de conflit (même couple, note différente) : la note du lot **remplace** l'ancienne, parce qu'elle est la plus récente. Le cas ne se produit pas dans les données réelles : le sujet précise qu'il n'y a pas de doublon contradictoire.

### Premier passage : `python f6.py`

```
--- Résultat de l'import F6 ---
Nouvelles notes insérées : 112863
Notes déjà identiques : 0
Notes modifiées : 0
```

La base passe de **903 033 à 1 015 896 notes**. Le lecteur 20000 passe de 91 à 103 notes.

### Second passage, même lot : `python f6.py`

```
--- Résultat de l'import F6 ---
Nouvelles notes insérées : 0
Notes déjà identiques : 112863
Notes modifiées : 0
```

Toujours 1 015 896 notes : rejouer le lot ne change rien, et aucun doublon n'est créé.

### Les services reflètent la mise à jour : `python f3.py 20000` avant et après

Les 12 notes du lot pour le lecteur 20000 incluent quatre notes de 5 : *Rebecca* [270], *The Orphan Master's Son* [1462], *Life After Life* [551], *The Phantom Tollbooth* [561]. Elles incluent aussi **un 3 pour *Where'd You Go, Bernadette* [320]**, qui était la 2e recommandation de F3 avant la mise à jour.

| | Avant F6 | Après F6 |
|---|---|---|
| Notes utilisées | 91 | 103 |
| Graines nouvelles | - | *Rebecca*, *The Orphan Master's Son*, *Life After Life*, *The Phantom Tollbooth* |
| Graines sorties | - | *This Is How You Lose Her*, *Rules of Civility*, *Behind the Beautiful Forevers*, *Oscar Wao* |
| 1re recommandation | *Wild* (score 3) | *All the Light We Cannot See* (score 3 : *Orphan Master's Son*, *Life After Life*, *Unbroken*) |
| Nouvelles recommandations | - | *The Goldfinch*, *The Kite Runner*, *A God in Ruins* (même auteur que *Life After Life*), *Oryx and Crake* (même auteur que *The Handmaid's Tale*) |
| *Where'd You Go, Bernadette* | 2e | **disparue** : le lecteur l'a notée, elle n'est plus proposée |
| Livres dans le top 100 des plus notés | 0/10 | 1/10 |
| Popularité médiane des recommandations | 236 notes | 402 notes |

À dire :

- Les nouvelles notes changent les graines, donc les recommandations.
- Un livre recommandé puis noté par le lecteur sort de la liste.
- Les nombres de lecteurs communs augmentent aussi, puisque les autres lecteurs ont reçu leurs notes : *Cat's Cradle* -> *Breakfast of Champions* passe de 141 à 155 lecteurs communs.
- *Where'd You Go, Bernadette* est un petit exemple pour E3 : F3 l'avait recommandé, le lecteur l'a vraiment lu ensuite, mais ne lui a donné que 3.

### Avec une instance arrêtée (cluster)

Si un shard est arrêté, F6 échoue explicitement **avant toute écriture** (code de retour 2), au lieu d'appliquer la moitié du lot (voir Part II).
