import pandas as pd

from config import (MAX_USER_ID, MIN_USER_ID, ORIGINAL, PROCESSED, RATINGS_EVALUATION_E3,
                     RATINGS_INITIAL, RATINGS_UPDATE_F6, SPLIT_F6, SPLIT_INITIAL, TO_READ_FILTERED)

def initialize_directories():
    """Crée le dossier de destination s'il n'existe pas."""
    PROCESSED.mkdir(parents=True, exist_ok=True)


def load_and_filter_ratings():
    """Charge ratings.csv et sélectionne les utilisateurs concernés."""

    print("Chargement et filtrage de ratings.csv...")

    ratings = pd.read_csv(ORIGINAL / "ratings.csv")

    selected = ratings[ratings["user_id"].between(MIN_USER_ID, MAX_USER_ID)].copy()

    print(f"Notes sélectionnées : {len(selected)} | Utilisateurs : {selected['user_id'].nunique()}")

    return selected


def validate_ratings(ratings):
    """Vérifie les valeurs manquantes, les notes et les doublons."""

    # Vérification des valeurs manquantes
    missing = ratings[["user_id", "book_id", "rating"]].isnull().sum()

    if missing.any():
        raise ValueError(f"Valeurs manquantes détectées :\n{missing}")

    # Vérification des notes
    if not ratings["rating"].between(1, 5).all():
        raise ValueError("Des notes sont en dehors de l'intervalle 1-5.")

    # Vérification des doublons
    duplicates = ratings[ratings.duplicated(subset=["user_id", "book_id"], keep=False)].sort_values(["user_id", "book_id"])

    if not duplicates.empty:

        grouped = duplicates.groupby(["user_id", "book_id"])["rating"].nunique()

        exact_duplicates = (grouped == 1).sum()
        conflicting_duplicates = (grouped > 1).sum()

        print("\nERREUR : doublons détectés dans ratings.csv")
        print(f"Couples dupliqués : {len(grouped)}")
        print(f"Doublons avec la même note : {exact_duplicates}")
        print(f"Doublons avec des notes différentes : " f"{conflicting_duplicates}")

        print("\nExemples :")
        print(duplicates.head(10))

        raise ValueError("Corriger les doublons avant de continuer.")

    print("Validation des ratings réussie.")


def split_ratings(ratings):
    """
    Sépare les ratings en trois ensembles :
    - Base initiale
    - Lot F6
    - Lot E3

    La séparation respecte l'ordre des lignes pour chaque utilisateur.
    """

    group = ratings.groupby("user_id", sort=False)

    pos = group.cumcount()
    total = group["user_id"].transform("size")

    lim_base = (total * SPLIT_INITIAL).round()
    lim_f6 = (total * SPLIT_F6).round()

    initial = ratings[pos < lim_base].copy()

    update_f6 = ratings[(pos >= lim_base) & (pos < lim_f6)].copy()

    evaluation_e3 = ratings[pos >= lim_f6].copy()

    return initial, update_f6, evaluation_e3


def filter_to_read():
    """Charge et filtre to_read.csv selon les utilisateurs sélectionnés."""

    print("\nFiltrage de to_read.csv...")

    to_read = pd.read_csv(ORIGINAL / "to_read.csv")

    return to_read[to_read["user_id"].between(MIN_USER_ID, MAX_USER_ID)].copy()


def clean_book_tags():
    """
    Supprime les doublons dans book_tags.csv.

    Un doublon correspond à un même couple :
    (goodreads_book_id, tag_id).

    On conserve la dernière occurrence dans le fichier original.
    """

    print("\nNettoyage de book_tags.csv...")

    book_tags = pd.read_csv(ORIGINAL / "book_tags.csv")

    initial_count = len(book_tags)

    # Vérification des doublons
    duplicates = book_tags[book_tags.duplicated(subset=["goodreads_book_id", "tag_id"], keep=False)]

    duplicate_count = book_tags.duplicated(subset=["goodreads_book_id", "tag_id"], keep="last").sum()

    print(f"Lignes initiales : {initial_count}")
    print(f"Lignes concernées par des doublons : {len(duplicates)}")
    print(f"Doublons supprimés : {duplicate_count}")

    # Conservation de la dernière occurrence
    book_tags_cleaned = book_tags.drop_duplicates(subset=["goodreads_book_id", "tag_id"], keep="last").copy()

    # Export dans processed
    output_path = PROCESSED / "book_tags_filtered.csv"

    book_tags_cleaned.to_csv(output_path, index=False)

    print(f"Lignes après nettoyage : {len(book_tags_cleaned)}")
    print(f"Fichier nettoyé enregistré : {output_path}")

    return book_tags_cleaned


def display_statistics(initial, update_f6, evaluation_e3, selected):
    """Affiche les statistiques des trois ensembles."""

    print("\n--- Vérification des ensembles ---")

    print(f"Base initiale : {len(initial)} lignes")
    print(f"Lot F6        : {len(update_f6)} lignes")
    print(f"Lot E3        : {len(evaluation_e3)} lignes")

    # Vérification de la répartition
    assert len(initial) + len(update_f6) + len(evaluation_e3) == len(selected), "Erreur de répartition !"

    for name, df in [("Initial", initial), ("F6", update_f6), ("E3", evaluation_e3)]:
        print(f"{name} : {df['user_id'].nunique()} utilisateurs, {df['book_id'].nunique()} livres")


def export_ratings(initial, update_f6, evaluation_e3, to_read):
    """Exporte les différents fichiers dans data/processed."""

    initial.to_csv(RATINGS_INITIAL, index=False)
    update_f6.to_csv(RATINGS_UPDATE_F6, index=False)
    evaluation_e3.to_csv(RATINGS_EVALUATION_E3, index=False)
    to_read.to_csv(TO_READ_FILTERED, index=False)

    print(f"\nFichiers exportés dans : {PROCESSED}")


def main():

    initialize_directories()

    ratings = load_and_filter_ratings()
    validate_ratings(ratings)
    initial, update_f6, evaluation_e3 = split_ratings(ratings)
    
    to_read = filter_to_read()
    clean_book_tags()

    display_statistics(initial, update_f6, evaluation_e3, ratings)
    export_ratings(initial, update_f6, evaluation_e3, to_read)

    print("\nPréparation terminée !")


if __name__ == "__main__":
    main()
