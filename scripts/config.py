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
# Un tag porté par plus de COMMON_TAG_RATIO des livres est "très fréquent" (to-read, favorites, owned...)
COMMON_TAG_RATIO = 0.5
# Note minimale pour considérer qu'un lecteur a "aimé" un livre
LIKE_MIN_RATING = 4
# Nombre minimum de lecteurs ayant aimé les deux livres pour parler de lectorat partagé
MIN_SHARED_READERS = 3
# Poids des deux composantes du score (thématique / lecteurs), et bonus catalogue
WEIGHT_TAGS = 0.5
WEIGHT_READERS = 0.5
AUTHOR_BONUS = 0.3   # même auteur : bonus "assez gros"
YEAR_BONUS = 0.1     # année de publication proche : bonus "moyen", dégressif
YEAR_WINDOW = 5      # écart maximal (en années) donnant droit au bonus d'année
# Nombre de résultats affichés par défaut
F1_LIMIT = 10

# --- F2 : similarité utilisateurs ---
# Nombre minimum de livres en commun pour pour pouvoir évaluer les similarité entre deux utilisateurs
MIN_SHARED_BOOKS = 5

# --- F3 : recommandations ---
# Nombre de livres les mieux notés du lecteur sur lesquels on lance F1 (livres "graines")
F3_SEEDS = 10
# Nombre de livres similaires récupérés par F1 pour chaque graine
F3_SIMILAR_PER_SEED = 10
# Nombre de recommandations affichées par défaut
F3_LIMIT = 10
