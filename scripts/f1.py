# F1 : explorer un livre et ses connexions.
#
# Pour un livre donné (book_id ou titre), on affiche sa fiche catalogue puis les livres liés, classés par un
# score qui combine :
#   - la série   : un autre tome de la même série est toujours placé en tête ;
#   - les tags   : tags communs pondérés par leur rareté (IDF), les tags très fréquents ne comptent pas ;
#   - les lecteurs : lecteurs ayant aimé (note >= LIKE_MIN_RATING) les deux livres, normalisé par la popularité
#                  des deux livres et pondéré par l'activité de chaque lecteur ;
#   - des bonus  : même auteur (gros bonus) et année de publication proche (bonus moyen).
# Chaque résultat est accompagné de ses preuves et de la nature du lien (lectorat partagé / thème spécifique /
# uniquement des tags très fréquents).
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



# ---------------------------------------------------------------------------------------------------------------
# Catalogue : série, auteurs, année
# ---------------------------------------------------------------------------------------------------------------

SERIES_PART = re.compile(r"^\s*(.+?),?\s*#\s*([\d.\-]+)")


def parse_series(title):
    """'Catching Fire (The Hunger Games, #2)' -> [('The Hunger Games', '2')]. Un titre peut citer plusieurs séries (';')."""
    m = re.search(r"\(([^()]*#[^()]*)\)\s*$", str(title))
    if not m:
        return []
    series = []
    for part in m.group(1).split(";"):
        p = SERIES_PART.match(part)
        if p:
            series.append((p.group(1).strip(), p.group(2)))
    return series


def split_authors(authors):
    return [a.strip() for a in str(authors).split(",") if a.strip()]


def series_books(db, book):
    """Autres livres de la même série que `book` : {book_id: (nom de la série, numéro du tome)}."""
    found = {}
    for name, _ in parse_series(book["title"]):
        pattern = r"[(;]\s*" + re.escape(name) + r",?\s*#"
        for b in db.books.find({"title": {"$regex": pattern}, "book_id": {"$ne": book["book_id"]}},
                               {"book_id": 1, "title": 1}):
            tome = next((n for s, n in parse_series(b["title"]) if s == name), "?")
            found[b["book_id"]] = (name, tome)
    return found


def author_books(db, book):
    """Autres livres partageant au moins un auteur avec `book` : {book_id: [auteurs communs]}."""
    authors = split_authors(book["authors"])
    if not authors:
        return {}
    pattern = "|".join(r"(^|,\s*)" + re.escape(a) + r"\s*(,|$)" for a in authors)
    found = {}
    for b in db.books.find({"authors": {"$regex": pattern}, "book_id": {"$ne": book["book_id"]}},
                           {"book_id": 1, "authors": 1}):
        found[b["book_id"]] = sorted(set(authors) & set(split_authors(b["authors"])))
    return found


def year_bonus(year_a, year_b):
    """Bonus dégressif : YEAR_BONUS pour la même année, 0 au-delà de YEAR_WINDOW ans d'écart."""
    if not isinstance(year_a, (int, float)) or not isinstance(year_b, (int, float)):
        return 0.0
    if math.isnan(year_a) or math.isnan(year_b):
        return 0.0
    gap = abs(year_a - year_b)
    if gap > YEAR_WINDOW:
        return 0.0
    return YEAR_BONUS * (1 - gap / (YEAR_WINDOW + 1))


# ---------------------------------------------------------------------------------------------------------------
# Thématique : tags pondérés par leur rareté
# ---------------------------------------------------------------------------------------------------------------

def tag_profile(db, book):
    """Tags du livre avec leur fréquence dans le catalogue.

    Renvoie {tag_id: {"count": nb de fois où le tag a été posé sur ce livre, "df": nb de livres portant ce tag,
    "idf": log(N / df), "common": True si le tag est porté par plus de COMMON_TAG_RATIO des livres}}.
    """
    gr_id = book["goodreads_book_id"]
    counts = {bt["tag_id"]: bt["count"] for bt in db.book_tags.find({"goodreads_book_id": gr_id})}
    if not counts:
        return {}
    nb_books = db.books.count_documents({})
    df = {d["_id"]: d["df"] for d in db.book_tags.aggregate([
        {"$match": {"tag_id": {"$in": list(counts)}}},
        {"$group": {"_id": "$tag_id", "df": {"$sum": 1}}},
    ])}
    return {t: {"count": c, "df": df[t], "idf": math.log(nb_books / df[t]),
                "common": df[t] / nb_books > COMMON_TAG_RATIO}
            for t, c in counts.items()}


