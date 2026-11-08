import pandas as pd
import argparse
import random
from pymongo import MongoClient

import f3
from config import (
    DATABASE_NAME,
    MONGO_URI,
    MONGO_TIMEOUT_MS,
    RATINGS_EVALUATION_E3,
    LIKE_MIN_RATING,
    ROOT
)

# ---------------- CONFIGURATION ----------------

K = 10
SHORT_HISTORY = 5

# ---------------- METRICS ----------------


def recall_at_k(recommendations, relevant_books):
    relevant = set(relevant_books)
    recommended = set(recommendations[:K])

    if not relevant:
        return None

    return len(recommended & relevant) / len(relevant)


def precision_at_k(recommendations, relevant_books):
    relevant = set(relevant_books)
    recommended = recommendations[:K]

    if not recommended:
        return 0.0

    return sum(b in relevant for b in recommended) / K


# ---------------- POPULARITY BASELINE ----------------


def popularity_recommendations(db, user_id, truncate=None):
    history = f3.user_history(db, user_id, truncate)
    rated_books = {book_id for book_id, rating in history}

    counts = db.ratings.aggregate(
        [
            {"$group": {"_id": "$book_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1, "_id": 1}},
        ]
    )

    recommendations = []

    for book in counts:
        book_id = book["_id"]

        if book_id not in rated_books:
            recommendations.append(book_id)

        if len(recommendations) == K:
            break

    return recommendations


# ---------------- EVALUATION ----------------


def evaluate_method(recommendations, relevant_books):
    return {
        "recall": recall_at_k(recommendations, relevant_books),
        "precision": precision_at_k(recommendations, relevant_books),
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluation E3 de BookScope")
    parser.add_argument(
        "--users",
        default="20",
        help="Nombre d'utilisateurs à évaluer, ou 'all' pour tous",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=1,
        help="Graine pour rendre la sélection reproductible",
    )
    args = parser.parse_args()

    evaluation = pd.read_csv(RATINGS_EVALUATION_E3)

    required = {"user_id", "book_id", "rating"}
    if not required.issubset(evaluation.columns):
        raise ValueError("CSV must contain user_id, book_id, rating")

    # On ne garde que les notes considérées comme positives
    evaluation = evaluation[evaluation["rating"] >= LIKE_MIN_RATING]

    relevant_by_user = (
        evaluation.groupby("user_id")["book_id"].apply(lambda x: set(x)).to_dict()
    )

    # Sélection reproductible des utilisateurs
    candidates = sorted(relevant_by_user.keys())

    if args.users.lower() == "all":
        selected_users = candidates
    else:
        try:
            number_users = int(args.users)
            if number_users <= 0:
                parser.error("--users doit être positif ou égal à 'all'")
        except ValueError:
            parser.error("--users doit être un nombre ou 'all'")

        rng = random.Random(args.seed)
        selected_users = rng.sample(candidates, min(number_users, len(candidates)))

    print(f"Utilisateurs disponibles : {len(candidates)}")
    print(f"Utilisateurs sélectionnés : {len(selected_users)}")
    print(f"Graine utilisée : {args.seed}")

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)
    db = client[DATABASE_NAME]

    results = []

    try:
        for index, user_id in enumerate(selected_users, start=1):
            relevant_books = relevant_by_user[user_id]

            print(
                f"Evaluation {index}/{len(selected_users)} " f"- utilisateur {user_id}"
            )

            for history_type, truncate in [
                ("complete", None),
                ("short_5", SHORT_HISTORY),
            ]:
                # BookScope
                f3_result = f3.recommend(db, int(user_id), limit=K, truncate=truncate)

                recommendations = [
                    item["book"]["book_id"] for item in f3_result["results"]
                ]

                scores = evaluate_method(recommendations, relevant_books)

                results.append(
                    {
                        "user_id": user_id,
                        "history": history_type,
                        "method": "BookScope",
                        "recall": scores["recall"],
                        "precision": scores["precision"],
                    }
                )

                # Baseline popularité
                baseline = popularity_recommendations(db, int(user_id), truncate)

                scores = evaluate_method(baseline, relevant_books)

                results.append(
                    {
                        "user_id": user_id,
                        "history": history_type,
                        "method": "Popularity",
                        "recall": scores["recall"],
                        "precision": scores["precision"],
                    }
                )

        df = pd.DataFrame(results)
        df.to_csv(ROOT / "results" / "results_e3.csv", index=False)

        summary = df.groupby(["history", "method"])[["recall", "precision"]].mean()

        print("\nRésultats moyens :")
        print(summary)

    finally:
        client.close()


if __name__ == "__main__":
    main()
