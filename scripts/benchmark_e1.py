# Benchmark E1 de BookScope.
#
# Le script mesure les statistiques de la base et les latences des fonctions F1 à F5.
# Il effectue une exécution de chauffe, non comptabilisée, avant les répétitions.
#
# Usage (depuis scripts/) :
#     python benchmark_e1.py --db bookscope_e1_250k --repeats 5
#     python benchmark_e1.py --db bookscope_e1_500k --repeats 5
#     python benchmark_e1.py --db bookscope_e1_full --repeats 5

import argparse
import csv
import statistics
import time
from datetime import datetime
from pathlib import Path

from pymongo import MongoClient

import f1
import f2
import f3
import f4
import f5
from config import MONGO_URI, MONGO_TIMEOUT_MS, F1_LIMIT, F3_LIMIT, F4_LIMIT


def timed_call(function):
    start = time.perf_counter()
    result = function()
    return time.perf_counter() - start, result


def measure(name, function, repeats):
    # Une exécution de chauffe pour limiter l'effet du démarrage/cache froid.
    function()

    durations = []
    for _ in range(repeats):
        elapsed, _ = timed_call(function)
        durations.append(elapsed)

    return {
        "workload": name,
        "repeats": repeats,
        "mean_ms": statistics.mean(durations) * 1000,
        "median_ms": statistics.median(durations) * 1000,
        "stdev_ms": statistics.stdev(durations) * 1000 if len(durations) > 1 else 0,
        "min_ms": min(durations) * 1000,
        "max_ms": max(durations) * 1000,
    }


def choose_f1_book(db):
    row = next(db.ratings.aggregate([
        {"$group": {"_id": "$book_id", "n": {"$sum": 1}}},
        {"$sort": {"n": -1, "_id": 1}},
        {"$limit": 1},
    ]), None)
    if row is None:
        raise RuntimeError("Aucune note dans la collection ratings.")
    book = db.books.find_one({"book_id": row["_id"]})
    if book is None:
        raise RuntimeError(f"Livre {row['_id']} absent de books.")
    return book


