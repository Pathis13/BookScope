# Mesures de la Part I : représentation actuelle vs représentation alternative (index couvrants).
#
# Configurations comparées sur les mêmes données (collection `ratings`) :
#   - base     : index créés par import_data.py, (user_id, book_id) unique et (book_id) ;
#   - covering : base + deux index couvrants, (book_id, rating, user_id) et (user_id, rating, book_id),
#                qui permettent de répondre aux requêtes "lecteurs ayant aimé X" et "livres aimés par ces lecteurs"
#                sans lire les documents.
#
# Pour chaque configuration :
#   - 3 requêtes élémentaires (temps + explain : étapes du plan, clés et documents examinés) ;
#   - 5 services complets (F1 à F5) sur des entrées fixes ;
#   - coût en écriture : insertion du lot F6 dans une collection temporaire portant les mêmes index ;
#   - taille des index.
# Le premier appel après le changement de configuration est noté à part ("froid"), puis REPS appels ("chaud").
# Les résultats des services sont comparés entre configurations (ils doivent être identiques).
#
# Usage (depuis scripts/) :
#   python bench.py                    (5 répétitions, puis retour à la configuration retenue, covering)
#   python bench.py --reps 10
#   python bench.py --keep base        (retirer les index couvrants à la fin)
#
# Sorties : results/bench_raw.csv (toutes les mesures), results/bench_summary.csv, results/bench_env.txt

import argparse
import csv
import os
import platform
import statistics
import sys
import time

import pandas as pd
import pymongo
from pymongo import MongoClient

import f1
import f2
import f3
import f4
import f5
from config import DATABASE_NAME, LIKE_MIN_RATING, MONGO_TIMEOUT_MS, MONGO_URI, RATINGS_UPDATE_F6, ROOT

RESULTS = ROOT / "results"

BASE_INDEXES = [([("user_id", 1), ("book_id", 1)], {"unique": True}), ([("book_id", 1)], {})]
ALT_INDEXES = {
    "book_rating_user": [("book_id", 1), ("rating", 1), ("user_id", 1)],
    "user_rating_book": [("user_id", 1), ("rating", 1), ("book_id", 1)],
}

# Entrées fixes (identiques pour les deux configurations)
BOOK = 1            # The Hunger Games : livre le plus aimé, cas le plus lourd
USER = 20000
F2_PAIR = (20000, 22534)  # 28 livres en commun
F4_PAIR = (1, 5000)
F5_PAIR = (11305, 26138)  # fantasy / romance

LIKED = {"$gte": LIKE_MIN_RATING}


# ---------------------------------------------------------------------------------------------------------------
# Configurations
# ---------------------------------------------------------------------------------------------------------------

def set_config(db, config):
    """Met `ratings` dans la configuration demandée ; renvoie le temps de création des index ajoutés."""
    existing = db.ratings.index_information()
    for name in ALT_INDEXES:
        if name in existing:
            db.ratings.drop_index(name)
    if config == "base":
        return 0.0
    t = time.perf_counter()
    for name, keys in ALT_INDEXES.items():
        db.ratings.create_index(keys, name=name)
    return time.perf_counter() - t


def index_sizes(db):
    s = db.command("collStats", "ratings")
    return s["indexSizes"], s["totalIndexSize"], s["storageSize"]


def write_cost(db, config, records):
    """Insère le lot F6 dans une collection temporaire portant les index de la configuration (coût pour F6)."""
    tmp = db.bench_write
    tmp.drop()
    for keys, opts in BASE_INDEXES:
        tmp.create_index(keys, **opts)
    if config == "covering":
        for name, keys in ALT_INDEXES.items():
            tmp.create_index(keys, name=name)
    docs = [dict(r) for r in records]
    t = time.perf_counter()
    tmp.insert_many(docs, ordered=False)
    elapsed = time.perf_counter() - t
    tmp.drop()
    return elapsed


# ---------------------------------------------------------------------------------------------------------------
# Requêtes mesurées : chacune renvoie une "empreinte" de son résultat, comparée entre configurations
# ---------------------------------------------------------------------------------------------------------------

def likers_filter():
    return {"book_id": BOOK, "rating": LIKED}