def tag_candidates(db, book, profile):
    """Livres partageant au moins un tag NON très fréquent avec `book`.

    Renvoie {goodreads_book_id: score_tags} où score_tags = somme des IDF des tags communs / somme des IDF des tags
    du livre (part du "poids thématique" du livre qu'on retrouve chez l'autre, entre 0 et 1).
    Les tags très fréquents sont exclus de la recherche : à eux seuls ils ne créent pas de lien.
    """
    specific = [t for t, p in profile.items() if not p["common"]]
    total_idf = sum(p["idf"] for p in profile.values())
    if not specific or total_idf == 0:
        return {}
    pipeline = [
        {"$match": {"tag_id": {"$in": specific},
                    "goodreads_book_id": {"$ne": book["goodreads_book_id"]}}},
        {"$group": {"_id": "$goodreads_book_id",
                    "tags": {"$push": "$tag_id"}}},
    ]
    return {d["_id"]: sum(profile[t]["idf"] for t in d["tags"]) / total_idf
            for d in db.book_tags.aggregate(pipeline)}


# ---------------------------------------------------------------------------------------------------------------
# Lecteurs : lectorat partagé normalisé
# ---------------------------------------------------------------------------------------------------------------

def reader_candidates(db, book):
    """Livres aimés par les lecteurs qui ont aimé `book`.

    Renvoie (n_a, n_users, {book_id: {"co", "weighted", "n_b", "lift", "score"}}) avec :
      co       : nombre de lecteurs ayant aimé les deux livres ;
      weighted : somme sur ces lecteurs de 1 / sqrt(nb de livres aimés par le lecteur)
                 (un lecteur très actif, qui aime beaucoup de livres, pèse moins) ;
      score    : weighted / sqrt(n_a * n_b), où n_a et n_b sont les nombres de lecteurs ayant aimé chaque livre
                 (un best-seller aimé par tout le monde n'est pas favorisé) ;
      lift     : co / (n_a * n_b / n_users), combien de fois plus de lecteurs communs que le hasard.
    Seuls les livres ayant au moins MIN_SHARED_READERS lecteurs communs sont gardés.
    """
    bid = book["book_id"]
    likers = [r["user_id"] for r in db.ratings.find({"book_id": bid, "rating": {"$gte": LIKE_MIN_RATING}},
                                                    {"user_id": 1})]
    n_a = len(likers)
    if n_a == 0:
        return 0, 0, {}

    pipeline = [
        {"$match": {"user_id": {"$in": likers}, "rating": {"$gte": LIKE_MIN_RATING}}},
        {"$group": {"_id": "$user_id", "books": {"$push": "$book_id"}, "n_liked": {"$sum": 1}}},
        {"$unwind": "$books"},
        {"$match": {"books": {"$ne": bid}}},
        {"$group": {"_id": "$books",
                    "co": {"$sum": 1},
                    "weighted": {"$sum": {"$divide": [1, {"$sqrt": "$n_liked"}]}}}},
        {"$match": {"co": {"$gte": MIN_SHARED_READERS}}},
    ]
    cands = {d["_id"]: {"co": d["co"], "weighted": d["weighted"]} for d in db.ratings.aggregate(pipeline)}
    if not cands:
        return n_a, 0, {}

    # Popularité (nombre de lecteurs qui l'ont aimé) de chaque livre candidat
    n_b = {d["_id"]: d["n"] for d in db.ratings.aggregate([
        {"$match": {"book_id": {"$in": list(cands)}, "rating": {"$gte": LIKE_MIN_RATING}}},
        {"$group": {"_id": "$book_id", "n": {"$sum": 1}}},
    ])}
    n_users = len(db.ratings.distinct("user_id"))
    for b, c in cands.items():
        c["n_b"] = n_b[b]
        c["score"] = c["weighted"] / math.sqrt(n_a * n_b[b])
        c["lift"] = c["co"] * n_users / (n_a * n_b[b])
    return n_a, n_users, cands


