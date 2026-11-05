import pandas as pd

from pymongo import MongoClient
from pymongo.errors import BulkWriteError

from config import DATABASE_NAME, MONGO_TIMEOUT_MS, MONGO_URI, ORIGINAL, RATINGS_INITIAL, TO_READ_FILTERED


def import_csv(collection_name, filename):

    df = pd.read_csv(filename)
    data = df.to_dict(orient="records")

    if data:
        try:
            result = db[collection_name].insert_many(data, ordered=False)
            nb_insérés = len(result.inserted_ids)
        except BulkWriteError as e:
            nb_insérés = e.details['nInserted']

        print(f"{collection_name} : {nb_insérés} documents insérés avec succès")
    else:
        print(f"{collection_name} : Le fichier CSV est vide.")


client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)

try:
    client.admin.command("ping")

    print("Connexion à MongoDB réussie")

    db = client[DATABASE_NAME]

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

    import_csv("ratings", RATINGS_INITIAL)
    import_csv("to_read", TO_READ_FILTERED)
    import_csv("books", ORIGINAL / "books.csv")
    import_csv("book_tags", ORIGINAL / "book_tags.csv")
    import_csv("tags", ORIGINAL / "tags.csv")

    print("\nImport terminé avec succès")

except Exception as error:
    print("Une erreur est survenue :", error)

finally:
    client.close()