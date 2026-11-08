# F4 : tracer une connexion entre deux livres.
#
# Deux livres A et B sont reliés par un chemin A -> X -> B (ou A -> X -> Y -> B) où chaque étape est un lien
# "lectorat partagé" : assez de lecteurs ont aimé les deux livres, nettement plus que le hasard.
# Requêtes MongoDB utilisées :
#   - $match + $group + $sum : popularité de chaque livre et activité de chaque lecteur, pour écarter
#     les livres "carrefours" (intermédiaires interdits) et les lecteurs très actifs (non utilisés) ;
#   - $match + $group + $sort avec départage par _id : voisins d'un livre = livres aimés par ses lecteurs ;
#   - $elemMatch (deux conditions sur le même élément) : lecteurs qui prouvent une étape, c.-à-d. qui ont aimé
#     les deux livres ;
#   - $project : ne renvoyer que les champs utiles pour les preuves.
#
# Usage (depuis scripts/) :
#   python f4.py 1 2                          (par book_id)
#   python f4.py "Twilight (Twilight, #1)" 2  (par titre ; si plusieurs livres correspondent, on les affiche)
#   python f4.py 1 2 --limit 10               (nombre de chemins)
#   python f4.py 1 2 --max-steps 2            (ne pas chercher de chemins à 3 étapes)

import argparse
import math
import sys

from pymongo import MongoClient

import f1
from config import (DATABASE_NAME, F4_ACTIVE_READERS, F4_EXPAND, F4_HUB_BOOKS, F4_LIMIT, F4_MIN_LIFT,
                    LIKE_MIN_RATING, MIN_SHARED_READERS, MONGO_TIMEOUT_MS, MONGO_URI)


# ---------------------------------------------------------------------------------------------------------------
# Livres et lecteurs trop connectés ($match + $group + $sum)
# ---------------------------------------------------------------------------------------------------------------