# ---------------------------------------------------------------------------------------------------------------
# Combinaison des scores
# ---------------------------------------------------------------------------------------------------------------

def similar_books(db, book, limit=F1_LIMIT, exclude=(), evidence=True):
    """Calcule les livres liés à `book`, classés : tomes de la même série d'abord, puis par score décroissant.

    exclude  : book_id à ne pas renvoyer (F3 y met les livres déjà notés par le lecteur)
    evidence : False pour ne pas calculer les preuves détaillées (tags, lecteurs communs) des résultats
    """
    profile = tag_profile(db, book)
    tags_by_gr = tag_candidates(db, book, profile)
    n_a, n_users, readers = reader_candidates(db, book)
    series = series_books(db, book)
    authors = author_books(db, book)

    # book_tags utilise goodreads_book_id : on convertit en book_id
    gr_to_id = {b["goodreads_book_id"]: b["book_id"]
                for b in db.books.find({"goodreads_book_id": {"$in": list(tags_by_gr)}},
                                       {"goodreads_book_id": 1, "book_id": 1})}
    tags = {gr_to_id[g]: s for g, s in tags_by_gr.items() if g in gr_to_id}

    candidate_ids = set(tags) | set(readers) | set(series) | set(authors)
    docs = {b["book_id"]: b for b in db.books.find({"book_id": {"$in": list(candidate_ids)}})}
    max_reader = max((r["score"] for r in readers.values()), default=0) or 1

    results = []
    for bid in candidate_ids:
        other = docs.get(bid)
        if other is None or bid in exclude:
            continue
        s_tags = tags.get(bid, 0.0)
        s_readers = readers[bid]["score"] / max_reader if bid in readers else 0.0
        b_author = AUTHOR_BONUS if bid in authors else 0.0
        b_year = year_bonus(book.get("original_publication_year"), other.get("original_publication_year"))
        results.append({
            "book": other,
            "score": WEIGHT_TAGS * s_tags + WEIGHT_READERS * s_readers + b_author + b_year,
            "s_tags": s_tags,
            "s_readers": s_readers,
            "b_author": b_author,
            "b_year": b_year,
            "series": series.get(bid),
            "authors": authors.get(bid, []),
            "readers": readers.get(bid),
        })

    # Série en tête, puis score décroissant, égalités départagées par book_id (ordre déterministe)
    results.sort(key=lambda r: (r["series"] is None, -r["score"], r["book"]["book_id"]))
    results = results[:limit]
    if evidence:
        for r in results:
            add_evidence(db, book, profile, r)
    return {"profile": profile, "n_likers": n_a, "n_users": n_users, "results": results}


# ---------------------------------------------------------------------------------------------------------------
# Preuves (traçabilité)
# ---------------------------------------------------------------------------------------------------------------

def shared_tags(db, book, other, profile):
    """Tags communs aux deux livres avec leurs comptes sur chaque livre, les plus rares d'abord."""
    other_counts = {bt["tag_id"]: bt["count"]
                    for bt in db.book_tags.find({"goodreads_book_id": other["goodreads_book_id"],
                                                 "tag_id": {"$in": list(profile)}})}
    names = {t["tag_id"]: t["tag_name"] for t in db.tags.find({"tag_id": {"$in": list(other_counts)}})}
    rows = [{"tag_id": t,
             "tag_name": names.get(t, "?"),
             "count_a": profile[t]["count"],
             "count_b": c,
             "df": profile[t]["df"],
             "idf": profile[t]["idf"],
             "common": profile[t]["common"]}
            for t, c in other_counts.items()]
    rows.sort(key=lambda x: (-x["idf"], x["tag_id"]))
    return rows


