

Le but de ce document est de partager notre réflexion sur les différentes features et comment construire les requêtes pour y répondre.


## F1
Entrée : id livre / Nom livre
Sortie : Liste de livres similaires à l'entrée
On veut : Similarité entre livres basé sur informations catalogues, et patterns lecteurs

### Première Idée
Titre - Commencer par verifier si le livre a d'autre tomes associés (Suite = Similarité max --> En tête de suggestion)

Tags - Plus il y a de tags similaires, plus le score de similarité est elevé
Problème est que certains tag aident pas pour la similarité de livres, donc :
Soit on fixe une sorte de liste de tags qu'on juge "valable" pour la recherche de similarité
Soit on les pondères en fonction de leur rareté pour éviter que les tags comme "to_read" ne fausse l'interet

Lecteurs -
Soit on prends un nombre aléatoire de users, on regarde le rating qu'ils ont mis au livre, et on prends les livres à laquelle ils ont mis la même note et qui ont minimum X tags en commun
Soit on prends uniquement des users qui ont mis 5 étoiles, et on recup les autres livres qu'il a noté 5 étoiles qui ont des tags similaires 

Au final plus un livre sera cité dans les lecteurs plus il aura un score haut

Auteur / Année de sortie ajouterai un bonus de similarité (assez gros pour l'auteur et moyen pour l'année de sortie) ?

### Implémentation

**Recherche du livre** : si l'entrée ne contient que des chiffres, c'est un `book_id`. Sinon on cherche le texte dans `title` et `original_title`.
Si plusieurs livres correspondent, on les liste tous avec leur `book_id` au lieu d'en choisir un. On affiche ensuite la fiche : auteurs, année, note Goodreads, nombre de notes et moyenne dans notre base, série, tags principaux.

**Titre / série** --> `parse_series`, `series_books`
Dans `books.csv` la série est écrite dans le titre : `Catching Fire (The Hunger Games, #2)`. On lit le nom de la série entre parenthèses (un titre peut en citer plusieurs, séparées par `;`), puis on cherche les livres dont le titre contient `(<série>, #`. Les autres tomes sont classés en tête.

**Tags** --> `tag_profile`, `tag_candidates`
Des deux options, on a gardé la pondération par rareté, qui ne demande pas de choisir les tags à la main :
- pour chaque tag du livre on compte `df`, le nombre de livres qui le portent, et on calcule `idf = log(N / df)` (N = 10 000 livres). `to-read`, porté par 99,8 % des livres, a un idf proche de 0,un tag rare a un idf élevé
- un tag porté par plus de 50 % des livres (`COMMON_TAG_RATIO`) est classé « très fréquent » (to-read, favorites, owned, kindle, fiction…). Il ne sert pas à trouver des livres : il est seulement affiché comme contexte
- `score_tags` = somme des idf des tags communs / somme des idf des tags du livre. C'est la part du poids thématique du livre qu'on retrouve dans l'autre livre, entre 0 et 1.

**Lecteurs** --> `reader_candidates`
On a suivi la deuxième option (lecteurs qui ont aimé le livre), avec deux changements :
- « aimé » veut dire une note ≥ 4 (`LIKE_MIN_RATING`), pas seulement 5. Avec 5 étoiles seulement, un livre peu populaire n'a presque pas de lecteurs dans notre sélection
- le filtre « avec des tags similaires » n'est pas appliqué ici. Les tags ont déjà leur propre score, et le sujet demande de distinguer un lien par les lecteurs d'un lien par les tags. Les deux scores sont donc calculés séparément.

Une agrégation MongoDB sur `ratings` prend les lecteurs qui ont aimé le livre, regroupe les autres livres qu'ils ont aimés et compte `co`, le nombre de lecteurs communs. Les livres avec moins de 3 lecteurs communs (`MIN_SHARED_READERS`) sont ignorés, pour éviter le bruit.

Afin d'éviter l'affichage de best sellers pour chaque livres :
- **lecteurs très actifs** : chaque lecteur compte pour `1 / sqrt(nb de livres qu'il a aimés)` au lieu de 1
- **livres très populaires** : `score_lecteurs = somme des poids / sqrt(nA × nB)`, où nA et nB sont les nombres de lecteurs qui ont aimé chaque livre (similarité cosinus). Le score est ensuite ramené entre 0 et 1 en le divisant par le meilleur score des candidats

