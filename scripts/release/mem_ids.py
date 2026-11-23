#!/usr/bin/env python3
"""
mem_ids.py — saved Mem collection ids, so a normal run makes no lookup calls.

Every Mem collection the release commands touch is found by name only once,
then its id is kept in the folder's config (`.medtrics-release-notes/
config.json`, under "mem"). A run uses the saved id directly — no
`get_collection` check, no `search_collections`, no `list_collections` walk.
When something is missing, the command pages through `list_collections`
ONCE (limit 100) and resolves every missing name from that one dump.

Mem is reachable only through the Mem MCP (Claude-side), so this script never
calls Mem: it reads the config and the saved dump, and prints what to do.

  need <keys…>      The saved id for each key, and the keys still missing
                    (with the collection name and description to look for).
  resolve <keys…> --collections <dump.json>
                    Match each missing key's name in one merged
                    `list_collections` dump: exact title ignoring case, then
                    exact case, then the oldest `created_at`; still tied →
                    ambiguous (never written). Saves every id it finds.
                    Keys with no match are listed under `create`.
  save <key> <id>   Save one id (after `create_collection`).
  forget <key>      Drop a saved id that Mem says no longer exists; the next
                    `need` lists it as missing again.
  landed <key> --response <file>
                    Did a `create_note` land in the key's collection? Mem
                    silently ignores a collection id it cannot find, so a stale
                    saved id makes a loose note with no error. Reads the
                    response saved to <file>: `landed` true (fine), false (the
                    id is forgotten; look it up once and `add_note_to_collection`)
                    or null (the response names no collections; nothing to check).
  wait <seconds>    Sleep before retrying a refused Mem call (1–120s).

A key the one-walk lookup did not find is remembered as **absent** for
ABSENT_DAYS (config "mem_absent"), so a run that needs `pod_log` before
`/release-note-setup pod` made it does not walk `list_collections` every time.
`need` lists those under `absent`, not `missing`; `--recheck` (setup) ignores
the marks; `save` clears one.

Keys: release_archive, pod_map, pod_log, dev_directory, pr_overrides,
kb_ledger, kb_queue, kb_catalogue, kb_demo.

`MEM_PR_OVERRIDES_COLLECTION_ID` in .env (or the environment) still wins
for pr_overrides, as before.

Every subcommand prints JSON, then:

    ---MEM_IDS_RESULT---
    {"ok", "action", ..., "error"}

Exit 0 on success, 1 on a bad key or unreadable dump.
"""

import argparse
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "kb"))

import dev_directory  # noqa: E402
import kb_ledger  # noqa: E402
import kb_paths  # noqa: E402
import pod_map  # noqa: E402
import pr_overrides  # noqa: E402
import release_archive  # noqa: E402

KEYS = ("release_archive", "pod_map", "pod_log", "dev_directory", "pr_overrides",
        "kb_ledger", "kb_queue", "kb_catalogue", "kb_demo")
PR_OVERRIDES_ENV_ID = "MEM_PR_OVERRIDES_COLLECTION_ID"
ABSENT_KEY = "mem_absent"
ABSENT_DAYS = 7


def collection_names(env_path=".env", environ=None):
    """{key: {"name", "description"}} for every key, from .env / defaults."""
    out = {}
    name, _ = release_archive.resolve_collection_name(env_path, environ)
    out["release_archive"] = {"name": name,
                              "description": release_archive.COLLECTION_DESCRIPTION}
    name, _ = pod_map.resolve_collection_name(env_path, environ)
    out["pod_map"] = {"name": name, "description": pod_map.COLLECTION_DESCRIPTION}
    name, _ = pod_map.resolve_log_collection_name(env_path, environ)
    out["pod_log"] = {"name": name, "description": pod_map.LOG_COLLECTION_DESCRIPTION}
    out["dev_directory"] = {"name": dev_directory.COLLECTION_TITLE,
                            "description": dev_directory.COLLECTION_DESCRIPTION}
    out["pr_overrides"] = {"name": pr_overrides.COLLECTION_TITLE,
                           "description": pr_overrides.COLLECTION_DESCRIPTION}
    for which, rec in kb_ledger.resolve_names(env_path, environ).items():
        out[f"kb_{which}"] = {"name": rec["name"], "description": rec["description"]}
    return out


def _env_value(env_path, key, environ=None):
    environ = os.environ if environ is None else environ
    return (environ.get(key) or "").strip() or \
        release_archive._read_env_file_key(env_path, key).strip()


