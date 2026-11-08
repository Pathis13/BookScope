# Import des CSV dans MongoDB (base vide à chaque lancement : l'import est reproductible).
#
# Usage (depuis scripts/) :
#   python import_data.py                                  (déploiement local, port 27017)
#   python import_data.py --uri mongodb://localhost:27400/ (cluster distribué, Part II : notes réparties par lecteur)
#   python import_data.py --uri mongodb://localhost:27400/ --db bookscope_book --shard-key book_id
#                                                          (placement alternatif : notes réparties par livre)
#
# Sur le cluster, `ratings` et `to_read` (activité des lecteurs) sont réparties entre les shards avec une clé
# hachée, comme `users` dans le Lab 5 (T23) ; le catalogue (books, book_tags, tags) n'est pas réparti et reste
# sur le shard primaire de la base.

import argparse
import sys

import pandas as pd
from bson import ObjectId
from pymongo import MongoClient, UpdateOne

from config import DATABASE_NAME, MONGO_TIMEOUT_MS, MONGO_URI, ORIGINAL, RATINGS_INITIAL, TO_READ_FILTERED, BOOK_TAGS_FILTERED


def shard_activity(client, db, key):
    """Répartit ratings et to_read avec une clé hachée (Lab 5 : sh.enableSharding + sh.shardCollection)."""
    client.admin.command("enableSharding", db.name)
    for name in ("ratings", "to_read"):
        db.create_collection(name)
        client.admin.command("shardCollection", f"{db.name}.{name}", key={key: "hashed"})
        print(f"{name} : répartie sur les shards, clé {{{key}: 'hashed'}}")


def create_indexes(db, pair):
    """Index de toutes les collections ; `pair` est l'ordre de l'index unique (lecteur, livre)."""
    print("\nCréation des index...")

    db.ratings.create_index(pair, unique=True)
    db.ratings.create_index("book_id")
    # Index couvrants (alternative retenue après mesure, voir docs/LocalPrototype.md et bench.py) :
    # "lecteurs ayant aimé un livre" et "livres aimés par des lecteurs" sans lire les documents
    db.ratings.create_index([("book_id", 1), ("rating", 1), ("user_id", 1)], name="book_rating_user")
    db.ratings.create_index([("user_id", 1), ("rating", 1), ("book_id", 1)], name="user_rating_book")

    db.to_read.create_index(pair, unique=True)

    db.books.create_index("book_id", unique=True)
    db.books.create_index("goodreads_book_id")
    db.books.create_index("title")

    db.book_tags.create_index([("goodreads_book_id", 1), ("tag_id", 1)], unique=True)
    db.book_tags.create_index("tag_id")

    db.tags.create_index("tag_id", unique=True)

    print("Index créés")


def import_csv(db, collection_name, filename, identity_fields):

    print(f"\nImport de {collection_name}...")
    df = pd.read_csv(filename)

    if df.empty:
        print(f"{collection_name} : fichier CSV vide.")
        return

    data = df.to_dict(orient="records")

    # Générer les ObjectId côté client, dans l'ordre du CSV
    for document in data:
        document["_id"] = ObjectId()

    result = db[collection_name].insert_many(data, ordered=False)

    print(f"{collection_name} : {len(result.inserted_ids)} documents insérés.")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Import des CSV dans MongoDB")
    parser.add_argument("--uri", default=MONGO_URI, help="URI MongoDB (mongos pour le cluster)")
    parser.add_argument("--db", default=DATABASE_NAME, help="nom de la base")
    parser.add_argument("--shard-key", choices=["user_id", "book_id"], default="user_id",
                        help="clé de répartition de l'activité (cluster uniquement)")
    parser.add_argument("--ratings-file", default=str(RATINGS_INITIAL), help="Fichier CSV des notes à importer")
    args = parser.parse_args()

    client = MongoClient(args.uri, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)

    try:
        client.admin.command("ping")
        cluster = client.admin.command("hello").get("msg") == "isdbgrid"  # connecté à un mongos ?
        print(f"Connexion à MongoDB réussie ({'cluster via mongos' if cluster else 'serveur unique'}), "
              f"base {args.db}")

        db = client[args.db]

        # Repartir d'une base vide : relancer l'import donne toujours le même état
        for name in ("ratings", "to_read", "books", "book_tags", "tags"):
            db[name].drop()
        print("Collections existantes supprimées")

        if cluster:
            shard_activity(client, db, args.shard_key)

        # Sur un cluster, un index unique doit commencer par la clé de répartition : l'identité d'une note
        # (lecteur, livre) est donc indexée dans l'ordre (book_id, user_id) quand on répartit par livre.
        pair = [("book_id", 1), ("user_id", 1)] if cluster and args.shard_key == "book_id" \
            else [("user_id", 1), ("book_id", 1)]
        create_indexes(db, pair)

        import_csv(db, "ratings", args.ratings_file, ["user_id", "book_id"])
        import_csv(db, "to_read", TO_READ_FILTERED, ["user_id", "book_id"])
        import_csv(db, "books", ORIGINAL / "books.csv", ["book_id"])
        import_csv(db, "book_tags", BOOK_TAGS_FILTERED, ["goodreads_book_id", "tag_id"])
        import_csv(db, "tags", ORIGINAL / "tags.csv", ["tag_id"])

        print("\nImport terminé avec succès")

    except Exception as error:
        print("Une erreur est survenue :", error)

    finally:
        client.close()


if __name__ == "__main__":
    main()
