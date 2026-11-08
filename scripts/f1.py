# F1 : explorer un livre et ses connexions.
#
# Pour un livre donné (book_id ou titre), on affiche sa fiche catalogue puis les livres liés, classés par un
# score qui combine :
#   - la série   : un autre tome de la même série est toujours placé en tête ;
#   - les tags   : tags communs pondérés par leur rareté (IDF), les tags très fréquents ne comptent pas ;
#   - les lecteurs : lecteurs ayant aimé (note >= LIKE_MIN_RATING) les deux livres, normalisé par la popularité
#                  des deux livres et pondéré par l'activité de chaque lecteur ;
#   - des bonus  : même auteur (gros bonus) et année de publication proche (bonus moyen).
# Tout est calculé par UNE agrégation MongoDB (voir similar_books) : chaque source de liens produit des
# candidats, qu'on réunit ($unionWith), fusionne par livre ($group), puis note et trie.
#
# Usage (depuis scripts/) :
#   python f1.py 1                       (par book_id)
#   python f1.py "Hunger Games"          (par titre ; si plusieurs livres correspondent, on les affiche)
#   python f1.py 1 --limit 20            (nombre de résultats)
#   python f1.py 1 --trace 2             (liste tous les enregistrements qui relient le livre 1 au livre 2)

import argparse
import math
import re
import sys

from pymongo import MongoClient

from config import (AUTHOR_BONUS, COMMON_TAG_RATIO, DATABASE_NAME, F1_LIMIT, LIKE_MIN_RATING,
                    MIN_SHARED_READERS, MONGO_TIMEOUT_MS, MONGO_URI, WEIGHT_READERS, WEIGHT_TAGS,
                    YEAR_BONUS, YEAR_WINDOW)

LIKED = {"$gte": LIKE_MIN_RATING}

# ---------------------------------------------------------------------------------------------------------------
# Recherche du livre
# ---------------------------------------------------------------------------------------------------------------

def find_books(db, query):
    """Renvoie la liste des livres correspondant à un book_id ou à un titre."""
    if query.isdigit():
        return list(db.books.find(
                {"book_id": int(query)}
            ))
    pattern = re.escape(query)  # le titre est cherché tel quel, pas comme une expression régulière
    return list(db.books.find(
        { 
            "$or": [
                {"title": { "$regex": pattern, "$options": "i" }}, 
                {"original_title": { "$regex": pattern, "$options": "i" }}
        ]},
    ).sort("book_id", 1))

def parse_series(title):
    """'Catching Fire (The Hunger Games, #2)' -> [('The Hunger Games', '2')]. Un titre peut citer plusieurs séries (';')."""
    m = re.search(r"\(([^()]*#[^()]*)\)\s*$", str(title))
    if not m:
        return []
    parts = (re.match(r"^\s*(.+?),?\s*#\s*([\d.\-]+)", p) for p in m.group(1).split(";"))
    return [(p.group(1).strip(), p.group(2)) for p in parts if p]


def split_authors(authors):
    return [a.strip() for a in str(authors).split(",") if a.strip()]


def is_year(y):
    return isinstance(y, (int, float)) and not math.isnan(y)


def tag_profile(db, book):
    """Tags du livre : {tag_id: {"tag_name", "count" (nb de fois où le tag a été posé sur ce livre),
    "df" (nb de livres portant ce tag), "idf" = log(N / df), "common" (porté par plus de COMMON_TAG_RATIO des livres)}}."""
    n_books = db.books.count_documents({})
    pipeline = [
        # 1. Les tags posés sur le livre
        {"$match": {"goodreads_book_id": book["goodreads_book_id"]}},
        # 2. df : nombre de livres portant ce tag
        {"$lookup": {"from": "book_tags", "localField": "tag_id", "foreignField": "tag_id",
                     "pipeline": [{"$count": "n"}], "as": "df"}},
        # 3. Nom du tag
        {"$lookup": {"from": "tags", "localField": "tag_id", "foreignField": "tag_id", "as": "tag"}},
        {"$project": {"_id": 0, "tag_id": 1, "count": 1,
                      "tag_name": {"$ifNull": [{"$first": "$tag.tag_name"}, "?"]},
                      "df": {"$first": "$df.n"}}},
        # 4. Rareté du tag
        {"$set": {"idf": {"$ln": {"$divide": [n_books, "$df"]}},
                  "common": {"$gt": [{"$divide": ["$df", n_books]}, COMMON_TAG_RATIO]}}},
    ]
    return {t["tag_id"]: t for t in db.book_tags.aggregate(pipeline)}


