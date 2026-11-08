# F2 : Trouver les similarités entre deux utilisateurs

import sys
from math import sqrt
from pymongo import MongoClient

import availability

from config import DATABASE_NAME, MIN_SHARED_BOOKS, MONGO_TIMEOUT_MS, MONGO_URI, GENRES

def get_user_tags(db, user_id):
    pipeline = [
        # 1. Récupérer les notes de l'utilisateur
        {"$match": {"user_id": user_id}},
        # 2. Relier ratings.book_id à books.work_id
        {
            "$lookup": {
                "from": "books",
                "localField": "book_id",
                "foreignField": "work_id",
                "as": "book",
            }
        },
        {"$unwind": "$book"},
        # 3. Relier books.goodreads_book_id à book_tags
        {
            "$lookup": {
                "from": "book_tags",
                "localField": "book.goodreads_book_id",
                "foreignField": "goodreads_book_id",
                "as": "book_tags",
            }
        },
        {"$unwind": "$book_tags"},
        # 4. Compter les occurrences de chaque tag
        {
            "$group": {
                "_id": "$book_tags.tag_id",
                "score": {"$sum": {"$divide": [{"$subtract": ["$rating", 3]}, 2]}},
                "count": {"$sum": 1},
            }
        },
        # Récupérer le nom du tag
        {
            "$lookup": {
                "from": "tags",
                "localField": "_id",
                "foreignField": "tag_id",
                "as": "tag_info",
            }
        },
        {"$unwind": "$tag_info"},
        {"$project": {"_id": 0, "tag_name": "$tag_info.tag_name", "count": 1, "score": 1}},
    ]
    return list(db.ratings.aggregate(pipeline))

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

def group_user_tags(user_tags):
    genres = {}
    for tag in user_tags:
        tag_name = tag["tag_name"].lower()
        score = tag["score"]

        for genre, keywords in GENRES.items():

            if any(keyword in tag_name for keyword in keywords):
                genres[genre] = genres.get(genre, 0) + score

    return genres

def calculate_tags_similarity(tags1, tags2):

    all_tags = set(tags1) | set(tags2)

    dot_product = sum(tags1.get(tag, 0) * tags2.get(tag, 0) for tag in all_tags)

    norm1 = sqrt(sum(v**2 for v in tags1.values()))
    norm2 = sqrt(sum(v**2 for v in tags2.values()))

    if norm1 == 0 or norm2 == 0:
        return None

    cosine = dot_product / (norm1 * norm2)

    return (cosine + 1) / 2 * 100


def calculate_similarity(db, user1, user2):
    common_books = find_common_books(db, user1, user2)

    if len(common_books) < MIN_SHARED_BOOKS:
        return {
            "similarity": None,
            "message": "Pas assez de livres communs pour une comparaison pertinente.",
            "common_books": len(common_books),
        }

    user1_tags = group_user_tags(get_user_tags(db, user1))
    user2_tags = group_user_tags(get_user_tags(db, user2))

    rating_score = 0

    for book in common_books:
        rating_gap = abs(book['ratings'][0] - book['ratings'][1])

        book_similarity = 1 - (rating_gap / 4)
        rating_score += book_similarity * 100

    rating_similarity  = rating_score / len(common_books)

    # Comparer les tags
    tag_similarity = calculate_tags_similarity(user1_tags, user2_tags)

    # Combiner les deux scores
    similarity = rating_similarity * 0.6 + tag_similarity * 0.4

    return {
        "user1": user1,
        "user2": user2,
        "common_books": len(common_books),
        "similarity": round(similarity, 2),
        "rating_similarity": round(rating_similarity, 2),
        "tag_similarity": round(tag_similarity, 2),
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
        if (arg1 < 20000 or arg1 > 29999 or arg2 < 20000 or arg2 > 29999):
            print("Erreur : Les id doivent être compris entre 20000 et 29999.")
            return

    except ValueError:
        # Déclenché si l'un des arguments contient des lettres, un point (ex: 3.14), etc.
        print("Erreur : L'un des arguments (ou les deux) n'est pas un entier valide.")
        return

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)
    db = client[DATABASE_NAME]

    # Cluster (Part II) : refuser de répondre si les notes d'un des lecteurs sont sur une instance injoignable
    if not availability.check(db, "F2", readers=[arg1, arg2]):
        client.close()
        sys.exit(2)
    ok, result = availability.run_or_fail("F2", lambda: calculate_similarity(db, arg1, arg2))
    client.close()
    if not ok:
        sys.exit(2)

    for nom, valeur in result.items():
        print(f"{nom:<20} : {valeur}")

if __name__ == "__main__":
    main()
