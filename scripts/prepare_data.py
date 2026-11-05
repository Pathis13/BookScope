from pathlib import Path
import pandas as pd

# 1. Configuration
DATASET = Path("../data/goodbooks-10k")
OUTPUT = Path("../data/processed")
OUTPUT.mkdir(parents=True, exist_ok=True)

# 2. Chargement et sélection des utilisateurs (20000 à 29999)
print("Chargement et filtrage de ratings.csv...")
ratings = pd.read_csv(DATASET / "ratings.csv")
selected = ratings[ratings["user_id"].between(20000, 29999)].copy()

print(f"Notes sélectionnées : {len(selected)} | Utilisateurs : {selected['user_id'].nunique()}")

# 3. Séparation chronologique 80 / 10 / 10
# 'pos' est l'index de la note pour le lecteur, 'total' est son nombre total de notes
group = selected.groupby("user_id", sort=False)
pos = group.cumcount()
total = group["user_id"].transform("size")

# Calcul direct des seuils par note
lim_base = (total * 0.80).round()
lim_f6 = (total * 0.90).round()

# Distribution dans les trois ensembles
initial = selected[pos < lim_base].copy()
update_f6 = selected[(pos >= lim_base) & (pos < lim_f6)].copy()
evaluation_e3 = selected[pos >= lim_f6].copy()

# 4. Filtrage de to_read.csv
print("\nFiltrage de to_read.csv...")
to_read = pd.read_csv(DATASET / "to_read.csv")
to_read_filtered = to_read[to_read["user_id"].between(20000, 29999)]

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
initial.to_csv(OUTPUT / "ratings_initial.csv", index=False)
update_f6.to_csv(OUTPUT / "ratings_update_F6.csv", index=False)
evaluation_e3.to_csv(OUTPUT / "ratings_evaluation_E3.csv", index=False)
to_read_filtered.to_csv(OUTPUT / "to_read_filtered.csv", index=False)

print(f"\nPréparation terminée ! Fichiers créés dans : {OUTPUT}")
