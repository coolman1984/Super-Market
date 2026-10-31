"""The in-app guide's server half: the catalogue, each person's saved progress and
the lessons the shop's own records already prove (af-guide, vendored from the
factory like in the Yousef app; the engine files are not edited here)."""

import json
import pathlib

from . import afguide
from .auth import ROLES

CATALOGUE = json.loads((pathlib.Path(__file__).resolve().parents[1] / "web" / "guide" / "catalogue.json").read_text(encoding="utf-8"))

# state -> SQL that is true when THIS person already did it (the lesson then counts as done from the data)
FACTS = {
    "shift.opened": "SELECT 1 FROM shifts WHERE user_id=:u",
    "sale.first": "SELECT 1 FROM sales WHERE user_id=:u",
    "return.first": "SELECT 1 FROM returns WHERE user_id=:u",
    "shift.closed": "SELECT 1 FROM shifts WHERE user_id=:u AND closed_at IS NOT NULL",
    # the training copy's starting delivery is not the learner's own work
    "receipt.first": "SELECT 1 FROM receipts WHERE user_id=:u AND id > :seed",
    "cutting.first": "SELECT 1 FROM productions WHERE user_id=:u AND kind='cutting'",
    "tagine.first": "SELECT 1 FROM productions WHERE user_id=:u AND kind='recipe'",
    "count.first": "SELECT 1 FROM count_lines WHERE user_id=:u",
    "product.added": "SELECT 1 FROM audit WHERE user_id=:u AND action='product.add' AND id > :seed_audit",
    "user.added": "SELECT 1 FROM audit WHERE user_id=:u AND action='user.added'",
    "backup.made": "SELECT 1 FROM audit WHERE user_id=:u AND action LIKE 'backup%'",
    "password.changed": "SELECT 1 FROM audit WHERE user_id=:u AND action='user.password_changed'",
    "day.closed": "SELECT 1 FROM day_closes WHERE user_id=:u",
    "supplier.paid": "SELECT 1 FROM supplier_entries e JOIN ops o ON o.id=e.op_id WHERE e.kind='payment' AND o.user_id=:u",
}


def live_states(store, user_id):
    training = store.mode == "training"
    args = {"u": user_id, "seed": 1 if training else 0,
            "seed_audit": (store.scalar("SELECT MAX(id) FROM audit WHERE action='product.add' AND user_id=(SELECT MIN(id) FROM users)") or 0)
            if training else 0}
    return [name for name, sql in FACTS.items() if store.db.execute(sql, args).fetchone()]


def _can(actor):
    perms = ROLES.get(actor["role"], set())
    return lambda perm: perm in (None, "*") or perm in perms


def state(store, actor):
    with store.lock:
        record = afguide.load(store.db, actor["id"])
        live = live_states(store, actor["id"])
    return {**afguide.state(CATALOGUE, actor["role"], record, live, _can(actor)), "role": actor["role"]}


def save_progress(store, actor, update):
    if not isinstance(update, dict) or any(k in update and not isinstance(update[k], str) for k in ("op", "guide", "lang")):
        raise ValueError("bad update")

    def work():
        record = afguide.apply(afguide.load(store.db, actor["id"]), update, CATALOGUE)
        afguide.save(store.db, actor["id"], record)
    store.tx(work)
    return state(store, actor)