def saved_ids(home, env_path=".env", environ=None):
    """{key: id} from the config, with the .env pr_overrides id on top."""
    mem = kb_paths.load(home).get("mem") or {}
    ids = {k: v for k, v in mem.items() if k in KEYS and isinstance(v, str) and v.strip()}
    pinned = _env_value(env_path, PR_OVERRIDES_ENV_ID, environ)
    if pinned:
        ids["pr_overrides"] = pinned
    return ids


def _check_keys(keys):
    bad = [k for k in keys if k not in KEYS]
    if bad:
        raise ValueError(f"unknown key(s): {', '.join(bad)} (known: {', '.join(KEYS)})")
    return list(dict.fromkeys(keys))


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


def absent_keys(home, now=None, days=ABSENT_DAYS):
    """Keys the lookup found missing less than `days` ago."""
    now = now or _now()
    out = set()
    for k, ts in (kb_paths.load(home).get(ABSENT_KEY) or {}).items():
        try:
            t = _dt.datetime.fromisoformat(str(ts))
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=_dt.timezone.utc)
        if now - t < _dt.timedelta(days=days):
            out.add(k)
    return out


def mark_absent(home, keys, now=None):
    if not keys:
        return
    cfg = kb_paths.load(home)
    marks = cfg.get(ABSENT_KEY) or {}
    stamp = (now or _now()).isoformat(timespec="seconds")
    marks.update({k: stamp for k in keys})
    cfg[ABSENT_KEY] = marks
    kb_paths.ensure(home)
    kb_paths.save(home, cfg)


def clear_absent(home, keys):
    cfg = kb_paths.load(home)
    marks = cfg.get(ABSENT_KEY) or {}
    if any(k in marks for k in keys):
        for k in keys:
            marks.pop(k, None)
        cfg[ABSENT_KEY] = marks
        kb_paths.save(home, cfg)


def need(home, keys, env_path=".env", environ=None, recheck=False, now=None):
    keys = _check_keys(keys)
    names = collection_names(env_path, environ)
    ids = saved_ids(home, env_path, environ)
    gone = set() if recheck else absent_keys(home, now)
    found = {k: ids[k] for k in keys if k in ids}
    missing = [{"key": k, **names[k]} for k in keys if k not in ids and k not in gone]
    absent = [k for k in keys if k not in ids and k in gone]
    return {"ids": found, "missing": missing, "absent": absent}


def _collection_ids_in(obj, depth=0):
    """Every collection id a create_note response names, or None when it names no
    collections at all (`collection_ids`, `collections` as ids or {id}, nested)."""
    if depth > 4:
        return None
    if isinstance(obj, list):
        found = None
        for x in obj:
            got = _collection_ids_in(x, depth + 1)
            if got is not None:
                found = (found or set()) | got
        return found
    if not isinstance(obj, dict):
        return None
    found = None
    for k in ("collection_ids", "collections"):
        if k in obj and isinstance(obj[k], list):
            ids = {str(c.get("id") if isinstance(c, dict) else c) for c in obj[k]
                   if (c.get("id") if isinstance(c, dict) else c)}
            found = (found or set()) | ids
    for k in ("note", "data", "result"):
        if k in obj:
            got = _collection_ids_in(obj[k], depth + 1)
            if got is not None:
                found = (found or set()) | got
    return found


def _note_id_in(obj):
    if isinstance(obj, dict):
        for k in ("id", "note_id"):
            if isinstance(obj.get(k), str) and obj[k].strip():
                return obj[k].strip()
        for k in ("note", "data", "result"):
            got = _note_id_in(obj.get(k))
            if got:
                return got
    return None


def landed(home, key, response, env_path=".env", environ=None):
    """{"landed": True|False|None, "note_id", "collection_id"}. False forgets the
    saved id (it no longer maps to a collection Mem can find)."""
    _check_keys([key])
    cid = saved_ids(home, env_path, environ).get(key)
    try:
        resp = json.loads(response) if isinstance(response, str) else response
    except ValueError:
        resp = None
    ids = _collection_ids_in(resp)
    out = {"note_id": _note_id_in(resp), "collection_id": cid}
    if ids is None or not cid:
        return {**out, "landed": None}
    if cid in ids:
        return {**out, "landed": True}
    cfg = kb_paths.load(home)
    if (cfg.get("mem") or {}).pop(key, None) is not None:
        kb_paths.save(home, cfg)
    return {**out, "landed": False}