def activity_stats(db):
    """Calcule à partir de `ratings` :
      - active  : lecteurs très actifs (la part F4_ACTIVE_READERS ayant aimé le plus de livres), ignorés partout ;
      - likes   : {book_id: nb de lecteurs (non très actifs) l'ayant aimé} ;
      - hubs    : les F4_HUB_BOOKS livres les plus aimés, interdits comme intermédiaires ;
      - n_users : nb de lecteurs gardés (pour le calcul du hasard).
    """
    per_user = list(db.ratings.aggregate([
        {"$match": {"rating": {"$gte": LIKE_MIN_RATING}}},
        {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
        {"$sort": {"n": -1, "_id": 1}},
    ]))
    nb_active = int(len(per_user) * F4_ACTIVE_READERS)
    active = {d["_id"] for d in per_user[:nb_active]}
    active_limit = per_user[nb_active - 1]["n"] if nb_active else None

    per_book = list(db.ratings.aggregate([
        {"$match": {"rating": {"$gte": LIKE_MIN_RATING}, "user_id": {"$nin": list(active)}}},
        {"$group": {"_id": "$book_id", "n": {"$sum": 1}}},
        {"$sort": {"n": -1, "_id": 1}},
    ]))
    likes = {d["_id"]: d["n"] for d in per_book}
    hubs = {d["_id"] for d in per_book[:F4_HUB_BOOKS]}
    hub_limit = per_book[F4_HUB_BOOKS - 1]["n"] if len(per_book) >= F4_HUB_BOOKS else None
    return {"active": active, "active_limit": active_limit, "likes": likes, "hubs": hubs,
            "hub_limit": hub_limit, "n_users": len(per_user) - nb_active}


# ---------------------------------------------------------------------------------------------------------------
# Voisins d'un livre ($match + $group + $sort, départage par _id)
# ---------------------------------------------------------------------------------------------------------------

def neighbors(db, book_id, stats, cache):
    """Livres reliés à `book_id` par une étape convaincante : {book_id: {"co", "lift", "strength"}}.

    co       : nb de lecteurs (non très actifs) ayant aimé les deux livres, au moins MIN_SHARED_READERS ;
    lift     : co / (nA x nB / nb_lecteurs), combien de fois plus que le hasard, au moins F4_MIN_LIFT ;
    strength : co / sqrt(nA x nB) (similarité cosinus, entre 0 et 1), sert à classer les chemins.
    """
    if book_id in cache:
        return cache[book_id]
    likers = [r["user_id"] for r in db.ratings.find(
        {"book_id": book_id, "rating": {"$gte": LIKE_MIN_RATING}, "user_id": {"$nin": list(stats["active"])}},
        {"_id": 0, "user_id": 1})]
    result = {}
    if likers:
        pipeline = [
            {"$match": {"user_id": {"$in": likers}, "rating": {"$gte": LIKE_MIN_RATING},
                        "book_id": {"$ne": book_id}}},
            {"$group": {"_id": "$book_id", "co": {"$sum": 1}}},
            {"$match": {"co": {"$gte": MIN_SHARED_READERS}}},
            {"$sort": {"co": -1, "_id": 1}},
        ]
        n_a = len(likers)
        for d in db.ratings.aggregate(pipeline):
            n_b = stats["likes"].get(d["_id"], d["co"])
            lift = d["co"] * stats["n_users"] / (n_a * n_b)
            if lift >= F4_MIN_LIFT:
                result[d["_id"]] = {"co": d["co"], "lift": lift, "strength": d["co"] / math.sqrt(n_a * n_b)}
    cache[book_id] = result
    return result


# ---------------------------------------------------------------------------------------------------------------
# Recherche des chemins
# ---------------------------------------------------------------------------------------------------------------

def find_paths(db, a, b, stats, limit=F4_LIMIT, max_steps=3):
    """Chemins de A à B, classés du plus au moins convaincant.

    Score d'un chemin = force de son étape la plus faible (un chemin vaut ce que vaut son maillon le plus faible).
    Classement : moins d'étapes d'abord, puis score décroissant, puis identifiants (ordre déterministe).
    Les livres carrefours (hubs) ne peuvent pas être intermédiaires ; A et B peuvent l'être eux-mêmes.
    """
    cache = {}
    n_a, n_b = neighbors(db, a, stats, cache), neighbors(db, b, stats, cache)
    paths = []

    # Lien direct
    if b in n_a:
        paths.append({"books": [a, b], "steps": [n_a[b]]})

    # 2 étapes : A -> X -> B, X voisin de A et de B
    blocked = []  # intermédiaires qui auraient relié A et B mais sont des carrefours
    for x in set(n_a) & set(n_b) - {a, b}:
        if x in stats["hubs"]:
            blocked.append(x)
            continue
        paths.append({"books": [a, x, b], "steps": [n_a[x], n_b[x]]})

    # 3 étapes : A -> X -> Y -> B, seulement s'il manque des chemins plus courts
    nb_short = len(paths)
    if max_steps >= 3 and nb_short < limit:
        explore = [x for x in sorted(n_a, key=lambda x: (-n_a[x]["strength"], x))
                   if x not in stats["hubs"] and x != b][:F4_EXPAND]
        for x in explore:
            n_x = neighbors(db, x, stats, cache)
            best = None
            for y in set(n_x) & set(n_b) - {a, b, x}:
                if y in stats["hubs"]:
                    continue
                p = {"books": [a, x, y, b], "steps": [n_a[x], n_x[y], n_b[y]]}
                if best is None or path_key(p) < path_key(best):
                    best = p
            if best:  # un seul chemin par premier intermédiaire, pour proposer des alternatives différentes
                paths.append(best)

    for p in paths:
        p["score"] = min(s["strength"] for s in p["steps"])
    paths.sort(key=path_key)
    return {"paths": paths[:limit], "total": len(paths), "n_a": len(n_a), "n_b": len(n_b), "blocked": blocked}


def path_key(p):
    return (len(p["books"]), -min(s["strength"] for s in p["steps"]), p["books"])


# ---------------------------------------------------------------------------------------------------------------
# Preuves d'une étape ($elemMatch, $project)
# ---------------------------------------------------------------------------------------------------------------

def step_readers(db, x, y, stats, limit=3):
    """Lecteurs (non très actifs) ayant aimé x ET y, avec leurs deux notes.

    On regroupe les notes de chaque lecteur dans un tableau, puis $elemMatch impose la note >= LIKE_MIN_RATING sur
    le MÊME élément que le livre.
    """
    liked = lambda book: {"$elemMatch": {"book_id": book, "rating": {"$gte": LIKE_MIN_RATING}}}
    pipeline = [
        {"$match": {"book_id": {"$in": [x, y]}, "user_id": {"$nin": list(stats["active"])}}},
        {"$group": {"_id": "$user_id", "notes": {"$push": {"book_id": "$book_id", "rating": "$rating"}}}},
        {"$match": {"notes": {"$all": [liked(x), liked(y)]}}},
        {"$project": {"_id": 1, "notes": 1}},
        {"$sort": {"_id": 1}},
        {"$limit": limit},
    ]
    rows = []
    for d in db.ratings.aggregate(pipeline):
        notes = {n["book_id"]: n["rating"] for n in d["notes"]}
        rows.append((d["_id"], notes[x], notes[y]))
    return rows


def catalogue_link(db, bx, by):
    """Attributs catalogue communs à deux livres (affichés comme contexte, pas utilisés dans le score)."""
    links = []
    if set(f1.split_authors(bx["authors"])) & set(f1.split_authors(by["authors"])):
        links.append("même auteur")
    if {s for s, _ in f1.parse_series(bx["title"])} & {s for s, _ in f1.parse_series(by["title"])}:
        links.append("même série")
    px, py = f1.tag_profile(db, bx), f1.tag_profile(db, by)
    specific = sorted((t for i, t in px.items() if i in py and not t["common"]), key=lambda t: -t["idf"])
    if specific:
        links.append(f"{len(specific)} tags spécifiques communs (ex. {', '.join(t['tag_name'] for t in specific[:3])})")
    return links


# ---------------------------------------------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------------------------------------------

def print_report(db, a, b, res, stats, max_steps=3):
    print(f"Lien entre [{a['book_id']}] {a['title']} et [{b['book_id']}] {b['title']}")
    print(f"  Livres carrefours exclus comme intermédiaires : les {F4_HUB_BOOKS} plus aimés "
          f"(>= {stats['hub_limit']} lecteurs)")
    print(f"  Lecteurs très actifs ignorés : {len(stats['active'])} ({F4_ACTIVE_READERS:.0%}, "
          f">= {stats['active_limit']} livres aimés)")
    print(f"  Étape convaincante : >= {MIN_SHARED_READERS} lecteurs communs (note >= {LIKE_MIN_RATING}) "
          f"et >= x{F4_MIN_LIFT} par rapport au hasard")
    print(f"  Voisins convaincants : {res['n_a']} pour [{a['book_id']}], {res['n_b']} pour [{b['book_id']}]")

    if not res["paths"]:
        print("\nAucune connexion convaincante trouvée.")
        if res["n_a"] == 0 or res["n_b"] == 0:
            empty = a if res["n_a"] == 0 else b
            print(f"  [{empty['book_id']}] n'a aucun voisin convaincant : trop peu de lecteurs l'ont aimé, ou ses "
                  f"lecteurs n'aiment pas d'autres livres plus que le hasard.")
        if res["blocked"]:
            docs = {d["book_id"]: d for d in db.books.find({"book_id": {"$in": res["blocked"][:5]}})}
            print(f"  {len(res['blocked'])} chemin(s) passeraient uniquement par des livres carrefours (exclus), ex. "
                  + ", ".join(f"[{x}] {docs[x]['title']}" for x in res["blocked"][:5] if x in docs))
        elif res["n_a"] and res["n_b"]:
            print(f"  Les deux livres ont des voisins, mais aucun livre (hors carrefours) n'est voisin des deux"
                  + (" et aucun chemin à 3 étapes n'a été trouvé." if max_steps >= 3
                     else " ; essayer --max-steps 3."))
        return

    ids = {i for p in res["paths"] for i in p["books"]}
    docs = {d["book_id"]: d for d in db.books.find({"book_id": {"$in": list(ids)}})}
    print(f"\n{res['total']} chemin(s) trouvé(s), les {len(res['paths'])} plus convaincants :")
    for i, p in enumerate(res["paths"], 1):
        kind = "lien direct" if len(p["books"]) == 2 else f"{len(p['books']) - 1} étapes"
        weakest = min(s["co"] for s in p["steps"])
        solidity = "solide" if weakest >= 10 else "moyen" if weakest >= 5 else "fragile"
        print(f"\n{i}. {kind}, score {p['score']:.3f} (force de l'étape la plus faible) - {solidity} : "
              f"l'étape la plus faible repose sur {weakest} lecteurs")
        print("   " + " -> ".join(f"[{bk}] {docs[bk]['title']}" for bk in p["books"]))
        for (x, y), s in zip(zip(p["books"], p["books"][1:]), p["steps"]):
            ex = ", ".join(f"user {u} ({nx}/{ny})" for u, nx, ny in step_readers(db, x, y, stats))
            print(f"   [{x}] -> [{y}] : {s['co']} lecteurs communs, x{s['lift']:.1f} vs hasard, force {s['strength']:.3f}")
            print(f"       ex. {ex}")
            cat = catalogue_link(db, docs[x], docs[y])
            if cat:
                print(f"       catalogue : {', '.join(cat)}")
    if res["blocked"]:
        print(f"\n{len(res['blocked'])} autre(s) chemin(s) à 2 étapes écarté(s) car ils passent par un livre carrefour.")
    print(f"\nTracer une étape : python f1.py <livre> --trace <livre suivant>")


def resolve(db, query):
    """Un seul livre pour la requête, ou None après avoir affiché le problème (aucun / plusieurs livres)."""
    books = f1.find_books(db, query)
    if not books:
        print(f"Aucun livre trouvé pour « {query} ».")
        return None
    if len(books) > 1:
        print(f"{len(books)} livres correspondent à « {query} », relancez avec un book_id :")
        for bk in books:
            print(f"  [{bk['book_id']}] {bk['title']} - {bk['authors']}")
        return None
    return books[0]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="F4 : tracer une connexion entre deux livres")
    parser.add_argument("book1", help="book_id ou (partie du) titre")
    parser.add_argument("book2", help="book_id ou (partie du) titre")
    parser.add_argument("--limit", type=int, default=F4_LIMIT, help="nombre de chemins à afficher")
    parser.add_argument("--max-steps", type=int, choices=[2, 3], default=3, help="nombre maximal d'étapes")
    args = parser.parse_args()

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)
    db = client[DATABASE_NAME]
    try:
        a, b = resolve(db, args.book1), resolve(db, args.book2)
        if a is None or b is None:
            return
        if a["book_id"] == b["book_id"]:
            print("Les deux livres sont identiques.")
            return
        stats = activity_stats(db)
        res = find_paths(db, a["book_id"], b["book_id"], stats, args.limit, args.max_steps)
        print_report(db, a, b, res, stats, args.max_steps)
    finally:
        client.close()


if __name__ == "__main__":
    main()