# ---------------------------------------------------------------------------------------------------------------
# La requête : livres liés
# ---------------------------------------------------------------------------------------------------------------

def similar_books(db, book, limit=F1_LIMIT, exclude=(), evidence=True, only=None, sample=5):
    """Livres liés à `book`, classés : tomes de la même série d'abord, puis par score décroissant.

    exclude  : book_id à ne pas renvoyer (F3 y met les livres déjà notés par le lecteur)
    evidence : False pour ne pas récupérer les preuves (tags et lecteurs communs) des résultats
    only     : ne renvoyer que ce book_id (utilisé par --trace, avec le même score que dans la liste)
    sample   : nombre de lecteurs communs donnés en exemple (None = tous)
    """
    bid, gr = book["book_id"], book["goodreads_book_id"]

    # --- Quelques constantes calculées avant la requête ---
    profile = tag_profile(db, book)
    specific = [t for t, p in profile.items() if not p["common"]]        # tags qui servent à trouver des liens
    specific_idf = [profile[t]["idf"] for t in specific]
    total_idf = sum(p["idf"] for p in profile.values()) or 1
    likers = {r["user_id"]: r["rating"] for r in db.ratings.find({"book_id": bid, "rating": LIKED},
                                                                 {"_id": 0, "user_id": 1, "rating": 1})}
    n_a = len(likers) or 1                                               # lecteurs ayant aimé le livre
    n_users = len(db.ratings.distinct("user_id"))
    series = [re.escape(s) for s, _ in parse_series(book["title"])]
    authors = split_authors(book["authors"])
    year = book.get("original_publication_year")

    pipeline = [
        # ===== Source 1 : TAGS. Livres portant au moins un tag spécifique du livre =====
        {"$match": {"tag_id": {"$in": specific}, "goodreads_book_id": {"$ne": gr}}},
        {"$set": {"idf": {"$arrayElemAt": [specific_idf, {"$indexOfArray": [specific, "$tag_id"]}]}}},
        {"$group": {"_id": "$goodreads_book_id", "idf": {"$sum": "$idf"}}},
        {"$lookup": {"from": "books", "localField": "_id", "foreignField": "goodreads_book_id", "as": "b"}},
        {"$unwind": "$b"},
        # score_tags = part du poids thématique (somme des idf) du livre qu'on retrouve chez l'autre
        {"$project": {"_id": 0, "book_id": "$b.book_id", "s_tags": {"$divide": ["$idf", total_idf]}}},

        # ===== Source 2 : LECTEURS. Livres aimés par les lecteurs qui ont aimé le livre =====
        {"$unionWith": {"coll": "ratings", "pipeline": [
            {"$match": {"user_id": {"$in": list(likers)}, "rating": LIKED}},
            # un lecteur qui aime beaucoup de livres pèse moins : poids 1 / sqrt(nb de livres aimés)
            {"$group": {"_id": "$user_id", "books": {"$push": "$book_id"}}},
            {"$set": {"weight": {"$divide": [1, {"$sqrt": {"$size": "$books"}}]}}},
            {"$unwind": "$books"},
            {"$match": {"books": {"$ne": bid}}},
            {"$group": {"_id": "$books", "co": {"$sum": 1}, "weighted": {"$sum": "$weight"}}},
            {"$match": {"co": {"$gte": MIN_SHARED_READERS}}},
            # n_b : nombre de lecteurs ayant aimé le livre candidat
            {"$lookup": {"from": "ratings", "localField": "_id", "foreignField": "book_id",
                         "pipeline": [{"$match": {"rating": LIKED}}, {"$count": "n"}], "as": "n_b"}},
            {"$project": {"_id": 0, "book_id": "$_id",
                          "readers": {"co": "$co", "weighted": "$weighted", "n_b": {"$first": "$n_b.n"}}}},
        ]}},
    ]

    # ===== Source 3 : SÉRIE. Titres contenant "(<série>, #<tome>" =====
    if series:
        series_re = r"[(;]\s*(" + "|".join(series) + r"),?\s*#\s*([\d.\-]*)"
        pipeline.append({"$unionWith": {"coll": "books", "pipeline": [
            {"$match": {"title": {"$regex": series_re}, "book_id": {"$ne": bid}}},
            {"$set": {"m": {"$regexFind": {"input": "$title", "regex": series_re}}}},
            {"$project": {"_id": 0, "book_id": 1, "series": {"name": {"$arrayElemAt": ["$m.captures", 0]},
                                                             "tome": {"$arrayElemAt": ["$m.captures", 1]}}}},
        ]}})

    # ===== Source 4 : AUTEURS. Livres ayant au moins un auteur en commun =====
    if authors:
        authors_re = "|".join(r"(^|,\s*)" + re.escape(a) + r"\s*(,|$)" for a in authors)
        pipeline.append({"$unionWith": {"coll": "books", "pipeline": [
            {"$match": {"authors": {"$regex": authors_re}, "book_id": {"$ne": bid}}},
            {"$project": {"_id": 0, "book_id": 1, "authors": {"$sortArray": {"sortBy": 1, "input": {
                "$setIntersection": [authors, {"$map": {"input": {"$split": ["$authors", ","]},
                                                        "in": {"$trim": {"input": "$$this"}}}}]}}}}},
        ]}})

    pipeline += [
        # ===== Fusion : un document par livre candidat =====
        {"$group": {"_id": "$book_id", "s_tags": {"$max": "$s_tags"}, "readers": {"$max": "$readers"},
                    "series": {"$max": "$series"}, "authors": {"$max": "$authors"}}},

        # ===== Score lecteurs = somme des poids / sqrt(nA * nB), ramené entre 0 et 1 par le meilleur =====
        {"$set": {"r_score": {"$cond": [{"$ifNull": ["$readers", False]},
                                        {"$divide": ["$readers.weighted",
                                                     {"$sqrt": {"$multiply": [n_a, "$readers.n_b"]}}]}, 0]}}},
        {"$setWindowFields": {"output": {"r_max": {"$max": "$r_score",
                                                   "window": {"documents": ["unbounded", "unbounded"]}}}}},

        # ===== Exclusions, puis fiche catalogue du candidat =====
        {"$match": {"_id": {"$eq": only} if only is not None else {"$nin": list(exclude)}}},
        {"$lookup": {"from": "books", "localField": "_id", "foreignField": "book_id", "as": "book"}},
        {"$unwind": "$book"},

        # ===== Composantes du score =====
        {"$set": {
            "s_tags": {"$ifNull": ["$s_tags", 0]},
            "s_readers": {"$cond": [{"$gt": ["$r_max", 0]}, {"$divide": ["$r_score", "$r_max"]}, 0]},
            "authors": {"$ifNull": ["$authors", []]},
            "b_author": {"$cond": [{"$ifNull": ["$authors", False]}, AUTHOR_BONUS, 0]},
            # bonus dégressif : YEAR_BONUS pour la même année, 0 au-delà de YEAR_WINDOW ans d'écart
            "b_year": 0 if not is_year(year) else {"$let": {
                "vars": {"gap": {"$abs": {"$subtract": ["$book.original_publication_year", year]}}},
                "in": {"$cond": [{"$and": [{"$gte": ["$$gap", 0]}, {"$lte": ["$$gap", YEAR_WINDOW]}]},
                                 {"$multiply": [YEAR_BONUS, {"$subtract": [1, {"$divide": ["$$gap", YEAR_WINDOW + 1]}]}]},
                                 0]}}},
            # lift : combien de fois plus de lecteurs communs que le hasard
            "readers": {"$cond": [{"$ifNull": ["$readers", False]}, {"$mergeObjects": ["$readers", {
                "score": "$r_score",
                "lift": {"$divide": [{"$multiply": ["$readers.co", n_users]},
                                     {"$multiply": [n_a, "$readers.n_b"]}]}}]}, None]},
        }},
        {"$set": {"score": {"$add": [{"$multiply": [WEIGHT_TAGS, "$s_tags"]},
                                     {"$multiply": [WEIGHT_READERS, "$s_readers"]}, "$b_author", "$b_year"]},
                  "in_series": {"$cond": [{"$ifNull": ["$series", False]}, 1, 0]}}},

        # ===== Classement : série en tête, puis score, égalités départagées par book_id =====
        {"$sort": {"in_series": -1, "score": -1, "_id": 1}},
        {"$limit": limit},
        {"$project": {"r_score": 0, "r_max": 0, "in_series": 0}},
    ]

    # ===== Preuves, seulement pour les livres retenus =====
    if evidence:
        pipeline += [
            # tags du livre de départ également posés sur le candidat
            {"$lookup": {"from": "book_tags", "localField": "book.goodreads_book_id",
                         "foreignField": "goodreads_book_id", "as": "tags",
                         "pipeline": [{"$match": {"tag_id": {"$in": list(profile)}}}]}},
            # lecteurs ayant aimé les deux livres
            {"$lookup": {"from": "ratings", "localField": "_id", "foreignField": "book_id", "as": "reader_sample",
                         "pipeline": [{"$match": {"user_id": {"$in": list(likers)}, "rating": LIKED}},
                                      {"$sort": {"user_id": 1}}] + ([{"$limit": sample}] if sample else [])}},
        ]

    results = list(db.book_tags.aggregate(pipeline))
    for r in results:
        if evidence:
            r["tags"] = sorted(({**profile[t["tag_id"]], "count_b": t["count"]} for t in r["tags"]),
                               key=lambda t: (-t["idf"], t["tag_id"]))
            r["reader_sample"] = [(x["user_id"], likers[x["user_id"]], x["rating"]) for x in r["reader_sample"]]
            r["nature"] = link_nature(r)
    return {"profile": profile, "n_likers": len(likers), "n_users": n_users, "results": results}


