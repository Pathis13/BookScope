import pandas as pd

from pymongo import MongoClient, UpdateOne

from config import DATABASE_NAME, MONGO_TIMEOUT_MS, MONGO_URI, ORIGINAL, RATINGS_INITIAL, TO_READ_FILTERED, BOOK_TAGS_FILTERED


def create_indexes(db):
    print("\nCréation des index...")
    
    db.ratings.create_index([("user_id", 1), ("book_id", 1)], unique=True)
    db.ratings.create_index("book_id")

    db.to_read.create_index([("user_id", 1), ("book_id", 1)], unique=True)
    
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

    operations = []

    for document in data:
        identity = {field: document[field] for field in identity_fields}
        operations.append(UpdateOne(identity, {"$setOnInsert": document}, upsert=True))

    result = db[collection_name].bulk_write(operations, ordered=True)

    print(f"{collection_name} : {result.upserted_count} documents insérés, {result.matched_count} déjà existants.")


def main():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)

    try:
        client.admin.command("ping")

        print("Connexion à MongoDB réussie")

        db = client[DATABASE_NAME]

        create_indexes(db)

        import_csv(db, "ratings", RATINGS_INITIAL, ["user_id", "book_id"])
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