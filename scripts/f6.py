# F6 : importer le fichier d'update et afficher les résultats de l'import.
#
# Les notes présentes dans l'update sont ajoutées à la collection, si une note d'un utilisateur est déjà présente pour
# un livre, elle est alors remplacée par la nouvelle.
#
# Usage (depuis scripts/) :
#   (S'assurer d'avoir lancé prepare_data.py auparavant pour récupérer l'update batch)
#   python f6.py

import pandas as pd

from pymongo import MongoClient, UpdateOne

from config import DATABASE_NAME, MONGO_TIMEOUT_MS, MONGO_URI, RATINGS_UPDATE_F6


def import_ratings_update(db, filename):

    print("\nImport du lot de ratings F6...")
    df = pd.read_csv(filename)

    if df.empty:
        print("Le fichier F6 est vide.")
        return

    data = df.to_dict(orient="records")

    operations = []

    # Vérification de chaque note existante
    for document in data:
        identity = {"user_id": document["user_id"], "book_id": document["book_id"]}
        operations.append(UpdateOne(identity, {"$set": document}, upsert=True))

    if operations:
        result = db.ratings.bulk_write(operations, ordered=True)

    print("\n--- Résultat de l'import F6 ---")
    print(f"Nouvelles notes insérées : {result.upserted_count}")
    print(f"Notes déjà identiques : {result.matched_count}")
    print(f"Notes modifiées : {result.modified_count}")

    print("\nImport F6 terminé.")


def main():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)

    try:
        client.admin.command("ping")

        print("Connexion à MongoDB réussie")

        db = client[DATABASE_NAME]

        import_ratings_update(db,RATINGS_UPDATE_F6)

        print("\nImport terminé avec succès")

    except Exception as error:
        print("Une erreur est survenue :", error)

    finally:
        client.close()

if __name__ == "__main__":
    main()
