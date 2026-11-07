# F5 : découvrir les liens entre deux centres d'intérêt (deux tags).
#
# Groupe de lecteurs d'un tag : lecteurs ayant aimé (note >= LIKE_MIN_RATING) au moins F5_MIN_LIKED livres portant
# ce tag, et plus de livres de ce tag que la moyenne des lecteurs.
# Score de lien : recoupement des deux groupes comparé au hasard (lift = |G1 ∩ G2| x nb_lecteurs / (|G1| x |G2|)).
# Livres pont : livres aimés à la fois par une part des lecteurs du groupe 1 et du groupe 2, classés par la plus
# petite des deux parts (un pont doit tenir des deux côtés).
# Le tout est recalculé sans les livres très populaires ni les lecteurs très actifs (mêmes définitions que F4),
# pour voir si le lien tient ou s'effondre.
#
# Usage (depuis scripts/) :
#   python f5.py 11305 26138               (par tag_id : fantasy / romance)
#   python f5.py fantasy romance           (par nom exact du tag)
#   python f5.py fantasy romance --limit 5 (nombre de livres pont)

import argparse
import sys

from pymongo import MongoClient

import f4
from config import (DATABASE_NAME, F4_ACTIVE_READERS, F4_HUB_BOOKS, F5_EXAMPLES, F5_LIMIT, F5_MIN_LIFT,
                    F5_MIN_LIKED, LIKE_MIN_RATING, MIN_SHARED_READERS, MONGO_TIMEOUT_MS, MONGO_URI)


# ---------------------------------------------------------------------------------------------------------------
# Tags et livres
# ---------------------------------------------------------------------------------------------------------------

def find_tag(db, query):
    """Tag par tag_id (argument numérique) ou par nom exact."""
    if query.isdigit():
        return db.tags.find_one({"tag_id": int(query)})
    return db.tags.find_one({"tag_name": query})


def tag_books(db, tag_id):
    """Livres portant le tag : {book_id: count} (count = nb d'utilisateurs Goodreads ayant posé ce tag)."""
    counts = {d["goodreads_book_id"]: d["count"] for d in db.book_tags.find({"tag_id": tag_id})}
    return {b["book_id"]: counts[b["goodreads_book_id"]]
            for b in db.books.find({"goodreads_book_id": {"$in": list(counts)}}, {"book_id": 1, "goodreads_book_id": 1})}


# ---------------------------------------------------------------------------------------------------------------
# Groupes de lecteurs ($match + $group + $sum)
# ---------------------------------------------------------------------------------------------------------------