def shared_readers(db, book, other, limit=None):
    """Lecteurs ayant aimé les deux livres, avec leur note pour chacun."""
    pipeline = [
        {"$match": {"book_id": {"$in": [book["book_id"], other["book_id"]]}, "rating": {"$gte": LIKE_MIN_RATING}}},
        {"$group": {"_id": "$user_id", "ratings": {"$push": {"book_id": "$book_id", "rating": "$rating"}}}},
        {"$match": {"ratings.1": {"$exists": True}}},
        {"$sort": {"_id": 1}},
    ]
    if limit:
        pipeline.append({"$limit": limit})
    rows = []
    for d in db.ratings.aggregate(pipeline):
        notes = {x["book_id"]: x["rating"] for x in d["ratings"]}
        rows.append((d["_id"], notes[book["book_id"]], notes[other["book_id"]]))
    return rows


def add_evidence(db, book, profile, r):
    """Ajoute à un résultat ses tags communs, quelques lecteurs communs et la nature du lien."""
    r["tags"] = shared_tags(db, book, r["book"], profile)
    r["reader_sample"] = shared_readers(db, book, r["book"], limit=5) if r["readers"] else []
    specific = [t for t in r["tags"] if not t["common"]]
    nature = []
    if r["series"]:
        nature.append("même série")
    if r["readers"] and r["readers"]["lift"] > 1:
        nature.append("lectorat partagé")
    elif r["readers"]:
        nature.append("lecteurs communs mais pas plus que le hasard (livres populaires)")
    if specific:
        nature.append("thème spécifique")
    if r["authors"]:
        nature.append("même auteur")
    if not nature:
        nature.append("uniquement des tags très fréquents" if r["tags"] else "aucune preuve")
    r["nature"] = nature


# ---------------------------------------------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------------------------------------------

def fmt_year(y):
    return str(int(y)) if isinstance(y, (int, float)) and not math.isnan(y) else "?"


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
    top = sorted(profile.items(), key=lambda x: -x[1]["count"])[:10]
    names = {t["tag_id"]: t["tag_name"] for t in db.tags.find({"tag_id": {"$in": [t for t, _ in top]}})}
    print(f"  Tags principaux: {', '.join(names.get(t, '?') + ('*' if p['common'] else '') for t, p in top)}")
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
            print(f"    Série : {r['series'][0]} #{r['series'][1]}")
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


def print_trace(db, book, other, profile):
    """Liste tous les enregistrements (notes, tags) qui relient les deux livres."""
    print(f"\nPreuves du lien entre [{book['book_id']}] {book['title']} et [{other['book_id']}] {other['title']}")
    series = series_books(db, book).get(other["book_id"])
    if series:
        print(f"\nSérie : {series[0]} #{series[1]}")
    common_authors = author_books(db, book).get(other["book_id"])
    if common_authors:
        print(f"Auteur(s) commun(s) : {', '.join(common_authors)}")

    readers = shared_readers(db, book, other)
    print(f"\nRatings : {len(readers)} lecteurs ont noté les deux livres >= {LIKE_MIN_RATING}")
    print(f"  {'user_id':>8} | {'note ' + str(book['book_id']):>10} | {'note ' + str(other['book_id']):>10}")
    for u, a, b in readers:
        print(f"  {u:>8} | {a:>10} | {b:>10}")

    tags = shared_tags(db, book, other, profile)
    print(f"\nBook_tags : {len(tags)} tags communs (count = nb de fois où le tag a été posé sur le livre)")
    print(f"  {'tag_id':>6} | {'tag_name':<30} | {'count A':>8} | {'count B':>8} | {'nb livres':>9} | {'idf':>5}")
    for t in tags:
        flag = " (très fréquent)" if t["common"] else ""
        print(f"  {t['tag_id']:>6} | {t['tag_name'][:30]:<30} | {t['count_a']:>8} | {t['count_b']:>8} | "
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
        profile = tag_profile(db, book)
        if args.trace is not None:
            other = db.books.find_one({"book_id": args.trace})
            if other is None:
                print(f"Livre {args.trace} introuvable.")
                return
            print_trace(db, book, other, profile)
            return

        print_book_info(db, book, profile)
        print_results(book, similar_books(db, book, args.limit))
    finally:
        client.close()


if __name__ == "__main__":
    main()