On calcule aussi le **lift** = `co / (nA × nB / nb_lecteurs)` : combien de fois il y a plus de lecteurs communs que si les deux livres étaient aimés au hasard. Il sert de preuve affichée : x1 veut dire que le lien s'explique par la popularité des livres.

**Auteur / année** --> `author_books`, `year_bonus`
Les deux bonus prévus sont gardés. Même auteur (au moins un nom commun dans `authors`) : +0,3. Année proche : +0,1 si c'est la même année, de moins en moins jusqu'à 0 au-delà de 5 ans d'écart. Les livres du même auteur font partie des candidats même s'ils n'ont ni tag ni lecteur en commun.

**Score final et classement** --> `similar_books`
`score = 0,5 × score_tags + 0,5 × score_lecteurs + bonus_auteur + bonus_année`
Tri : tomes de la même série d'abord, puis score décroissant, puis `book_id` en cas d'égalité, pour que l'ordre soit toujours le même (utile pour comparer avec la version distribuée).

**Preuves et nature du lien** --> `add_evidence`, `--trace`
Chaque résultat affiche le détail du score, quelques lecteurs communs avec leurs deux notes, les tags spécifiques communs (avec le nombre de livres qui les portent) et, à part, les tags très fréquents communs. Il reçoit aussi une **nature du lien** :
- `même série`, `même auteur` ;
- `lectorat partagé` : au moins 3 lecteurs communs et un lift > 1 ;
- `lecteurs communs mais pas plus que le hasard` : des lecteurs communs, mais seulement parce que les deux livres sont populaires ;
- `thème spécifique` : au moins un tag commun qui n'est pas très fréquent ;
- `uniquement des tags très fréquents` : rien d'autre que des tags comme to-read ou favorites.

`--trace <book_id>` liste tous les enregistrements qui relient les deux livres : chaque lecteur avec ses deux notes (`ratings`) et chaque tag commun avec son `count` sur chaque livre (`book_tags`).


## F2
Entrée : id user1, id user2
Sortie : Score de similarité / Preuves / Metriques d'explication
On veut : Déterminer la similarité entre deux utilisateurs par leurs notes

### Première Idée
Commencer par recup l'historique des deux users
Se base surtout sur les evolutions communes donc ne garder que les livres lus par les deux

Fixer N livre minimum en commun pour pouvoir évaluer similarité.

Tant que la note a un score de 1 (ou moins) d'écart on note un accord, sinon désaccord.

Le score de similarité entre deux utilisateur serait donc genre le % d'accord = nb accord / nb livres communs 

### Plus poussé ?
Comparer même les livres pas en commun ? Donc comparer par exemple les tags les plus lus, la quantité de notes etc.

### Implémentation

## F3
Entrée : id user
Sortie : Liste de livres (ordonnée du plus au moins recommandable)
On veut : Recommandations des livres à un utilisateur

### Première Idée
Réutiliser F2 pour récupérer les livres les plus aimés par les lecteurs similaires ?

Réutiliser F1 mais sur la liste des livres les plus aimés de l'utilisateur ?

Mélanger F1 et F2 ?

### Implémentation
On a gardé la deuxième idée : réutiliser F1 sur les livres les plus aimés du lecteur. F2 reste une piste d'amélioration.

**Historique du lecteur** --> `user_history`
Les notes du lecteur sont lues dans l'ordre d'insertion (`_id`). C'est l'ordre des lignes de `ratings.csv`, donc l'ordre chronologique. Les livres déjà notés ne sont jamais recommandés.

**Livres graines** --> `pick_seeds`
On prend les 10 livres les mieux notés du lecteur (`F3_SEEDS`), seulement ceux notés ≥ 4 (`LIKE_MIN_RATING`), les plus récents d'abord en cas d'égalité.

