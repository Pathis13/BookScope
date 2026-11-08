# Part II : disponibilité des instances et localisation des notes d'un lecteur (perte d'une instance).
#
# Ces informations viennent du serveur de configuration via mongos (config.shards, config.chunks), qui reste
# joignable quand un shard est arrêté : on sait donc AVANT de calculer si un résultat serait incomplet.
# Sur un serveur unique (déploiement local), rien n'est vérifié.
#
# Politique : un service qui a besoin de notes stockées sur un shard injoignable ÉCHOUE explicitement ; aucun
# résultat partiel n'est affiché comme s'il était complet. Si un shard tombe pendant le calcul, l'erreur MongoDB
# est aussi transformée en échec explicite (voir run_or_fail).

from bson.max_key import MaxKey
from bson.min_key import MinKey
from pymongo import MongoClient
from pymongo.errors import PyMongoError


def is_cluster(db):
    return db.client.admin.command("hello").get("msg") == "isdbgrid"


def shard_status(db):
    """{nom du shard: joignable ?}, en contactant directement chaque shard (1 s maximum)."""
    status = {}
    for s in db.client.config.shards.find():
        host = s["host"].split("/", 1)[-1]
        try:
            with MongoClient(f"mongodb://{host}/", directConnection=True, serverSelectionTimeoutMS=1000) as c:
                c.admin.command("ping")
            status[s["_id"]] = True
        except PyMongoError:
            status[s["_id"]] = False
    return status


def _bound(v):
    """Borne de chunk comparable : MinKey / MaxKey -> -inf / +inf."""
    if isinstance(v, MinKey):
        return float("-inf")
    if isinstance(v, MaxKey):
        return float("inf")
    return v


def reader_shards(db, user_id):
    """Shards qui contiennent les notes du lecteur.

    Clé {user_id: hashed} : un seul shard, celui du chunk contenant le hachage de user_id ($toHashedIndexKey,
    calculé par mongos). Clé {book_id: hashed} : les notes du lecteur sont dispersées, tous les shards ayant des
    chunks sont concernés.
    """
    coll = db.client.config.collections.find_one({"_id": f"{db.name}.ratings"})
    chunks = list(db.client.config.chunks.find({"uuid": coll["uuid"]}))
    if "user_id" not in coll["key"]:
        return sorted({ch["shard"] for ch in chunks})
    h = next(db.aggregate([{"$documents": [{"u": user_id}]},
                           {"$project": {"h": {"$toHashedIndexKey": "$u"}}}]))["h"]
    for ch in chunks:
        if _bound(ch["min"]["user_id"]) <= h < _bound(ch["max"]["user_id"]):
            return [ch["shard"]]
    return sorted({ch["shard"] for ch in chunks})


def check(db, service, readers=(), needs_all=False):
    """Vérifie avant le calcul ; renvoie True si le service peut donner un résultat complet.

    readers   : lecteurs dont l'historique est nécessaire ;
    needs_all : le service lit des notes de tous les lecteurs (requêtes centrées livre de F1, utilisées par F3).
    En cas de problème, affiche ce qui manque et pourquoi le service refuse de répondre.
    """
    if not is_cluster(db):
        return True
    status = shard_status(db)
    down = [s for s, ok in status.items() if not ok]
    if not down:
        return True
    print(f"{service} : instance(s) injoignable(s) : {', '.join(down)} "
          f"(joignables : {', '.join(s for s, ok in status.items() if ok) or 'aucune'}).")
    missing = False
    for u in readers:
        where = reader_shards(db, u)
        lost = [s for s in where if s in down]
        missing |= bool(lost)
        state = f"INACCESSIBLES ({', '.join(lost)} arrêté)" if lost else "accessibles"
        print(f"  notes du lecteur {u} : stockées sur {', '.join(where)} -> {state}")
    primary = db.client.config.databases.find_one({"_id": db.name})["primary"]
    if primary in down:
        print(f"  catalogue (books, tags, book_tags) : stocké sur {primary} -> INACCESSIBLE")
        missing = True
    if needs_all:
        print(f"  ce service lit aussi les notes de tous les lecteurs (livres similaires) : une partie est sur "
              f"{', '.join(down)}")
        missing = True
    if missing:
        print(f"ÉCHEC de {service} : aucun résultat n'est affiché, il serait calculé sur une partie seulement "
              f"des données.")
        return False
    print(f"  Toutes les données nécessaires à {service} sont sur des instances joignables : le résultat "
          f"ci-dessous est complet.\n")
    return True


def run_or_fail(service, fn):
    """Exécute fn() ; une erreur MongoDB pendant le calcul (instance perdue en cours de route) devient un échec
    explicite au lieu d'une trace Python ou d'un résultat incomplet. Renvoie (ok, résultat)."""
    try:
        return True, fn()
    except PyMongoError as e:
        print(f"ÉCHEC de {service} : erreur MongoDB pendant le calcul, aucun résultat affiché.")
        print(f"  {type(e).__name__} : {str(e).split(', full error')[0]}")
        return False, None
