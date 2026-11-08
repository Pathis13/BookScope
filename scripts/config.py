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
BOOK_TAGS_FILTERED = PROCESSED / "book_tags_filtered.csv"

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
# Liste des mots à chercher par genre
GENRES = {
    "fantasy": ["fantasy"],
    "science-fiction": ["science-fiction", "sci-fi"],
    "romance": ["romance", "romantic"],
    "mystery": ["mystery", "mysteries"],
    "thriller": ["thriller"],
    "horror": ["horror"],
    "historical": ["historical-fiction", "historical"],
    "adventure": ["adventure"],
    "young-adult": ["young-adult", "ya"],
    "dystopia": ["dystopia", "dystopian"]
}

# --- F3 : recommandations ---
# Nombre de livres les mieux notés du lecteur sur lesquels on lance F1 (livres "graines")
F3_SEEDS = 10
# Nombre de livres similaires récupérés par F1 pour chaque graine
F3_SIMILAR_PER_SEED = 10
# Nombre de recommandations affichées par défaut
F3_LIMIT = 10

# --- F4 : chemins entre deux livres ---
# Livres "carrefours" : les F4_HUB_BOOKS livres aimés par le plus de lecteurs ne servent pas d'intermédiaires
F4_HUB_BOOKS = 100
# Lecteurs très actifs : la part F4_ACTIVE_READERS des lecteurs ayant aimé le plus de livres n'est pas utilisée
F4_ACTIVE_READERS = 0.05
# Une étape (livre -> livre) est convaincante si au moins MIN_SHARED_READERS lecteurs ont aimé les deux livres
# et si ce nombre est au moins F4_MIN_LIFT fois plus grand que le hasard
F4_MIN_LIFT = 1.5
# Recherche à 3 étapes : nombre de voisins du premier livre explorés
F4_EXPAND = 20
# Nombre de chemins affichés par défaut
F4_LIMIT = 5

# --- F5 : liens entre deux centres d'intérêt (tags) ---
# Un lecteur "aime" un tag s'il a aimé au moins F5_MIN_LIKED livres portant ce tag
# ET plus de livres de ce tag que la moyenne des lecteurs
F5_MIN_LIKED = 3
# Le lien entre deux tags est fort si les deux groupes de lecteurs se recoupent F5_MIN_LIFT fois plus que le hasard
F5_MIN_LIFT = 1.5
# Nombre de livres pont affichés par défaut, et nombre d'exemples de lecteurs
F5_LIMIT = 10
F5_EXAMPLES = 3