def _collections(path):
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return []
    raw = json.loads(p.read_text())
    if isinstance(raw, dict):
        raw = raw.get("collections") or raw.get("items") or raw.get("results") or []
    return [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []


def _title(c):
    return (c.get("title") or c.get("name") or "").strip()


def pick(collections, name):
    """(id, candidates): the one collection named `name`, by the archive's
    rule. id is None when there is no match (candidates empty) or the tie
    cannot be broken (candidates lists every id)."""
    hits = [c for c in collections if _title(c).casefold() == name.casefold()
            and (c.get("id") or "").strip()]
    if not hits:
        return None, []
    if len(hits) > 1:
        exact = [c for c in hits if _title(c) == name]
        if exact:
            hits = exact
    if len(hits) > 1:
        oldest = min(str(c.get("created_at") or "~") for c in hits)
        hits = [c for c in hits if str(c.get("created_at") or "~") == oldest]
    if len(hits) == 1:
        return hits[0]["id"], [hits[0]["id"]]
    return None, [c["id"] for c in hits]


def resolve(home, keys, dump, env_path=".env", environ=None):
    state = need(home, keys, env_path, environ)
    cols = _collections(dump)
    newly, create, ambiguous = {}, [], []
    for m in state["missing"]:
        cid, cands = pick(cols, m["name"])
        if cid:
            newly[m["key"]] = cid
        elif cands:
            ambiguous.append({"key": m["key"], "name": m["name"], "candidates": cands})
        else:
            create.append(m)
    if newly:
        kb_paths.ensure(home)
        kb_paths.merge(home, {"mem": newly})
        clear_absent(home, list(newly))
    mark_absent(home, [m["key"] for m in create])
    return {"ids": {**state["ids"], **newly}, "saved": newly,
            "create": create, "ambiguous": ambiguous,
            "collections_read": len(cols)}


def emit(payload, result):
    print(json.dumps(payload, indent=1))
    print()
    print("---MEM_IDS_RESULT---")
    print(json.dumps(result))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Saved Mem collection ids")
    ap.add_argument("--home", help="the run folder (default $KB_HOME, else cwd)")
    ap.add_argument("--env-path", default=".env")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("need")
    p.add_argument("keys", nargs="+")
    p.add_argument("--recheck", action="store_true",
                   help="ignore the absent marks (setup): list every unsaved key as missing")
    p = sub.add_parser("resolve")
    p.add_argument("keys", nargs="+")
    p.add_argument("--collections", required=True,
                   help="Merged list_collections pages, {\"collections\": [...]}")
    p = sub.add_parser("save")
    p.add_argument("key")
    p.add_argument("id")
    p = sub.add_parser("forget")
    p.add_argument("key")
    p = sub.add_parser("landed")
    p.add_argument("key")
    p.add_argument("--response", required=True, help="the create_note response, saved as a file")
    p = sub.add_parser("wait")
    p.add_argument("seconds", type=int)
    a = ap.parse_args(argv)
    if a.cmd == "wait":
        secs = max(1, min(120, a.seconds))
        time.sleep(secs)
        emit({"waited": secs}, {"ok": True, "action": "wait", "seconds": secs,
                                "error": None})
        return 0
    home = kb_paths.folder(a.home)
    try:
        if a.cmd == "need":
            out = need(home, a.keys, a.env_path, recheck=a.recheck)
            emit(out, {"ok": True, "action": "need", "found": len(out["ids"]),
                       "missing": [m["key"] for m in out["missing"]],
                       "absent": out["absent"], "error": None})
        elif a.cmd == "resolve":
            out = resolve(home, a.keys, a.collections, a.env_path)
            emit(out, {"ok": True, "action": "resolve", "saved": sorted(out["saved"]),
                       "create": [m["key"] for m in out["create"]],
                       "ambiguous": [m["key"] for m in out["ambiguous"]], "error": None})
        elif a.cmd == "save":
            _check_keys([a.key])
            if not a.id.strip():
                raise ValueError("empty id")
            kb_paths.ensure(home)
            kb_paths.merge(home, {"mem": {a.key: a.id.strip()}})
            clear_absent(home, [a.key])
            emit({"saved": {a.key: a.id.strip()}},
                 {"ok": True, "action": "save", "key": a.key, "error": None})
        elif a.cmd == "landed":
            out = landed(home, a.key, Path(a.response).read_text(), a.env_path)
            emit(out, {"ok": True, "action": "landed", "key": a.key, "landed": out["landed"],
                       "error": None})
        else:
            _check_keys([a.key])
            cfg = kb_paths.load(home)
            dropped = (cfg.get("mem") or {}).pop(a.key, None)
            if dropped is not None:
                kb_paths.save(home, cfg)
            emit({"forgot": {a.key: dropped}},
                 {"ok": True, "action": "forget", "key": a.key,
                  "was_saved": dropped is not None, "error": None})
        return 0
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        emit({}, {"ok": False, "action": a.cmd, "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.path.insert(0, str(HERE.parent))
    import utf8_mode
    utf8_mode.ensure()          # Windows: rerun in UTF-8 mode
    sys.exit(main())
