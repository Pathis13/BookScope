## Initialisation des données

- Télécharger goodbooks-10k et l'extraire dans data/


```bash
python -m pip install -r requirements.txt

cd scripts
python prepare_data.py
python import_data.py
```

Vérifier dans MongoDB Compass que la base de données bookscope est dans la connexion `mongodb://localhost:27017/`


## Info des données

ratings : 
- garde les utilsateurs de 20k à 30k
- pour chaque utilisateur 80% des premeières notes sont dans initial, 10% d'apres dans F6 et le restant dans E3

to_read : garde les utilsateurs de 20k à 30k