# Part II : lancer et piloter le cluster MongoDB distribué (même architecture que le Lab 5, parties 5 à 7).
#
#   - 1 serveur de configuration : replica set "configRS" à un membre, port 27200 ;
#   - N shards (2 par défaut)    : replica sets "shard1RS", "shard2RS"... à un membre, ports 27301, 27302... ;
#   - 1 routeur mongos           : port 27400, seul point d'entrée de l'application.
# Chaque processus a son propre port et son propre dossier de données (data/cluster/...), comme dans le Lab 5.
# Comme dans le Lab, chaque shard est un replica set à UN SEUL membre : il n'y a aucune copie des données.
#
# mongosh n'est pas installé : les commandes du Lab (rs.initiate, sh.addShard, sh.status) sont envoyées
# depuis Python avec leurs équivalents (replSetInitiate, addShard, listShards).
#
# Usage (depuis scripts/) :
#   python cluster.py start              (lance et initialise config + 2 shards + mongos)
#   python cluster.py start --shards 3   (3 shards, comme la Part 7 du Lab)
#   python cluster.py status             (shards, état des processus, répartition des notes)
#   python cluster.py stop-shard 2       (arrête le shard 2 : test de perte d'une instance)
#   python cluster.py start-shard 2      (relance le shard 2 avec les mêmes données)
#   python cluster.py stop               (arrête tous les processus du cluster)
#   python cluster.py reset              (arrête tout et supprime data/cluster)

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError

from config import CLUSTER_DATA

BIN = Path(r"C:\Program Files\MongoDB\Server\9.0\bin")
CONFIG_PORT = 27200
SHARD_PORT = 27300  # shard i -> 27300 + i
MONGOS_PORT = 27400


def exe(name):
    path = BIN / f"{name}.exe"
    return str(path) if path.exists() else name  # sinon, on compte sur le PATH


def client(port, direct=True, timeout=3000):
    return MongoClient(f"mongodb://localhost:{port}/", directConnection=direct, serverSelectionTimeoutMS=timeout)


def is_up(port):
    try:
        with client(port, timeout=1000) as c:
            c.admin.command("ping")
        return True
    except PyMongoError:
        return False


def wait_up(port, timeout=60):
    t = time.time()
    while not is_up(port):
        if time.time() - t > timeout:
            sys.exit(f"Le processus du port {port} ne répond pas (voir {CLUSTER_DATA / 'logs'}).")
        time.sleep(0.5)