def reader_group(db, books, n_users, excl_readers=(), excl_books=()):
    """Lecteurs qui aiment le tag : {user_id: nb de livres du tag aimés}, et le seuil utilisé.

    Seuil = max(F5_MIN_LIKED, moyenne du nb de livres du tag aimés par lecteur, sur tous les lecteurs gardés).
    """
    counts = {d["_id"]: d["n"] for d in db.ratings.aggregate([
        {"$match": {"book_id": {"$in": [b for b in books if b not in excl_books]},
                    "rating": {"$gte": LIKE_MIN_RATING}, "user_id": {"$nin": list(excl_readers)}}},
        {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
    ])}
    mean = sum(counts.values()) / n_users if n_users else 0
    threshold = max(F5_MIN_LIKED, mean)
    return {u: n for u, n in counts.items() if n >= threshold}, threshold


def overlap(g1, g2, n_users):
    """Recoupement de deux groupes : taille, lift (vs hasard) et Jaccard."""
    both = set(g1) & set(g2)
    lift = len(both) * n_users / (len(g1) * len(g2)) if g1 and g2 else 0
    jaccard = len(both) / len(set(g1) | set(g2)) if g1 or g2 else 0
    return {"both": both, "lift": lift, "jaccard": jaccard}


# ---------------------------------------------------------------------------------------------------------------
# Livres pont ($match + $group + $sort)
# ---------------------------------------------------------------------------------------------------------------

def liked_by(db, group, excl_books=()):
    """{book_id: nb de lecteurs du groupe l'ayant aimé}."""
    return {d["_id"]: d["n"] for d in db.ratings.aggregate([
        {"$match": {"user_id": {"$in": list(group)}, "rating": {"$gte": LIKE_MIN_RATING},
                    "book_id": {"$nin": list(excl_books)}}},
        {"$group": {"_id": "$book_id", "n": {"$sum": 1}}},
    ])}


def bridges(db, g1, g2, likes, n_users, excl_books=()):
    """Livres aimés par les deux groupes, plus que par l'ensemble des lecteurs.

    a, b   : nb de lecteurs du groupe 1 / 2 ayant aimé le livre (au moins MIN_SHARED_READERS chacun) ;
    lift1/2: part du groupe qui l'a aimé / part de tous les lecteurs qui l'ont aimé (doit dépasser 1 des deux côtés) ;
    score  : min(a / |G1|, b / |G2|), la part la plus faible des deux groupes.
    """
    l1, l2 = liked_by(db, g1, excl_books), liked_by(db, g2, excl_books)
    res = []
    for book in set(l1) & set(l2):
        a, b = l1[book], l2[book]
        if a < MIN_SHARED_READERS or b < MIN_SHARED_READERS:
            continue
        p0 = likes[book] / n_users
        p1, p2 = a / len(g1), b / len(g2)
        if p1 / p0 > 1 and p2 / p0 > 1:
            res.append({"book_id": book, "a": a, "b": b, "lift1": p1 / p0, "lift2": p2 / p0, "score": min(p1, p2)})
    res.sort(key=lambda r: (-r["score"], r["book_id"]))
    return res


# ---------------------------------------------------------------------------------------------------------------
# Calcul complet (brut ou sans livres populaires / lecteurs actifs)
# ---------------------------------------------------------------------------------------------------------------

def link(db, books1, books2, likes, n_users, excl_readers=(), excl_books=()):
    g1, th1 = reader_group(db, books1, n_users, excl_readers, excl_books)
    g2, th2 = reader_group(db, books2, n_users, excl_readers, excl_books)
    return {"g1": g1, "g2": g2, "th1": th1, "th2": th2, **overlap(g1, g2, n_users),
            "bridges": bridges(db, g1, g2, likes, n_users, excl_books)}


def all_likes(db):
    """{book_id: nb de lecteurs l'ayant aimé} et nb total de lecteurs, sans aucune exclusion."""
    likes = {d["_id"]: d["n"] for d in db.ratings.aggregate([
        {"$match": {"rating": {"$gte": LIKE_MIN_RATING}}},
        {"$group": {"_id": "$book_id", "n": {"$sum": 1}}},
    ])}
    return likes, len(db.ratings.distinct("user_id"))


def tag_link(db, t1, t2):
    books1, books2 = tag_books(db, t1["tag_id"]), tag_books(db, t2["tag_id"])
    likes, n_users = all_likes(db)
    stats = f4.activity_stats(db)  # lecteurs très actifs et livres carrefours, comme F4
    raw = link(db, books1, books2, likes, n_users)
    robust = link(db, books1, books2, stats["likes"], stats["n_users"], stats["active"], stats["hubs"])
    # Contrôle : les livres portant les deux tags font-ils tout le lien ? (groupes calculés sans eux)
    double = set(books1) & set(books2)
    only = {}
    if double:
        o1, _ = reader_group(db, books1, n_users, excl_books=double)
        o2, _ = reader_group(db, books2, n_users, excl_books=double)
        only = overlap(o1, o2, n_users)
    return {"books1": books1, "books2": books2, "double": double, "raw": raw, "robust": robust, "only": only,
            "stats": stats, "n_users": n_users}


# ---------------------------------------------------------------------------------------------------------------
# Preuves et affichage
# ---------------------------------------------------------------------------------------------------------------

def reader_examples(db, users, books1, books2, docs):
    """Pour quelques lecteurs des deux groupes : un livre aimé de chaque tag, avec sa note."""
    rows = []
    for u in users:
        notes = list(db.ratings.find({"user_id": u, "rating": {"$gte": LIKE_MIN_RATING},
                                      "book_id": {"$in": list(set(books1) | set(books2))}},
                                     {"book_id": 1, "rating": 1}).sort("book_id", 1))
        n1 = next((n for n in notes if n["book_id"] in books1 and n["book_id"] not in books2), None) \
            or next((n for n in notes if n["book_id"] in books1), None)
        n2 = next((n for n in notes if n["book_id"] in books2 and n["book_id"] not in books1), None) \
            or next((n for n in notes if n["book_id"] in books2), None)
        rows.append((u, n1, n2))
    ids = {n["book_id"] for _, n1, n2 in rows for n in (n1, n2) if n}
    docs.update({d["book_id"]: d for d in db.books.find({"book_id": {"$in": list(ids - set(docs))}})})
    return rows


def bridge_readers(db, book, users, limit=F5_EXAMPLES):
    """Lecteurs des deux groupes ayant aimé le livre pont, avec leur note."""
    return [(r["user_id"], r["rating"]) for r in db.ratings.find(
        {"book_id": book, "user_id": {"$in": list(users)}, "rating": {"$gte": LIKE_MIN_RATING}},
        {"user_id": 1, "rating": 1}).sort("user_id", 1).limit(limit)]


def verdict(raw, robust):
    if len(raw["both"]) < MIN_SHARED_READERS:
        return "pas de lien : trop peu de lecteurs aiment les deux tags"
    if raw["lift"] < F5_MIN_LIFT:
        return f"lien faible : les deux groupes ne se recoupent pas nettement plus que le hasard (< x{F5_MIN_LIFT})"
    if len(robust["both"]) >= MIN_SHARED_READERS and robust["lift"] >= F5_MIN_LIFT:
        return "le lien TIENT sans les livres très populaires ni les lecteurs très actifs"
    return "le lien S'EFFONDRE sans les livres très populaires ni les lecteurs très actifs"


def print_group_summary(name, res, n1, n2):
    print(f"  {name} : groupe '{n1}' {len(res['g1'])} lecteurs (>= {res['th1']:.1f} livres aimés), "
          f"groupe '{n2}' {len(res['g2'])} lecteurs (>= {res['th2']:.1f}) ; "
          f"{len(res['both'])} lecteurs dans les deux, x{res['lift']:.2f} vs hasard (Jaccard {res['jaccard']:.2f})")


def print_bridges(db, res, books1, books2, limit, docs):
    if not res["bridges"]:
        print("    aucun livre pont")
        return
    top = res["bridges"][:limit]
    docs.update({d["book_id"]: d for d in db.books.find({"book_id": {"$in": [r["book_id"] for r in top]}})})
    for i, r in enumerate(top, 1):
        bk = r["book_id"]
        tags = [f"tag 1 posé {books1[bk]} fois" if bk in books1 else "sans tag 1",
                f"tag 2 posé {books2[bk]} fois" if bk in books2 else "sans tag 2"]
        print(f"\n  {i:>2}. [{bk}] {docs[bk]['title']} - {docs[bk]['authors']}")
        print(f"      score {r['score']:.3f} : aimé par {r['a']} lecteurs du groupe 1 ({r['a'] / len(res['g1']):.1%}, "
              f"x{r['lift1']:.1f} vs tous les lecteurs) et {r['b']} du groupe 2 "
              f"({r['b'] / len(res['g2']):.1%}, x{r['lift2']:.1f})")
        print(f"      catalogue : {', '.join(tags)}")
        ex = bridge_readers(db, bk, res["both"])
        if ex:
            print(f"      ex. lecteurs des deux groupes : " + ", ".join(f"user {u} ({n}★)" for u, n in ex))


def print_report(db, t1, t2, res, limit):
    n1, n2 = t1["tag_name"], t2["tag_name"]
    raw, robust, stats = res["raw"], res["robust"], res["stats"]
    docs = {}
    print(f"Lien entre les tags [{t1['tag_id']}] {n1} et [{t2['tag_id']}] {n2}")
    print(f"  Livres portant le tag : {len(res['books1'])} '{n1}', {len(res['books2'])} '{n2}', "
          f"{len(res['double'])} portent les deux (contexte catalogue, pas utilisé dans le score)")
    print(f"  Un lecteur aime un tag s'il a aimé (note >= {LIKE_MIN_RATING}) au moins {F5_MIN_LIKED} livres du tag "
          f"et plus que la moyenne des lecteurs")

    print(f"\nScore de lien (recoupement des groupes de lecteurs) :")
    print_group_summary("brut               ", raw, n1, n2)
    print_group_summary("sans pop. / actifs ", robust, n1, n2)
    print(f"    (sans les {F4_HUB_BOOKS} livres les plus aimés, >= {stats['hub_limit']} lecteurs, et sans les "
          f"{len(stats['active'])} lecteurs les plus actifs, {F4_ACTIVE_READERS:.0%}, >= {stats['active_limit']} "
          f"livres aimés)")
    if res["only"]:
        o = res["only"]
        print(f"  contrôle : sans les {len(res['double'])} livres portant les deux tags, {len(o['both'])} lecteurs "
              f"dans les deux groupes, x{o['lift']:.2f} vs hasard")
    print(f"\n  => {verdict(raw, robust)}")

    if raw["both"]:
        print(f"\nGroupe de lecteurs qui aiment les deux : {len(raw['both'])} lecteurs "
              f"({len(robust['both'])} sans pop. / actifs)")
        users = sorted(raw["both"], key=lambda u: (-min(raw["g1"][u], raw["g2"][u]), u))[:F5_EXAMPLES]
        for u, b1, b2 in reader_examples(db, users, res["books1"], res["books2"], docs):
            fmt = lambda n: f"[{n['book_id']}] {docs[n['book_id']]['title']} ({n['rating']}★)" if n else "-"
            print(f"  user {u} : {raw['g1'][u]} livres '{n1}' aimés, {raw['g2'][u]} '{n2}' ; "
                  f"ex. {fmt(b1)} / {fmt(b2)}")

    print(f"\nLivres pont (brut) :")
    print_bridges(db, raw, res["books1"], res["books2"], limit, docs)
    print(f"\nLivres pont sans livres populaires ni lecteurs actifs :")
    print_bridges(db, robust, res["books1"], res["books2"], limit, docs)
    kept = {r["book_id"] for r in raw["bridges"][:limit]} & {r["book_id"] for r in robust["bridges"][:limit]}
    print(f"\n{len(kept)}/{min(limit, len(raw['bridges']))} livres pont bruts restent dans le top {limit} "
          f"après exclusion")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="F5 : liens entre deux centres d'intérêt (tags)")
    parser.add_argument("tag1", help="tag_id ou nom exact du tag")
    parser.add_argument("tag2", help="tag_id ou nom exact du tag")
    parser.add_argument("--limit", type=int, default=F5_LIMIT, help="nombre de livres pont à afficher")
    args = parser.parse_args()

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)
    db = client[DATABASE_NAME]
    try:
        t1, t2 = find_tag(db, args.tag1), find_tag(db, args.tag2)
        for q, t in ((args.tag1, t1), (args.tag2, t2)):
            if t is None:
                print(f"Tag « {q} » inconnu.")
        if t1 is None or t2 is None:
            return
        if t1["tag_id"] == t2["tag_id"]:
            print("Les deux tags sont identiques.")
            return
        print_report(db, t1, t2, tag_link(db, t1, t2), args.limit)
    finally:
        client.close()


if __name__ == "__main__":
    main()
