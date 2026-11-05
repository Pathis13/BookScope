# F1 (base) : trouver les livres similaires à un livre donné.
#
# Règle TEMPORAIRE : deux livres sont similaires s'ils ont au moins MIN_SHARED_TAGS tags en commun.
# Plus ils ont de tags en commun, plus ils sont classés haut.
#
# Usage (depuis scripts/) :
#   python f1.py 1                     (par book_id)
#   python f1.py "The Hunger Games"    (par titre exact ; si plusieurs livres ont ce titre, on les affiche)

import sys

from pymongo import MongoClient

from config import DATABASE_NAME, MIN_SHARED_TAGS, MONGO_TIMEOUT_MS, MONGO_URI


def find_books(db, query):
    """Renvoie la liste des livres correspondant à un book_id ou à un titre exact."""
    if query.isdigit():
        return list(db.books.find(
                {"book_id": int(query)}
            ))
    return list(db.books.find(
            { "$or": [{"title": query}, {"original_title": query}] }
        ))


def similar_books(db, book, limit=10):
    # Attention : book_tags utilise goodreads_book_id, pas book_id
    gr_id = book["goodreads_book_id"]

    # 1. Les tags du livre
    tag_ids = [bt["tag_id"] for bt in db.book_tags.find({"goodreads_book_id": gr_id})]

    # 2. Les autres livres qui ont au moins MIN_SHARED_TAGS de ces tags
    pipeline = [
        {"$match": {"tag_id": {"$in": tag_ids}, "goodreads_book_id": {"$ne": gr_id}}},
        {"$group": {"_id": "$goodreads_book_id",
                    "nb_shared": {"$sum": 1},
                    "shared_tag_ids": {"$push": "$tag_id"}}},
        {"$match": {"nb_shared": {"$gte": MIN_SHARED_TAGS}}},
        {"$sort": {"nb_shared": -1, "_id": 1}},  # égalités départagées par l'identifiant
        {"$limit": limit},
    ]
    results = list(db.book_tags.aggregate(pipeline))

    # 3. On récupère les titres des livres et les noms des tags pour l'affichage
    for r in results:
        r["book"] = db.books.find_one({"goodreads_book_id": r["_id"]})
        r["shared_tags"] = [t["tag_name"] for t in db.tags.find({"tag_id": {"$in": r["shared_tag_ids"]}})]
    return results


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print('Usage : python f1.py <book_id | "titre">')
        return

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)
    db = client[DATABASE_NAME]
    books = find_books(db, sys.argv[1])

    if not books:
        print("Aucun livre trouvé.")
        return
    if len(books) > 1:
        print("Plusieurs livres correspondent, relancez avec un book_id :")
        for b in books:
            print(f"  [{b['book_id']}] {b['title']} - {b['authors']}")
        return

    book = books[0]
    print(f"[{book['book_id']}] {book['title']} - {book['authors']}\n")
    print(f"Livres ayant au moins {MIN_SHARED_TAGS} tags en commun :")
    for r in similar_books(db, book):
        b = r["book"]
        print(f"  [{b['book_id']}] {b['title']} : {r['nb_shared']} tags communs")
        print(f"      {', '.join(r['shared_tags'][:10])}")

    client.close()


if __name__ == "__main__":
    main()
