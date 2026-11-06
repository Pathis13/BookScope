# F2 : Trouver les similarités entre deux utilisateurs

import sys

from pymongo import MongoClient

from config import DATABASE_NAME, MIN_SHARED_BOOKS, MONGO_TIMEOUT_MS, MONGO_URI

def find_common_books(db, user_id1, user_id2):
    """Renvoie pour chaque livres notés par les deux utilisateurs, son id et les notes de chaqun des deux utilisateurs."""
    pipeline = [
        {"$match": {"user_id": { "$in": [user_id1, user_id2] }}},
        {"$group": { 
            "_id": "$book_id",
            "ratings": { "$push": "$rating" }}},
        {"$match": { "ratings.1": { "$exists": "true" }}},
    ]
    return list(db.ratings.aggregate(pipeline))


def calculate_similarity(db, user1, user2):
    common_books = find_common_books(db, user1, user2)

    if len(common_books) < MIN_SHARED_BOOKS:
        return {
            "similarity": None,
            "message": "Pas assez de livres communs pour une comparaison pertinente.",
            "common_books": len(common_books),
        }

    agreements = []
    disagreements = []

    for book in common_books:
        if abs(book['ratings'][0] - book['ratings'][1]) <= 1:
            agreements.append(book['_id'])
        else:
            disagreements.append(book['_id'])

    similarity = len(agreements) / len(common_books) * 100
    return {
        "user1": user1,
        "user2": user2,
        "similarity": round(similarity, 2),
        "common_books": len(common_books),
        "agreements": agreements,
        "disagreements": disagreements,
    }

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 3:
        print('Usage : python f2.py <user_id1> <user_id2>')
        return
    try:
        # Tentative de conversion des arguments 1 et 2 en entiers
        arg1 = int(sys.argv[1])
        arg2 = int(sys.argv[2])
    except ValueError:
        # Déclenché si l'un des arguments contient des lettres, un point (ex: 3.14), etc.
        print("Erreur : L'un des arguments (ou les deux) n'est pas un entier valide.")
        return

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)
    db = client[DATABASE_NAME]

    for nom, valeur in calculate_similarity(db, arg1, arg2).items():
        print(f"{nom:<15} : {valeur}")

    client.close()

if __name__ == "__main__":
    main()