def choose_f2_users(db):
    # Partir d'un lecteur actif, puis chercher un autre lecteur partageant
    # le plus de livres avec lui.
    first = next(db.ratings.aggregate([
        {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
        {"$sort": {"n": -1, "_id": 1}},
        {"$limit": 1},
    ]), None)
    if first is None:
        raise RuntimeError("Aucun lecteur dans ratings.")

    user1 = first["_id"]
    book_ids = [r["book_id"] for r in db.ratings.find(
        {"user_id": user1}, {"_id": 0, "book_id": 1}
    )]
    candidate = next(db.ratings.aggregate([
        {"$match": {"book_id": {"$in": book_ids}, "user_id": {"$ne": user1}}},
        {"$group": {"_id": "$user_id", "common": {"$sum": 1}}},
        {"$sort": {"common": -1, "_id": 1}},
        {"$limit": 1},
    ]), None)
    if candidate is None:
        raise RuntimeError("Impossible de trouver deux lecteurs partageant un livre.")
    return user1, candidate["_id"]


def choose_f3_user(db):
    row = next(db.ratings.aggregate([
        {"$group": {
            "_id": "$user_id",
            "n": {"$sum": 1},
            "liked": {"$sum": {"$cond": [{"$gte": ["$rating", 4]}, 1, 0]}},
        }},
        {"$match": {"liked": {"$gt": 0}}},
        {"$sort": {"n": -1, "_id": 1}},
        {"$limit": 1},
    ]), None)
    if row is None:
        raise RuntimeError("Aucun lecteur avec une note >= 4.")
    return row["_id"]


def choose_f4_books(db):
    rows = list(db.ratings.aggregate([
        {"$match": {"rating": {"$gte": 4}}},
        {"$group": {"_id": "$book_id", "n": {"$sum": 1}}},
        {"$sort": {"n": -1, "_id": 1}},
        {"$limit": 2},
    ]))
    if len(rows) < 2:
        raise RuntimeError("Il faut au moins deux livres notés >= 4 pour mesurer F4.")
    books = list(db.books.find(
        {"book_id": {"$in": [r["_id"] for r in rows]}}
    ))
    by_id = {b["book_id"]: b for b in books}
    if any(r["_id"] not in by_id for r in rows):
        raise RuntimeError("Un des livres choisis pour F4 est absent du catalogue.")
    return by_id[rows[0]["_id"]], by_id[rows[1]["_id"]]


def choose_f5_tags(db):
    # Deux tags parmi les plus représentés, sélectionnés de façon déterministe.
    rows = list(db.book_tags.aggregate([
        {"$group": {"_id": "$tag_id", "n": {"$sum": 1}}},
        {"$sort": {"n": -1, "_id": 1}},
        {"$limit": 20},
    ]))
    selected = []
    for row in rows:
        tag = db.tags.find_one({"tag_id": row["_id"]})
        if tag and tag.get("tag_name"):
            selected.append(tag)
        if len(selected) == 2:
            break
    if len(selected) < 2:
        raise RuntimeError("Il faut au moins deux tags valides pour mesurer F5.")
    return selected[0], selected[1]


def database_stats(db):
    stats = db.command("dbStats", scale=1024 * 1024)
    return {
        "database": db.name,
        "ratings": db.ratings.count_documents({}),
        "readers": len(db.ratings.distinct("user_id")),
        "rated_books": len(db.ratings.distinct("book_id")),
        "catalogue_books": db.books.count_documents({}),
        "data_size_mb": stats.get("dataSize", 0),
        "storage_size_mb": stats.get("storageSize", 0),
        "index_size_mb": stats.get("indexSize", 0),
        "total_size_mb": stats.get("totalSize", 0),
    }


def main():
    parser = argparse.ArgumentParser(description="Mesures E1 de BookScope")
    parser.add_argument("--db", required=True, help="Nom de la base MongoDB à mesurer")
    parser.add_argument("--uri", default=MONGO_URI, help="URI MongoDB")
    parser.add_argument("--repeats", type=int, default=5, help="Nombre de répétitions mesurées")
    parser.add_argument("--output", default="../results/e1_measurements.csv", help="CSV de sortie")
    parser.add_argument("--skip-f4", action="store_true", help="Ne pas mesurer F4 (requête potentiellement coûteuse)")
    parser.add_argument("--skip-f5", action="store_true", help="Ne pas mesurer F5 (calcul potentiellement coûteux)")
    args = parser.parse_args()

    if args.repeats < 2:
        parser.error("--repeats doit être >= 2 pour calculer la variabilité.")

    client = MongoClient(args.uri, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)
    db = client[args.db]

    try:
        # Vérifier la connexion avant de lancer les mesures.
        client.admin.command("ping")

        stats = database_stats(db)
        print("\n=== Taille de la base ===")
        for key, value in stats.items():
            print(f"{key:20s}: {value:.2f}" if isinstance(value, float) else f"{key:20s}: {value}")

        # Choix déterministe des entrées de test, une seule fois par base.
        book = choose_f1_book(db)
        user1, user2 = choose_f2_users(db)
        user3 = choose_f3_user(db)
        print("\n=== Entrées utilisées ===")
        print(f"F1 book_id : {book['book_id']} — {book.get('title', '')}")
        print(f"F2 users   : {user1}, {user2}")
        print(f"F3 user    : {user3}")

        measurements = []
        measurements.append(measure(
            "F1_similar_books",
            lambda: f1.similar_books(db, book, limit=F1_LIMIT, evidence=False),
            args.repeats,
        ))
        measurements.append(measure(
            "F2_calculate_similarity",
            lambda: f2.calculate_similarity(db, user1, user2),
            args.repeats,
        ))
        measurements.append(measure(
            "F3_recommend",
            lambda: f3.recommend(db, user3, limit=F3_LIMIT),
            args.repeats,
        ))

        if not args.skip_f4:
            book_a, book_b = choose_f4_books(db)
            # Les statistiques globales F4 sont calculées une fois puis réutilisées,
            # comme dans une session où le cache applicatif est déjà initialisé.
            f4_stats = f4.activity_stats(db)
            measurements.append(measure(
                "F4_find_paths (stats precomputed)",
                lambda: f4.find_paths(
                    db, book_a["book_id"], book_b["book_id"],
                    f4_stats, limit=F4_LIMIT, max_steps=3
                ),
                args.repeats,
            ))
            print(f"F4 books   : {book_a['book_id']}, {book_b['book_id']}")

        if not args.skip_f5:
            tag_a, tag_b = choose_f5_tags(db)
            measurements.append(measure(
                "F5_tag_link (calcul complet)",
                lambda: f5.tag_link(db, tag_a, tag_b),
                args.repeats,
            ))
            print(f"F5 tags    : {tag_a['tag_name']}, {tag_b['tag_name']}")

        print("\n=== Latences (ms) ===")
        for row in measurements:
            print(
                f"{row['workload']}: moyenne={row['mean_ms']:.2f}, "
                f"médiane={row['median_ms']:.2f}, écart-type={row['stdev_ms']:.2f}, "
                f"min={row['min_ms']:.2f}, max={row['max_ms']:.2f}"
            )

        output = Path(args.output)
        write_header = not output.exists()
        with output.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "timestamp", "database", "ratings", "readers", "rated_books",
                "catalogue_books", "data_size_mb", "storage_size_mb",
                "index_size_mb", "total_size_mb", "workload", "repeats",
                "mean_ms", "median_ms", "stdev_ms", "min_ms", "max_ms",
            ])
            if write_header:
                writer.writeheader()
            for row in measurements:
                writer.writerow({
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    **stats,
                    **row,
                })

        print(f"\nRésultats ajoutés à : {output.resolve()}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