def co_liked_pipeline(db):
    likers = [r["user_id"] for r in db.ratings.find(likers_filter(), {"_id": 0, "user_id": 1})]
    return [{"$match": {"user_id": {"$in": likers}, "rating": LIKED, "book_id": {"$ne": BOOK}}},
            {"$group": {"_id": "$book_id", "co": {"$sum": 1}}}]


def elementary(db):
    """Requêtes élémentaires : (nom, type, spécification pour explain, fonction)."""
    co_pipeline = co_liked_pipeline(db)
    return [
        ("likers", "find", {"filter": likers_filter(), "projection": {"_id": 0, "user_id": 1}},
         lambda: sorted(r["user_id"] for r in db.ratings.find(likers_filter(), {"_id": 0, "user_id": 1}))),
        ("user_notes", "find", {"filter": {"user_id": USER}, "projection": {"_id": 0, "book_id": 1, "rating": 1}},
         lambda: sorted((r["book_id"], r["rating"]) for r in
                        db.ratings.find({"user_id": USER}, {"_id": 0, "book_id": 1, "rating": 1}))),
        ("co_liked", "aggregate", {"pipeline": co_pipeline},
         lambda: sorted((d["_id"], d["co"]) for d in db.ratings.aggregate(co_pipeline))),
    ]


def services(db):
    book = db.books.find_one({"book_id": BOOK})
    t1, t2 = db.tags.find_one({"tag_id": F5_PAIR[0]}), db.tags.find_one({"tag_id": F5_PAIR[1]})

    def run_f4():
        stats = f4.activity_stats(db)
        return [p["books"] for p in f4.find_paths(db, *F4_PAIR, stats)["paths"]]

    def run_f5():
        r = f5.tag_link(db, t1, t2)
        return [(len(x["both"]), round(x["lift"], 6), [b["book_id"] for b in x["bridges"][:10]])
                for x in (r["raw"], r["robust"])]

    return [
        ("F2", "service", None, lambda: f2.calculate_similarity(db, *F2_PAIR)),
        ("F1", "service", None,
         lambda: [(r["book"]["book_id"], round(r["score"], 6)) for r in f1.similar_books(db, book)["results"]]),
        ("F3", "service", None,
         lambda: [(c["book"]["book_id"], c["count"]) for c in f3.recommend(db, USER)["results"]]),
        ("F4", "service", None, run_f4),
        ("F5", "service", None, run_f5),
    ]


def explain(db, kind, spec):
    """Étapes du plan gagnant, index utilisés, clés et documents examinés."""
    if kind == "find":
        cmd = {"find": "ratings", "filter": spec["filter"], "projection": spec["projection"]}
    else:
        cmd = {"aggregate": "ratings", "pipeline": spec["pipeline"], "cursor": {}}
    res = db.command("explain", cmd, verbosity="executionStats")
    stages, indexes, stats = [], [], {}

    def walk(node, in_plan=False):
        if isinstance(node, dict):
            if in_plan and "stage" in node:
                stages.append(node["stage"])
                if "indexName" in node:
                    indexes.append(node["indexName"])
            if "totalDocsExamined" in node and not stats:
                stats.update(keys=node.get("totalKeysExamined"), docs=node["totalDocsExamined"],
                             returned=node.get("nReturned"))
            for k, v in node.items():
                walk(v, in_plan or k == "winningPlan")
        elif isinstance(node, list):
            for v in node:
                walk(v, in_plan)

    walk(res)
    return {"stages": ">".join(dict.fromkeys(stages)), "indexes": ",".join(dict.fromkeys(indexes)), **stats}


# ---------------------------------------------------------------------------------------------------------------
# Mesures
# ---------------------------------------------------------------------------------------------------------------

def timed(fn):
    t = time.perf_counter()
    out = fn()
    return time.perf_counter() - t, out


