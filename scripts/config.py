# Configuration partagée de BookScope (importée par les scripts de ce dossier)

from pathlib import Path

# --- MongoDB ---
MONGO_URI = "mongodb://localhost:27017/"
DATABASE_NAME = "bookscope"
MONGO_TIMEOUT_MS = 5000

# --- Dossiers de données (relatifs à la racine du projet, quel que soit le dossier de lancement) ---
ROOT = Path(__file__).resolve().parent.parent
ORIGINAL = ROOT / "data" / "goodbooks-10k"   # CSV Goodbooks-10k d'origine
PROCESSED = ROOT / "data" / "processed"      # CSV produits par prepare_data.py

# Fichiers produits par prepare_data.py
RATINGS_INITIAL = PROCESSED / "ratings_initial.csv"
RATINGS_UPDATE_F6 = PROCESSED / "ratings_update_F6.csv"
RATINGS_EVALUATION_E3 = PROCESSED / "ratings_evaluation_E3.csv"
TO_READ_FILTERED = PROCESSED / "to_read_filtered.csv"

# --- Sélection des données ---
# Utilisateurs gardés : user_id entre MIN_USER_ID et MAX_USER_ID (inclus)
MIN_USER_ID = 20_000
MAX_USER_ID = 29_999

# Découpage chronologique des notes de chaque lecteur :
# [0, 80 %) -> base initiale, [80 %, 90 %) -> lot F6, [90 %, 100 %] -> lot E3
SPLIT_INITIAL = 0.80
SPLIT_F6 = 0.90

# --- F1 : livres similaires ---
# Nombre minimum de tags en commun pour considérer deux livres comme similaires
MIN_SHARED_TAGS = 10

# --- F2 : similarité utilisateurs ---
# Nombre minimum de livres en commun pour pour pouvoir évaluer les similarité entre deux utilisateurs
MIN_SHARED_BOOKS = 5