def spawn(name, args):
    """Lance un processus détaché (il survit à ce script), journal dans data/cluster/logs/<name>.log."""
    logs = CLUSTER_DATA / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(args + ["--logpath", str(logs / f"{name}.log"), "--logappend"],
                     creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def init_replset(port, name, configsvr=False):
    """rs.initiate() à un membre, puis attente de l'état PRIMARY (Lab 5, T19 / T20)."""
    with client(port) as c:
        try:
            conf = {"_id": name, "members": [{"_id": 0, "host": f"localhost:{port}"}]}
            if configsvr:
                conf["configsvr"] = True
            c.admin.command("replSetInitiate", conf)
        except OperationFailure as e:
            if "already initialized" not in str(e):
                raise
        while not c.admin.command("hello").get("isWritablePrimary"):
            time.sleep(0.5)


def start_shard(i):
    port, data = SHARD_PORT + i, CLUSTER_DATA / f"shard{i}"
    if is_up(port):
        print(f"  shard{i}RS déjà lancé (port {port})")
        return
    data.mkdir(parents=True, exist_ok=True)
    # Juste après stop-shard, le port ne répond plus mais mongod n'a pas encore libéré son verrou : relancé
    # trop tôt, le nouveau processus s'arrête aussitôt. mongod vide mongod.lock à la fin de son arrêt.
    lock, t = data / "mongod.lock", time.time()
    while lock.exists() and lock.stat().st_size > 0 and time.time() - t < 30:
        time.sleep(0.5)
    spawn(f"shard{i}", [exe("mongod"), "--shardsvr", "--replSet", f"shard{i}RS", "--dbpath", str(data),
                        "--port", str(port), "--bind_ip", "localhost"])
    wait_up(port)
    init_replset(port, f"shard{i}RS")
    print(f"  shard{i}RS : PRIMARY sur le port {port}")


def start(nb_shards):
    print("Serveur de configuration")
    if not is_up(CONFIG_PORT):
        (CLUSTER_DATA / "config").mkdir(parents=True, exist_ok=True)
        spawn("config", [exe("mongod"), "--configsvr", "--replSet", "configRS", "--dbpath",
                         str(CLUSTER_DATA / "config"), "--port", str(CONFIG_PORT), "--bind_ip", "localhost"])
        wait_up(CONFIG_PORT)
    init_replset(CONFIG_PORT, "configRS", configsvr=True)
    print(f"  configRS : PRIMARY sur le port {CONFIG_PORT}")

    print("Shards")
    for i in range(1, nb_shards + 1):
        start_shard(i)

    print("Routeur")
    if not is_up(MONGOS_PORT):
        spawn("mongos", [exe("mongos"), "--configdb", f"configRS/localhost:{CONFIG_PORT}", "--port",
                         str(MONGOS_PORT), "--bind_ip", "localhost"])
        wait_up(MONGOS_PORT)
    print(f"  mongos sur le port {MONGOS_PORT}")

    with client(MONGOS_PORT, direct=False, timeout=10000) as c:
        known = {s["_id"] for s in c.admin.command("listShards")["shards"]}
        for i in range(1, nb_shards + 1):
            if f"shard{i}RS" not in known:
                c.admin.command("addShard", f"shard{i}RS/localhost:{SHARD_PORT + i}")  # sh.addShard (T22, T29)
    status()


def shutdown(port):
    try:
        with client(port, timeout=2000) as c:
            c.admin.command("shutdown", force=True)
    except PyMongoError:
        pass  # la connexion se ferme pendant l'arrêt : c'est attendu
    t = time.time()
    while is_up(port) and time.time() - t < 30:
        time.sleep(0.5)


def running_shards():
    return [i for i in range(1, 10) if (CLUSTER_DATA / f"shard{i}").exists()]


def stop():
    shutdown(MONGOS_PORT)
    for i in running_shards():
        shutdown(SHARD_PORT + i)
    shutdown(CONFIG_PORT)
    print("Cluster arrêté.")


def status():
    print("\nProcessus :")
    print(f"  config  {CONFIG_PORT} : {'en marche' if is_up(CONFIG_PORT) else 'ARRÊTÉ'}")
    for i in running_shards():
        print(f"  shard{i}  {SHARD_PORT + i} : {'en marche' if is_up(SHARD_PORT + i) else 'ARRÊTÉ'}")
    print(f"  mongos  {MONGOS_PORT} : {'en marche' if is_up(MONGOS_PORT) else 'ARRÊTÉ'}")
    if not is_up(MONGOS_PORT):
        return
    with client(MONGOS_PORT, direct=False, timeout=5000) as c:
        print("\nShards connus du cluster (sh.status) :")
        for s in c.admin.command("listShards")["shards"]:
            print(f"  {s['_id']} -> {s['host']}")
        print("\nCollections réparties :")
        for coll in c.config.collections.find({"key": {"$exists": True}, "_id": {"$regex": r"^bookscope"}}):
            chunks = list(c.config.chunks.aggregate([{"$match": {"uuid": coll["uuid"]}},
                                                     {"$group": {"_id": "$shard", "n": {"$sum": 1}}},
                                                     {"$sort": {"_id": 1}}]))
            dbname, name = coll["_id"].split(".", 1)
            # équivalent de db.collection.getShardDistribution() (Lab 5, T25)
            docs = {d["shard"]: d["storageStats"]["count"] for d in
                    c[dbname][name].aggregate([{"$collStats": {"storageStats": {}}}])}
            print(f"  {coll['_id']} : clé {dict(coll['key'])} ; "
                  + ", ".join(f"{d['_id']} {d['n']} chunk(s) / {docs.get(d['_id'], 0)} documents" for d in chunks))
        print("\nShard primaire de chaque base (catalogue non réparti : books, book_tags, tags) :")
        for d in c.config.databases.find({"_id": {"$regex": r"^bookscope"}}):
            print(f"  {d['_id']} -> {d['primary']}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Cluster MongoDB distribué de BookScope (Lab 5)")
    parser.add_argument("action", choices=["start", "status", "stop", "stop-shard", "start-shard", "reset"])
    parser.add_argument("shard", nargs="?", type=int, help="numéro du shard (stop-shard / start-shard)")
    parser.add_argument("--shards", type=int, default=2, help="nombre de shards au démarrage")
    args = parser.parse_args()

    if args.action == "start":
        start(args.shards)
    elif args.action == "status":
        status()
    elif args.action == "stop":
        stop()
    elif args.action == "reset":
        stop()
        shutil.rmtree(CLUSTER_DATA, ignore_errors=True)
        print(f"{CLUSTER_DATA} supprimé.")
    elif args.action == "stop-shard":
        shutdown(SHARD_PORT + args.shard)
        print(f"shard{args.shard}RS (port {SHARD_PORT + args.shard}) arrêté.")
    elif args.action == "start-shard":
        start_shard(args.shard)


if __name__ == "__main__":
    main()
