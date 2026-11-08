# F6 : importer le fichier d'update et afficher les résultats de l'import.
#
# Les notes présentes dans l'update sont ajoutées à la collection, si une note d'un utilisateur est déjà présente pour
# un livre, elle est alors remplacée par la nouvelle.
#
# Usage (depuis scripts/) :
#   (S'assurer d'avoir lancé prepare_data.py auparavant pour récupérer l'update batch)
#   python f6.py

import sys

import pandas as pd

from bson import ObjectId
from pymongo import MongoClient, UpdateOne

import availability
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
        # _id créé ici, dans l'ordre du fichier : F3 trie l'historique sur _id. Créé par le serveur, il le serait
        # par chaque shard et l'ordre serait perdu sur un cluster réparti par livre (docs/Pieges.md, point 4).
        operations.append(UpdateOne(identity, {"$set": document, "$setOnInsert": {"_id": ObjectId()}},
                                    upsert=True))

    # ordered=False : sur le cluster, mongos regroupe les écritures par shard (ordered=True : ~80 notes/s).
    # Sans risque ici : prepare_data.py refuse les doublons (user_id, book_id), les opérations sont indépendantes.
    result = db.ratings.bulk_write(operations, ordered=False)

    print("\n--- Résultat de l'import F6 ---")
    print(f"Nouvelles notes insérées : {result.upserted_count}")
    # matched_count compte toutes les notes déjà présentes, modifiées ou non
    print(f"Notes déjà identiques : {result.matched_count - result.modified_count}")
    print(f"Notes modifiées : {result.modified_count}")

    print("\nImport F6 terminé.")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)

    try:
        client.admin.command("ping")

        print("Connexion à MongoDB réussie")

        db = client[DATABASE_NAME]

        # Cluster (Part II) : le lot touche des lecteurs de tous les shards. Si un shard est injoignable, on
        # n'écrit rien plutôt que d'appliquer le lot à moitié.
        if not availability.check(db, "F6", needs_all=True):
            sys.exit(2)
        ok, _ = availability.run_or_fail("F6", lambda: import_ratings_update(db, RATINGS_UPDATE_F6))
        if not ok:
            print("  Le lot a pu être appliqué en partie : relancer f6.py une fois l'instance revenue "
                  "(les notes déjà écrites sont simplement retrouvées).")
            sys.exit(2)

        print("\nImport terminé avec succès")

    except Exception as error:
        print("Une erreur est survenue :", error)

    finally:
        client.close()

if __name__ == "__main__":
    main()
