# F3 : recommander des livres à un lecteur, à partir de F1.
#
# On prend les F3_SEEDS livres les mieux notés du lecteur (livres "graines") et, pour chacun, les
# F3_SIMILAR_PER_SEED livres que F1 juge les plus proches (hors livres déjà notés). Chaque apparition donne +1 :
# un livre proche de plusieurs livres aimés par le lecteur remonte.
#
# Usage (depuis scripts/) :
#   python f3.py 20000                  (recommandations pour le lecteur 20000)
#   python f3.py 20000 --limit 20       (nombre de recommandations)
#   python f3.py 20000 --truncate 5     (historique réduit aux 5 premières notes du lecteur)

import argparse
import contextlib
import io
import statistics
import sys

from pymongo import MongoClient

import availability
import f1
from config import (DATABASE_NAME, F3_LIMIT, F3_SEEDS, F3_SIMILAR_PER_SEED, LIKE_MIN_RATING, MONGO_TIMEOUT_MS,
                    MONGO_URI)


# ---------------------------------------------------------------------------------------------------------------
# Historique du lecteur
# ---------------------------------------------------------------------------------------------------------------

def user_history(db, user_id, truncate=None):
    """Notes du lecteur dans l'ordre chronologique : liste de (book_id, rating).

    L'ordre d'insertion (_id) reprend l'ordre des lignes de ratings.csv, qui est l'ordre temporel d'après la
    documentation. Avec `truncate`, on ne garde que les k premières notes (historique court simulé).
    """
    history = [(r["book_id"], r["rating"])
               for r in db.ratings.find({"user_id": user_id},
                                        {"book_id": 1, "rating": 1})
                                        .sort("_id", 1)]
    return history[:truncate] if truncate else history


def pick_seeds(history):
    """Livres "graines" : les mieux notés (>= LIKE_MIN_RATING), les plus récents d'abord en cas d'égalité."""
    liked = [(pos, b, r) for pos, (b, r) in enumerate(history) if r >= LIKE_MIN_RATING]
    liked.sort(key=lambda x: (-x[2], -x[0]))
    return [(b, r) for _, b, r in liked[:F3_SEEDS]]


# ---------------------------------------------------------------------------------------------------------------
# Recommandation
# ---------------------------------------------------------------------------------------------------------------

def recommend(db, user_id, limit=F3_LIMIT, truncate=None):
    """Pour chaque graine, F1 donne ses livres similaires non notés ; chaque apparition compte +1.

    Classement : nb d'apparitions décroissant, puis somme des scores F1, puis book_id (ordre déterministe).
    """
    history = user_history(db, user_id, truncate)
    rated = {b for b, _ in history}
    seeds = pick_seeds(history)
    notes = []
    if not history:
        notes.append("Aucune note pour ce lecteur : pas de recommandation personnalisée possible.")
    elif not seeds:
        notes.append(f"Aucun livre noté >= {LIKE_MIN_RATING} : pas de livre graine, pas de recommandation.")

    seed_docs = {b["book_id"]: b for b in db.books.find({"book_id": {"$in": [s for s, _ in seeds]}})}
    cands = {}
    for seed_id, rating in seeds:
        res = f1.similar_books(db, seed_docs[seed_id], limit=F3_SIMILAR_PER_SEED, exclude=rated, evidence=False)
        for r in res["results"]:
            c = cands.setdefault(r["book"]["book_id"], {"book": r["book"], "count": 0, "f1_sum": 0.0, "seeds": []})
            c["count"] += 1
            c["f1_sum"] += r["score"]
            c["seeds"].append((seed_id, rating, r))

    results = sorted(cands.values(), key=lambda c: (-c["count"], -c["f1_sum"], c["book"]["book_id"]))[:limit]
    return {"history": history, "seeds": seeds, "seed_docs": seed_docs, "results": results, "notes": notes}


