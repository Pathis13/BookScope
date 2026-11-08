# Part II : effet de la répartition sur trois types de requêtes (méthode du Lab 5, T27 : explain via mongos).
#
#   1. requête centrée lecteur  : notes du lecteur 20000 (cœur de F2 / F3) ;
#   2. requête centrée livre    : lecteurs ayant aimé le livre 1 (cœur de F1 / F4 / F5) ;
#   3. parcours lecteur-livre   : livre 1 -> ses lecteurs -> les livres qu'ils ont aimés (étape de F1 / F4),
#      en deux sauts : le saut 1 est la requête 2, le saut 2 une agrégation sur la liste des lecteurs obtenue.
#
# Pour chaque requête et chaque placement (par lecteur : base bookscope, par livre : base bookscope_book) :
# shards contactés, travail fait sur chaque shard (clés / documents examinés, documents renvoyés), travail fait par
# mongos (fusion), et volume envoyé aux shards. Temps : médiane de 5 exécutions.
#
# Usage (depuis scripts/) : python dist_explain.py
# Sortie : results/dist_explain.csv

import csv
import statistics
import sys
import time

import bson
from pymongo import MongoClient

from config import CLUSTER_URI, LIKE_MIN_RATING, MONGO_TIMEOUT_MS, ROOT

PLACEMENTS = {"par lecteur (user_id)": "bookscope", "par livre (book_id)": "bookscope_book"}
USER, BOOK = 20000, 1
LIKED = {"$gte": LIKE_MIN_RATING}


def find_cmd(flt, proj):
    return {"find": "ratings", "filter": flt, "projection": proj}


def cases(db):
    likers = [r["user_id"] for r in db.ratings.find({"book_id": BOOK, "rating": LIKED}, {"_id": 0, "user_id": 1})]
    hop2 = [{"$match": {"user_id": {"$in": likers}, "rating": LIKED, "book_id": {"$ne": BOOK}}},
            {"$group": {"_id": "$book_id", "co": {"$sum": 1}}}]
    return [
        ("1. lecteur", "notes du lecteur 20000",
         find_cmd({"user_id": USER}, {"_id": 0, "book_id": 1, "rating": 1})),
        ("2. livre", "lecteurs ayant aimé le livre 1",
         find_cmd({"book_id": BOOK, "rating": LIKED}, {"_id": 0, "user_id": 1})),
        ("3. parcours, saut 2", f"livres aimés par les {len(likers)} lecteurs du livre 1",
         {"aggregate": "ratings", "pipeline": hop2, "cursor": {}}),
    ]


def run(db, cmd):
    if "find" in cmd:
        return list(db.ratings.find(cmd["filter"], cmd["projection"]))
    return list(db.ratings.aggregate(cmd["pipeline"]))


def stage_names(pipeline):
    return ">".join(next(iter(s)) for s in pipeline) if pipeline else "-"


def analyse(db, cmd):
    """Shards contactés et travail de chacun, d'après explain("executionStats") exécuté via mongos."""
    ex = db.command("explain", cmd, verbosity="executionStats")
    per_shard = {}
    if "find" in cmd:
        top = ex["executionStats"]["executionStages"]
        for s in top.get("shards", []):
            per_shard[s["shardName"]] = (s["nReturned"], s["totalKeysExamined"], s["totalDocsExamined"])
        merge, shards_part, merger_part = top["stage"], "find", "fusion des curseurs" if len(per_shard) > 1 else "-"
    else:
        for name, s in ex["shards"].items():
            st = s["executionStats"]
            per_shard[name] = (st["nReturned"], st["totalKeysExamined"], st["totalDocsExamined"])
        split = ex.get("splitPipeline") or {}
        merge = ex.get("mergeType", "?")
        shards_part = stage_names(split.get("shardsPart"))
        merger_part = stage_names(split.get("mergerPart"))
    sent = len(bson.encode(cmd))  # taille de la requête envoyée à CHAQUE shard contacté
    return per_shard, merge, shards_part, merger_part, sent


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    client = MongoClient(CLUSTER_URI, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)
    rows = []
    try:
        for placement, dbname in PLACEMENTS.items():
            db = client[dbname]
            print(f"\n=== Placement {placement} (base {dbname})")
            for case, label, cmd in cases(db):
                per_shard, merge, shards_part, merger_part, sent = analyse(db, cmd)
                times = []
                for _ in range(5):
                    t = time.perf_counter()
                    n = len(run(db, cmd))
                    times.append(time.perf_counter() - t)
                med = statistics.median(times)
                print(f"\n{case} : {label}")
                print(f"  shards contactés : {len(per_shard)} ({', '.join(per_shard)}) ; type de fusion : {merge}")
                print(f"  requête envoyée à chaque shard : {sent / 1024:.1f} Ko")
                print(f"  travail sur les shards : {shards_part}")
                for sh, (ret, keys, docs) in per_shard.items():
                    print(f"    {sh} : {keys} clés, {docs} documents examinés -> {ret} documents renvoyés à mongos")
                print(f"  travail sur mongos : {merger_part} -> {n} résultats ; médiane {med * 1000:.0f} ms")
                rows.append({"placement": placement, "case": case, "label": label, "shards": len(per_shard),
                             "merge": merge, "sent_kb": round(sent / 1024, 1), "shards_part": shards_part,
                             "merger_part": merger_part, "results": n, "median_ms": round(med * 1000, 1),
                             **{f"{sh}_returned": v[0] for sh, v in per_shard.items()},
                             **{f"{sh}_keys": v[1] for sh, v in per_shard.items()}})
    finally:
        client.close()

    (ROOT / "results").mkdir(exist_ok=True)
    fields = sorted({k for r in rows for k in r}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 99)
    with open(ROOT / "results" / "dist_explain.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print("\nRésultats : results/dist_explain.csv")


if __name__ == "__main__":
    main()