**Livres similaires et score** --> `recommend`
- Pour chaque graine on appelle `f1.similar_books` et on garde ses 10 livres similaires (`F3_SIMILAR_PER_SEED`). Les livres déjà notés sont exclus **avant** de couper à 10 (paramètre `exclude` ajouté à F1), sinon les autres tomes déjà lus prendraient toute la place.
- Chaque apparition donne **+1** : le score d'un livre est le nombre de graines qui le proposent. Un livre proche de plusieurs livres aimés par le lecteur remonte.
- Tri : score décroissant, puis somme des scores F1 (départage les égalités, nombreuses avec un score entier), puis `book_id` (ordre déterministe).

**Explication de chaque recommandation**
Pour chaque graine qui a proposé le livre, on affiche la raison donnée par F1 : même série, même auteur, nombre de lecteurs communs et lift, part des tags spécifiques communs. Pour remonter aux enregistrements : `python f1.py <graine> --trace <livre>`.

**Lecteurs avec peu de notes / historique tronqué**
- `--truncate k` ne garde que les k premières notes du lecteur. La sortie l'indique (« historique TRONQUÉ… du lecteur réel … ») : ce n'est pas un nouveau lecteur.
- 0 note, ou aucune note ≥ 4 : pas de graine, donc pas de recommandation, et la sortie le dit.
- peu de notes : il y a simplement moins de graines (par exemple, tronqué à 5 notes, le lecteur 20000 a 4 graines).

**Écart à la popularité** --> `popularity_stats`
Affiché à chaque exécution :
- le nombre de recommandations parmi les 100 livres les plus notés de la base ;
- la popularité médiane des recommandations (nb de notes dans la base), comparée à la médiane du catalogue et à celle d'une recommandation « par popularité » (les livres les plus notés que le lecteur n'a pas lus).

Exemple, lecteur 20000 (91 notes, 6 recommandations, environ 9 s) : 0/6 dans le top 100, médiane de 297 notes, contre 2774 pour la recommandation par popularité et 35 pour le catalogue. F1 normalise déjà par la popularité, et ça se retrouve ici. Avec un historique tronqué à 5 notes, les graines sont des best-sellers (Hunger Games…), donc les recommandations aussi (3/3 dans le top 100).

## F4
Entrée : id livre1, id livre2 (fonctionnement avec noms aussi comme F1)
Sortie : Liste de chemins reliant les deux livres (ordonnée du plus au moins convaincant) avec score et preuves pour chaque étape
On veut :  Trouver et expliquer un lien indirect entre deux livres (via des livres intermédiaires) à partir des lecteurs qui les ont aimés, sans passer par des livres/lecteurs trop populaires qui relient tout à tout

### Première Idée
Réussir à déterminer si un livre est "trop populaire" ou si un utilisateur a  "trop de livre lu"

Il me semble que le cours de mongodb contient une requete avec un système de voisins


## F5
Entrée : id tag 1, id tag 2
Sortie : - Score de lien entre les deux tags
         - Livres "pont" qui relient les deux (classés, avec preuves)
         - Groupe de lecteurs qui aiment les deux (taille, exemples)
         - Le même score recalculé sans les livres très populaires ni les lecteurs très actifs --> le lien tient ou s'effondre
On veut : Savoir si deux thèmes sont réellement liés par les lecteurs (pas juste parce que des livres portent les deux tags), et par quels livres / quels lecteurs

### Première Idée
Livres pont : avoir les deux tags ?
via lecteurs ? genre on prends les livres les mieux notés par les utilisateurs qui apprécient le tag1 et ceux qui aiment le tag2

groupe de lecteurs : avoir aimé au moins N livres d'un tag ?
aimer plus de livre d'un tag que la moyenne ?


## F6
Entrée : ratings_update_F6.csv
Sortie : Bilan de l'import : nb de notes ajoutées / inchangées (déjà présentes, même note) / modifiées (conflit, selon la politique) / rejetées (lecteur ou livre inconnu, note invalide)
On veut : Ajouter les nouvelles notes sans perdre ni dupliquer les anciennes, de façon idempotente (relancer le même lot = aucun changement), et que F1-F5 tiennent compte des nouvelles notes

### Première Idée
Verifier que les ratings sont bons (notre [1;5], id livre existe etc.)
Regarder si note est la même qu'une déjà mise par l'user sur ce livre
Sinon remplacer ?

Compter les trucs