def link_nature(r):
    """Nature du lien entre le livre et un résultat, à partir des preuves."""
    nature = []
    if r["series"]:
        nature.append("même série")
    if r["readers"]:
        nature.append("lectorat partagé" if r["readers"]["lift"] > 1
                      else "lecteurs communs mais pas plus que le hasard (livres populaires)")
    if any(not t["common"] for t in r["tags"]):
        nature.append("thème spécifique")
    if r["authors"]:
        nature.append("même auteur")
    return nature or ["uniquement des tags très fréquents" if r["tags"] else "aucune preuve"]


# ---------------------------------------------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------------------------------------------

def fmt_year(y):
    return str(int(y)) if is_year(y) else "?"


def print_book_info(db, book, profile):
    stats = next(db.ratings.aggregate([
        {"$match": {"book_id": book["book_id"]}},
        {"$group": {"_id": None, "n": {"$sum": 1}, "avg": {"$avg": "$rating"}}},
    ]), {"n": 0, "avg": 0})
    print(f"[{book['book_id']}] {book['title']}")
    print(f"  Auteur(s)      : {book['authors']}")
    print(f"  Année          : {fmt_year(book.get('original_publication_year'))}")
    print(f"  Goodreads      : note moyenne {book['average_rating']} sur {book['ratings_count']} notes")
    print(f"  Notre base     : {stats['n']} notes, moyenne {stats['avg']:.2f}")
    series = parse_series(book["title"])
    if series:
        print(f"  Série          : {', '.join(f'{s} #{n}' for s, n in series)}")
    top = sorted(profile.values(), key=lambda t: -t["count"])[:10]
    print(f"  Tags principaux: {', '.join(t['tag_name'] + ('*' if t['common'] else '') for t in top)}")
    print(f"                   (* = tag très fréquent, porté par plus de {COMMON_TAG_RATIO:.0%} des livres)")