def run_config(db, config, reps, records, raw_rows, summary_rows, fingerprints):
    print(f"\n=== Configuration {config}")
    creation = set_config(db, config)
    sizes, total_idx, storage = index_sizes(db)
    print(f"  création des index ajoutés : {creation:.2f} s ; taille totale des index {total_idx / 2**20:.1f} Mo "
          f"(données {storage / 2**20:.1f} Mo)")
    for name, size in sizes.items():
        print(f"    {name:<22} {size / 2**20:6.1f} Mo")
    summary_rows.append({"config": config, "request": "index_creation", "median_s": round(creation, 4),
                         "index_size_mb": round(total_idx / 2**20, 2)})

    write = [write_cost(db, config, records) for _ in range(3)]
    print(f"  écriture du lot F6 ({len(records)} notes) : médiane {statistics.median(write):.2f} s")
    for i, w in enumerate(write):
        raw_rows.append({"config": config, "request": "write_F6", "rep": i, "cold": False, "seconds": w})
    summary_rows.append({"config": config, "request": "write_F6", "median_s": round(statistics.median(write), 4),
                         "stdev_s": round(statistics.stdev(write), 4), "min_s": round(min(write), 4),
                         "max_s": round(max(write), 4)})

    for name, kind, spec, fn in elementary(db) + services(db):
        cold, out = timed(fn)
        fingerprints.setdefault(name, {})[config] = out
        warm = [timed(fn)[0] for _ in range(reps)]
        raw_rows.append({"config": config, "request": name, "rep": 0, "cold": True, "seconds": cold})
        raw_rows += [{"config": config, "request": name, "rep": i + 1, "cold": False, "seconds": w}
                     for i, w in enumerate(warm)]
        row = {"config": config, "request": name, "cold_s": round(cold, 4),
               "median_s": round(statistics.median(warm), 4),
               "stdev_s": round(statistics.stdev(warm), 4) if reps > 1 else 0,
               "min_s": round(min(warm), 4), "max_s": round(max(warm), 4)}
        if kind != "service":
            row.update(explain(db, kind, spec))
        summary_rows.append(row)
        plan = f"  [{row.get('stages')} / {row.get('indexes')} ; clés {row.get('keys')}, docs {row.get('docs')}]" \
            if kind != "service" else ""
        print(f"  {name:<11} froid {cold:7.3f} s | chaud médiane {row['median_s']:7.3f} s "
              f"(écart-type {row['stdev_s']:.3f}){plan}")


def environment(db):
    info = db.client.server_info()
    wt = db.command("serverStatus")["wiredTiger"]["cache"]["maximum bytes configured"]
    return [
        f"date : {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"machine : {platform.platform()}, {platform.processor()}, {os.cpu_count()} threads",
        f"MongoDB {info['version']} (cache WiredTiger {wt / 2**30:.1f} Go), Python {platform.python_version()}, "
        f"pymongo {pymongo.__version__}, pandas {pd.__version__}",
        f"ratings : {db.ratings.estimated_document_count()} documents",
        f"entrées : livre {BOOK}, lecteur {USER}, F2 {F2_PAIR}, F4 {F4_PAIR}, F5 tags {F5_PAIR}",
    ]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Mesures base vs index couvrants")
    parser.add_argument("--reps", type=int, default=5, help="nombre d'appels à chaud par requête")
    parser.add_argument("--keep", choices=["base", "covering"], default="covering", help="configuration laissée à la fin (covering = retenue)")
    args = parser.parse_args()

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGO_TIMEOUT_MS)
    db = client[DATABASE_NAME]
    RESULTS.mkdir(exist_ok=True)
    records = pd.read_csv(RATINGS_UPDATE_F6).to_dict(orient="records")
    raw_rows, summary_rows, fingerprints = [], [], {}
    try:
        env = environment(db)
        print("\n".join(env))
        for config in ("base", "covering"):
            run_config(db, config, args.reps, records, raw_rows, summary_rows, fingerprints)

        print("\n=== Résultats identiques entre configurations ?")
        for name, by_config in fingerprints.items():
            same = by_config["base"] == by_config["covering"]
            print(f"  {name:<11} {'oui' if same else 'NON'}")
            env.append(f"résultat identique {name} : {same}")
    finally:
        set_config(db, args.keep)
        client.close()

    with open(RESULTS / "bench_raw.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["config", "request", "rep", "cold", "seconds"])
        w.writeheader()
        w.writerows(raw_rows)
    fields = ["config", "request", "cold_s", "median_s", "stdev_s", "min_s", "max_s", "stages", "indexes", "keys",
              "docs", "returned", "index_size_mb"]
    with open(RESULTS / "bench_summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(summary_rows)
    (RESULTS / "bench_env.txt").write_text("\n".join(env) + "\n", encoding="utf-8")
    print(f"\nMesures écrites dans {RESULTS}")


if __name__ == "__main__":
    main()
