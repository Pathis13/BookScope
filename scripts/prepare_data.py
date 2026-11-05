import pandas as pd

from config import (MAX_USER_ID, MIN_USER_ID, ORIGINAL, PROCESSED, RATINGS_EVALUATION_E3,
                    RATINGS_INITIAL, RATINGS_UPDATE_F6, SPLIT_F6, SPLIT_INITIAL, TO_READ_FILTERED)

# 1. Configuration
PROCESSED.mkdir(parents=True, exist_ok=True)

# 2. Chargement et sélection des utilisateurs (MIN_USER_ID à MAX_USER_ID)
print("Chargement et filtrage de ratings.csv...")
ratings = pd.read_csv(ORIGINAL / "ratings.csv")
selected = ratings[ratings["user_id"].between(MIN_USER_ID, MAX_USER_ID)].copy()

print(f"Notes sélectionnées : {len(selected)} | Utilisateurs : {selected['user_id'].nunique()}")

# 3. Séparation chronologique 80 / 10 / 10
# 'pos' est l'index de la note pour le lecteur, 'total' est son nombre total de notes
group = selected.groupby("user_id", sort=False)
pos = group.cumcount()
total = group["user_id"].transform("size")

# Calcul direct des seuils par note
lim_base = (total * SPLIT_INITIAL).round()
lim_f6 = (total * SPLIT_F6).round()

# Distribution dans les trois ensembles
initial = selected[pos < lim_base].copy()
update_f6 = selected[(pos >= lim_base) & (pos < lim_f6)].copy()
evaluation_e3 = selected[pos >= lim_f6].copy()

# 4. Filtrage de to_read.csv
print("\nFiltrage de to_read.csv...")
to_read = pd.read_csv(ORIGINAL / "to_read.csv")
to_read_filtered = to_read[to_read["user_id"].between(MIN_USER_ID, MAX_USER_ID)]

# 5. Vérifications et affichage des statistiques
print("\n--- Vérification des ensembles ---")
print(f"Base initiale : {len(initial)} lignes")
print(f"Lot F6        : {len(update_f6)} lignes")
print(f"Lot E3        : {len(evaluation_e3)} lignes")

# Sécurité : On s'assure que le compte est bon
assert len(initial) + len(update_f6) + len(evaluation_e3) == len(selected), "Erreur de répartition !"

for name, df in [("Initial", initial), ("F6", update_f6), ("E3", evaluation_e3)]:
    print(f"{name} : {df['user_id'].nunique()} utilisateurs, {df['book_id'].nunique()} livres")

# 6. Export des fichiers
initial.to_csv(RATINGS_INITIAL, index=False)
update_f6.to_csv(RATINGS_UPDATE_F6, index=False)
evaluation_e3.to_csv(RATINGS_EVALUATION_E3, index=False)
to_read_filtered.to_csv(TO_READ_FILTERED, index=False)

print(f"\nPréparation terminée ! Fichiers créés dans : {PROCESSED}")