def print_results(book, res):
    print(f"\nLivres liés ({res['n_likers']} lecteurs ont aimé ce livre, note >= {LIKE_MIN_RATING}) :")
    for i, r in enumerate(res["results"], 1):
        b = r["book"]
        print(f"\n{i:>2}. [{b['book_id']}] {b['title']} - {b['authors']} ({fmt_year(b.get('original_publication_year'))})")
        print(f"    Score {r['score']:.3f} = {WEIGHT_TAGS} x tags {r['s_tags']:.3f} + {WEIGHT_READERS} x lecteurs "
              f"{r['s_readers']:.3f} + auteur {r['b_author']:.2f} + année {r['b_year']:.2f}")
        print(f"    Nature du lien : {', '.join(r['nature'])}")
        if r["series"]:
            print(f"    Série : {r['series']['name']} #{r['series']['tome']}")
        if r["authors"]:
            print(f"    Auteur(s) commun(s) : {', '.join(r['authors'])}")
        if r["readers"]:
            rd = r["readers"]
            sample = ", ".join(f"user {u} ({a}/{b2})" for u, a, b2 in r["reader_sample"])
            print(f"    Lecteurs : {rd['co']} ont aimé les deux (ce livre est aimé par {rd['n_b']} lecteurs) ; "
                  f"x{rd['lift']:.1f} par rapport au hasard")
            print(f"      ex. {sample}")
        specific = [t for t in r["tags"] if not t["common"]]
        common = [t for t in r["tags"] if t["common"]]
        if specific:
            print(f"    Tags spécifiques communs ({len(specific)}) : "
                  + ", ".join(f"{t['tag_name']} ({t['df']} livres)" for t in specific[:8]))
        if common:
            print(f"    Tags très fréquents communs ({len(common)}, non pris en compte) : "
                  + ", ".join(t["tag_name"] for t in common[:6]))
    print(f"\nPour lister toutes les preuves d'un lien : python f1.py {book['book_id']} --trace <book_id>")


