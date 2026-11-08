# Part II : vérification automatique que les déploiements donnent des résultats identiques.
#
# Les services F1 à F5 sont lancés avec les mêmes entrées sur :
#   - local        : serveur unique (port 27017, base bookscope) = référence ;
#   - cluster      : cluster via mongos (port 27400), notes réparties par lecteur (base bookscope) ;
#   - cluster_book : même cluster, placement alternatif, notes réparties par livre (base bookscope_book).
# Les trois bases sont chargées par import_data.py à partir des mêmes CSV (mêmes enregistrements).
#
# Chaque résultat est mis sous forme canonique avant comparaison : nombres arrondis à 1e-9 (l'ordre des additions
# change quand les $group sont faits par morceaux sur plusieurs shards), listes triées par score puis identifiant
# (égalités départagées de façon déterministe). Les entrées comprennent des lecteurs et des livres stockés sur
# des shards différents ; le shard de chaque lecteur / livre est affiché (explain de la requête ciblée).
#
# Usage (depuis scripts/) :
#   python compare.py                       (les trois déploiements)
#   python compare.py --only cluster        (local vs un seul déploiement)
# Sortie : results/compare_report.csv ; code de retour 1 si une différence est trouvée.

import statistics
import argparse
import csv
import sys
import time

from pymongo import MongoClient

import f1
import f2
import f3
import f4
import f5
from config import CLUSTER_URI, LOCAL_URI, MONGO_TIMEOUT_MS, ROOT

DEPLOYMENTS = {
    "local": (LOCAL_URI, "bookscope"),
    "cluster": (CLUSTER_URI, "bookscope"),
    "cluster_book": (CLUSTER_URI, "bookscope_book"),
}

# Entrées fixes. Placement par lecteur : 20000 et 26485 sur shard2RS, 22534 et 29999 sur shard1RS.
# Placement par livre : livres 1, 4000, 5000, 1129 sur shard1RS, 9000 sur shard2RS.
CASES = [
    ("F1", (1,)), ("F1", (9000,)), ("F1", (1129,)),
    ("F2", (20000, 22534)),         # lecteurs sur deux shards différents
    ("F2", (20000, 26485)),         # lecteurs sur le même shard
    ("F3", (20000, None)), ("F3", (29999, None)),
    ("F3", (20000, 5)),             # historique tronqué aux 5 premières notes
    ("F4", (1, 5000)), ("F4", (9000, 4000)), ("F4", (1129, 1)),
    ("F5", (11305, 26138)),         # fantasy / romance
    ("F5", (31745, 22973)),         # vampires / paranormal
]

R = 9  # arrondi des nombres réels


def r(x):
    return round(x, R) if isinstance(x, float) else x


# ---------------------------------------------------------------------------------------------------------------
# Formes canoniques
# ---------------------------------------------------------------------------------------------------------------

def run_f1(db, book_id):
    res = f1.similar_books(db, db.books.find_one({"book_id": book_id}))
    rows = [(x["book"]["book_id"], r(x["score"]), (x["readers"] or {}).get("co")) for x in res["results"]]
    return {"n_likers": res["n_likers"], "results": sorted(rows, key=lambda t: (-t[1], t[0]))}


def run_f2(db, u1, u2):
    return {k: r(v) for k, v in f2.calculate_similarity(db, u1, u2).items()}


def run_f3(db, user, truncate):
    res = f3.recommend(db, user, truncate=truncate)
    rows = [(c["book"]["book_id"], c["count"], r(c["f1_sum"])) for c in res["results"]]
    return {"seeds": res["seeds"], "results": sorted(rows, key=lambda t: (-t[1], -t[2], t[0]))}


def run_f4(db, a, b):
    stats = f4.activity_stats(db)
    res = f4.find_paths(db, a, b, stats)
    paths = [(tuple(p["books"]), r(p["score"]), tuple(s["co"] for s in p["steps"])) for p in res["paths"]]
    return {"total": res["total"], "n_a": res["n_a"], "n_b": res["n_b"], "blocked": sorted(res["blocked"]),
            "paths": sorted(paths, key=lambda t: (len(t[0]), -t[1], t[0]))}


def run_f5(db, t1, t2):
    res = f5.tag_link(db, db.tags.find_one({"tag_id": t1}), db.tags.find_one({"tag_id": t2}))
    out = {}
    for k in ("raw", "robust"):
        x = res[k]
        bridges = sorted(((b["book_id"], r(b["score"]), b["a"], b["b"]) for b in x["bridges"]),
                         key=lambda t: (-t[1], t[0]))
        out[k] = {"g1": len(x["g1"]), "g2": len(x["g2"]), "both": len(x["both"]), "lift": r(x["lift"]),
                  "bridges": bridges}
    out["only_lift"] = r(res["only"].get("lift")) if res["only"] else None
    return out


RUN = {"F1": run_f1, "F2": run_f2, "F3": run_f3, "F4": run_f4, "F5": run_f5}


# ---------------------------------------------------------------------------------------------------------------
# Localisation des enregistrements
# ---------------------------------------------------------------------------------------------------------------

def owner(db, field, value):
    """Shards visés par la requête {field: value} sur ratings (via explain sur mongos)."""
    plan = db.command("explain", {"find": "ratings", "filter": {field: value}}, verbosity="queryPlanner")
    return "+".join(s["shardName"] for s in plan["queryPlanner"]["winningPlan"].get("shards", []))


