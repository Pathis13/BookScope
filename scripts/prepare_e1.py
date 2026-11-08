import pandas as pd

from config import RATINGS_INITIAL, PROCESSED


def main():
    print("Lecture de la base initiale...")
    ratings = pd.read_csv(RATINGS_INITIAL)

    total = len(ratings)
    print(f"Base initiale : {total:,} notes")

    if total < 500_000:
        raise ValueError("La base contient moins de 500 000 notes.")

    PROCESSED.mkdir(parents=True, exist_ok=True)

    # Tirage aléatoire reproductible de 500 000 notes.
    # sort_index() conserve l'ordre original des lignes,
    # important pour l'ordre chronologique utilisé par F3.
    medium = ratings.sample(n=500_000, random_state=1).sort_index()

    # Les 250 000 notes sont prises dans les 500 000.
    small = medium.sample(n=250_000, random_state=1).sort_index()

    datasets = [("250k", small), ("500k", medium), ("full", ratings)]

    print("\n--- Jeux E1 ---")

    for name, df in datasets:
        output = PROCESSED / f"ratings_e1_{name}.csv"
        df.to_csv(output, index=False)

        print(f"\n{name} :")
        print(f"  Notes      : {len(df):,}")
        print(f"  Utilisateurs : {df['user_id'].nunique():,}")
        print(f"  Livres       : {df['book_id'].nunique():,}")
        print(f"  Fichier      : {output}")

    print("\nPréparation E1 terminée.")


if __name__ == "__main__":
    main()