def print_trace(book, other, res):
    """Liste tous les enregistrements (notes, tags) qui relient les deux livres."""
    print(f"\nPreuves du lien entre [{book['book_id']}] {book['title']} et [{other['book_id']}] {other['title']}")
    if not res["results"]:
        print("Aucun lien : ni série, ni auteur, ni tag spécifique commun, "
              f"ni au moins {MIN_SHARED_READERS} lecteurs communs.")
        return
    r = res["results"][0]
    print(f"Score {r['score']:.3f}, nature du lien : {', '.join(r['nature'])}")
    if r["series"]:
        print(f"\nSérie : {r['series']['name']} #{r['series']['tome']}")
    if r["authors"]:
        print(f"Auteur(s) commun(s) : {', '.join(r['authors'])}")

    print(f"\nRatings : {len(r['reader_sample'])} lecteurs ont noté les deux livres >= {LIKE_MIN_RATING}")
    print(f"  {'user_id':>8} | {'note ' + str(book['book_id']):>10} | {'note ' + str(other['book_id']):>10}")
    for u, a, b in r["reader_sample"]:
        print(f"  {u:>8} | {a:>10} | {b:>10}")

    print(f"\nBook_tags : {len(r['tags'])} tags communs (count = nb de fois où le tag a été posé sur le livre)")
    print(f"  {'tag_id':>6} | {'tag_name':<30} | {'count A':>8} | {'count B':>8} | {'nb livres':>9} | {'idf':>5}")
    for t in r["tags"]:
        flag = " (très fréquent)" if t["common"] else ""
        print(f"  {t['tag_id']:>6} | {t['tag_name'][:30]:<30} | {t['count']:>8} | {t['count_b']:>8} | "
              f"{t['df']:>9} | {t['idf']:>5.2f}{flag}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="F1 : explorer un livre et ses connexions")
    parser.add_argument("query", help="book_id ou (partie du) titre")
    parser.add_argument("--limit", type=int, default=F1_LIMIT, help="nombre de livres liés à afficher")
    parser.add_argument("--trace", type=int, metavar="BOOK_ID", help="lister les preuves du lien avec ce livre")
    args = parser.parse_args()

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)
    db = client[DATABASE_NAME]
    try:
        books = find_books(db, args.query)
        if not books:
            print("Aucun livre trouvé.")
            return
        if len(books) > 1:
            # Ambiguïté : on ne choisit pas à la place de l'utilisateur
            print(f"{len(books)} livres correspondent à « {args.query} », relancez avec un book_id :")
            for b in books:
                print(f"  [{b['book_id']}] {b['title']} - {b['authors']} ({fmt_year(b.get('original_publication_year'))})")
            return

        book = books[0]
        if args.trace is not None:
            other = db.books.find_one({"book_id": args.trace})
            if other is None:
                print(f"Livre {args.trace} introuvable.")
                return
            print_trace(book, other, similar_books(db, book, limit=1, only=args.trace, sample=None))
            return

        res = similar_books(db, book, args.limit)
        print_book_info(db, book, res["profile"])
        print_results(book, res)
    finally:
        client.close()


if __name__ == "__main__":
    main()