def locations(dbs):
    """Où sont stockées les notes des lecteurs (placement par lecteur) et des livres (placement par livre)."""
    loc = []
    if "cluster" in dbs:
        users = sorted({u for s, a in CASES if s in ("F2", "F3") for u in (a if s == "F2" else a[:1])})
        loc += [f"lecteur {u} -> {owner(dbs['cluster'], 'user_id', u)}" for u in users]
    if "cluster_book" in dbs:
        books = sorted({b for s, a in CASES if s in ("F1", "F4") for b in a})
        loc += [f"livre {b} -> {owner(dbs['cluster_book'], 'book_id', b)}" for b in books]
    return loc


def first_diff(a, b, path="résultat"):
    """Premier endroit où deux formes canoniques diffèrent (pour le rapport)."""
    if type(a) is not type(b):
        return f"{path} : {a!r} != {b!r}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=str):
            if a.get(k) != b.get(k):
                return first_diff(a.get(k), b.get(k), f"{path}.{k}")
    elif isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return f"{path} : {len(a)} éléments != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                return first_diff(x, y, f"{path}[{i}]")
    return f"{path} : {a!r} != {b!r}"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Comparaison répétée des déploiements")
    parser.add_argument("--only", choices=["cluster", "cluster_book"], help="ne comparer qu'un déploiement à local")
    parser.add_argument("--reps", type=int, default=5, help="nombre de répétitions mesurées après échauffement (défaut : 5)")
    args = parser.parse_args()
    if args.reps < 1:
        parser.error("--reps doit être supérieur ou égal à 1")
    names = ["local"] + ([args.only] if args.only else ["cluster", "cluster_book"])

    clients = {n: MongoClient(DEPLOYMENTS[n][0], serverSelectionTimeoutMS=MONGO_TIMEOUT_MS) for n in names}
    dbs = {n: clients[n][DEPLOYMENTS[n][1]] for n in names}
    raw_rows, nb_diff = [], 0

    def record(service, inputs, deployment, run_type, rep, elapsed, output, reference):
        nonlocal nb_diff
        same = output == reference
        if not same:
            nb_diff += 1
        raw_rows.append({
            "service": service, "input": repr(inputs), "deployment": deployment,
            "run_type": run_type, "rep": rep, "seconds": round(elapsed, 6),
            "identical": same,
            "diff": "" if same else first_diff(reference, output),
        })
        return same

    try:
        for n in names:
            print(f"{n:<13}: {DEPLOYMENTS[n][0]} base {DEPLOYMENTS[n][1]}, "
                  f"{dbs[n].ratings.estimated_document_count()} notes")
        print("\nLocalisation des enregistrements utilisés :")
        for line in locations(dbs):
            print(f"  {line}")

        print(f"\nMesures : 1 premier appel + 1 échauffement + {args.reps} répétitions mesurées")
        print("Le premier appel n'est pas une garantie de cache MongoDB/OS froid.")
        for service, inputs in CASES:
            # Référence locale calculée une fois, puis chronométrage de chaque déploiement.
            reference = RUN[service](dbs["local"], *inputs)
            for n in names:
                # Premier appel mesuré (distinct d'un véritable vidage de cache).
                t0 = time.perf_counter()
                output = RUN[service](dbs[n], *inputs)
                dt = time.perf_counter() - t0
                first_dt = dt
                same = record(service, inputs, n, "first_call", 1, dt, output, reference)
                if not same:
                    print(f"DIFF {service} {inputs} {n} premier appel : {first_diff(reference, output)}")

                # Échauffement non mesuré.
                warm_output = RUN[service](dbs[n], *inputs)
                if not record(service, inputs, n, "warmup", 0, 0.0, warm_output, reference):
                    print(f"DIFF {service} {inputs} {n} échauffement : {first_diff(reference, warm_output)}")

                hot_times = []
                for rep in range(1, args.reps + 1):
                    t0 = time.perf_counter()
                    output = RUN[service](dbs[n], *inputs)
                    dt = time.perf_counter() - t0
                    hot_times.append(dt)
                    same = record(service, inputs, n, "hot", rep, dt, output, reference)
                    if not same:
                        print(f"DIFF {service} {inputs} {n} répétition {rep} : {first_diff(reference, output)}")
                med = statistics.median(hot_times)
                print(f"{service:<3} {str(inputs):<18} {n:<13} premier={first_dt:.3f}s "
                      f"médiane_hot={med:.3f}s")
    finally:
        for c in clients.values():
            c.close()

    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    raw_path = out_dir / "compare_timing_raw.csv"
    with open(raw_path, "w", newline="", encoding="utf-8") as f:
        fields = ["service", "input", "deployment", "run_type", "rep", "seconds", "identical", "diff"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(raw_rows)

    # Statistiques sur les répétitions hot uniquement (mêmes entrées regroupées).
    groups = {}
    for row in raw_rows:
        if row["run_type"] == "hot":
            key = (row["service"], row["input"], row["deployment"])
            groups.setdefault(key, []).append(float(row["seconds"]))
    summary_rows = []
    for (service, inputs, deployment), values in groups.items():
        summary_rows.append({
            "service": service, "input": inputs, "deployment": deployment,
            "reps": len(values), "mean_s": round(statistics.mean(values), 6),
            "median_s": round(statistics.median(values), 6),
            "stdev_s": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
            "min_s": round(min(values), 6), "max_s": round(max(values), 6),
        })
    summary_path = out_dir / "compare_timing_summary.csv"
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        fields = ["service", "input", "deployment", "reps", "mean_s", "median_s", "stdev_s", "min_s", "max_s"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(summary_rows)

    total_checks = len(raw_rows)
    ok_checks = sum(1 for row in raw_rows if row["identical"])
    print(f"\n{ok_checks}/{total_checks} résultats identiques à la référence locale.")
    print(f"Mesures brutes : {raw_path}")
    print(f"Synthèse des répétitions hot : {summary_path}")
    sys.exit(1 if nb_diff else 0)


if __name__ == "__main__":
    main()
