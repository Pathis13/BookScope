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


## Info des données

ratings : 
- garde les utilsateurs de 20k à 30k
- pour chaque utilisateur 80% des premeières notes sont dans initial, 10% d'apres dans F6 et le restant dans E3

to_read : garde les utilsateurs de 20k à 30k