def popularity_stats(db, results, rated):
    """Écart à la popularité : part des recommandations dans le top 100 des livres les plus notés de la base,
    et popularité médiane (nombre de notes dans la base) comparée au catalogue et à une recommandation par popularité."""
    counts = {d["_id"]: d["n"] for d in db.ratings.aggregate([{"$group": {"_id": "$book_id", "n": {"$sum": 1}}}])}
    ranking = sorted(counts, key=lambda b: (-counts[b], b))
    top100 = set(ranking[:100])
    rec_ids = [r["book"]["book_id"] for r in results]
    baseline = [b for b in ranking if b not in rated][:len(rec_ids)]  # "les plus populaires non lus"
    return {
        "in_top100": sum(1 for b in rec_ids if b in top100),
        "median_rec": statistics.median(counts.get(b, 0) for b in rec_ids) if rec_ids else 0,
        "median_catalogue": statistics.median(counts.values()) if counts else 0,
        "median_baseline": statistics.median(counts[b] for b in baseline) if baseline else 0,
    }


# ---------------------------------------------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------------------------------------------

def link_reason(r):
    """Résumé court de la raison pour laquelle F1 relie la graine au livre recommandé."""
    reasons = []
    if r["series"]:
        reasons.append("même série")
    if r["authors"]:
        reasons.append("même auteur")
    if r["readers"]:
        reasons.append(f"{r['readers']['co']} lecteurs communs (x{r['readers']['lift']:.1f} vs hasard)")
    if r["s_tags"] > 0:
        reasons.append(f"tags spécifiques communs ({r['s_tags']:.0%} du poids thématique)")
    return ", ".join(reasons) or "score F1"


def print_report(db, user_id, res, truncate):
    history = res["history"]
    label = f" (historique TRONQUÉ aux {truncate} premières notes du lecteur réel {user_id})" if truncate else ""
    print(f"Lecteur {user_id}{label} : {len(history)} notes utilisées")
    for note in res["notes"]:
        print(f"  ! {note}")
    if not res["seeds"]:
        return

    docs = res["seed_docs"]
    print(f"\nLivres graines (les {len(res['seeds'])} mieux notés, envoyés à F1) :")
    for b, r in res["seeds"]:
        print(f"  [{b}] {docs[b]['title']} : {r}★")

    print(f"\nRecommandations :")
    for i, c in enumerate(res["results"], 1):
        b = c["book"]
        print(f"\n{i:>2}. [{b['book_id']}] {b['title']} - {b['authors']}")
        print(f"    Score {c['count']} (similaire à {c['count']} de vos {len(res['seeds'])} livres graines, "
              f"somme des scores F1 {c['f1_sum']:.2f})")
        for seed_id, rating, r in c["seeds"]:
            print(f"      - [{seed_id}] {docs[seed_id]['title']} ({rating}★) : {link_reason(r)}")

    stats = popularity_stats(db, res["results"], {bk for bk, _ in history})
    print(f"\nÉcart à la popularité :")
    print(f"  {stats['in_top100']}/{len(res['results'])} recommandations parmi les 100 livres les plus notés de la base")
    print(f"  Popularité médiane (nb de notes dans la base) : recommandations {stats['median_rec']:.0f}, "
          f"catalogue {stats['median_catalogue']:.0f}, recommandation par popularité {stats['median_baseline']:.0f}")
    print(f"\nTracer une recommandation : python f1.py <graine> --trace <book_id>")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="F3 : recommander des livres à un lecteur")
    parser.add_argument("user_id", type=int)
    parser.add_argument("--limit", type=int, default=F3_LIMIT, help="nombre de recommandations")
    parser.add_argument("--truncate", type=int, metavar="K", help="n'utiliser que les K premières notes du lecteur")
    args = parser.parse_args()

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)
    db = client[DATABASE_NAME]
    try:
        # Cluster (Part II) : refuser de répondre si une instance utile est injoignable (pas de résultat partiel)
        if not availability.check(db, "F3", readers=[args.user_id], needs_all=True):
            sys.exit(2)
        if not db.ratings.find_one({"user_id": args.user_id}):
            print(f"Lecteur {args.user_id} inconnu (aucune note dans la base).")
            return

        def compute():
            # le rapport est préparé en entier avant d'être affiché : une erreur en cours de route n'affiche rien
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                print_report(db, args.user_id, recommend(db, args.user_id, args.limit, args.truncate),
                             args.truncate)
            return out.getvalue()

        ok, report = availability.run_or_fail("F3", compute)
        if not ok:
            sys.exit(2)
        print(report, end="")
    finally:
        client.close()


if __name__ == "__main__":
    main()
