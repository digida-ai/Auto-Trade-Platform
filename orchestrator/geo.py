"""Map modelling for real-world manifests (ported/adapted from musashaban geo).

Models places, automaton/agent locations, work items/resources, and routes in SQLite
so real-world manifests are relatable. Coordinates must come from configured, observed,
or explicitly provided real sources; nothing is invented. Routes/costs are estimates
unless explicitly confirmed.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import GEO_DB_PATH, ensure_dirs

_lock = threading.RLock()

PLACE_CATEGORIES = {"faucet", "router", "paywall", "agent", "atm", "bank", "momo_agent", "exchange", "creator", "resource", "work_site", "other"}
PLACE_STATUSES = {"active", "inactive", "maintenance", "offline"}
ROUTE_MODES = {"boda", "car", "foot", "ussd", "gsma", "x402", "interledger", "bank_transfer", "momo", "pix", "other"}
ROUTE_STATUSES = {"planned", "active", "completed", "failed"}
GEO_SOURCES = {"gps", "cell", "wifi", "manual", "ip", "configured", "observed"}
WORK_KINDS = {"delivery", "pickup", "service", "collection", "task", "resource", "offer", "other"}
WORK_STATUSES = {"open", "assigned", "in_progress", "done", "cancelled"}
WORK_PRIORITIES = {"low", "normal", "high", "critical"}
PRESENCE = {"online", "away", "offline", "busy"}

EARTH_RADIUS_METERS = 6_371_008.8


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(str(GEO_DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_geo_schema() -> None:
    with _lock:
        conn = _conn()
        try:
            cur = conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS geo_places (
                  id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  category TEXT NOT NULL,
                  lat REAL NOT NULL,
                  lng REAL NOT NULL,
                  accuracy REAL,
                  location_source TEXT NOT NULL DEFAULT 'manual',
                  address TEXT,
                  phone TEXT,
                  currency TEXT,
                  status TEXT NOT NULL DEFAULT 'active',
                  automaton_id TEXT,
                  metadata TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_geo_places_lat_lng ON geo_places(lat, lng);
                CREATE INDEX IF NOT EXISTS idx_geo_places_category ON geo_places(category);

                CREATE TABLE IF NOT EXISTS geo_automaton_locations (
                  automaton_id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  address TEXT NOT NULL DEFAULT '',
                  chain_type TEXT NOT NULL DEFAULT 'n/a',
                  lat REAL NOT NULL,
                  lng REAL NOT NULL,
                  accuracy REAL,
                  location_source TEXT NOT NULL DEFAULT 'manual',
                  icon TEXT NOT NULL DEFAULT 'boda',
                  status TEXT NOT NULL DEFAULT 'online',
                  metadata TEXT,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS geo_routes (
                  id TEXT PRIMARY KEY,
                  from_lat REAL NOT NULL,
                  from_lng REAL NOT NULL,
                  to_lat REAL NOT NULL,
                  to_lng REAL NOT NULL,
                  mode TEXT NOT NULL,
                  distance_meters INTEGER NOT NULL,
                  duration_seconds INTEGER NOT NULL,
                  cost_minor INTEGER NOT NULL DEFAULT 0,
                  currency TEXT NOT NULL DEFAULT 'USD',
                  estimated INTEGER NOT NULL DEFAULT 1,
                  status TEXT NOT NULL DEFAULT 'planned',
                  place_id TEXT,
                  automaton_id TEXT,
                  work_item_id TEXT,
                  purpose TEXT,
                  metadata TEXT,
                  created_at TEXT NOT NULL,
                  completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_geo_routes_status ON geo_routes(status);

                CREATE TABLE IF NOT EXISTS geo_work_items (
                  id TEXT PRIMARY KEY,
                  kind TEXT NOT NULL,
                  title TEXT NOT NULL,
                  detail TEXT,
                  lat REAL,
                  lng REAL,
                  location_source TEXT,
                  place_id TEXT,
                  automaton_id TEXT,
                  status TEXT NOT NULL DEFAULT 'open',
                  priority TEXT NOT NULL DEFAULT 'normal',
                  value_minor INTEGER,
                  currency TEXT,
                  ref_type TEXT,
                  ref_id TEXT,
                  due_at TEXT,
                  metadata TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_geo_work_status ON geo_work_items(status);
                """
            )
            conn.commit()
        finally:
            conn.close()


class GeoError(Exception):
    pass


def _validate_lat_lng(lat: Any, lng: Any) -> Tuple[float, float]:
    try:
        latf = float(lat)
        lngf = float(lng)
    except Exception:
        raise GeoError("lat/lng must be numeric")
    if not (math.isfinite(latf) and math.isfinite(lngf)):
        raise GeoError("lat/lng must be finite")
    if latf < -90 or latf > 90 or lngf < -180 or lngf > 180:
        raise GeoError("lat must be [-90,90], lng must be [-180,180]")
    # reject obvious non-real Null Island default unless explicitly intentional; still allow if source states so
    return latf, lngf


def _one_of(value: Optional[str], allowed: set, field: str, default: Optional[str] = None) -> str:
    if value is None or value == "":
        if default is None:
            raise GeoError(f"{field} is required")
        return default
    if value not in allowed:
        raise GeoError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    return value


def haversine_meters(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    to_rad = lambda d: d * math.pi / 180.0
    d_lat = to_rad(b_lat - a_lat)
    d_lng = to_rad(b_lng - a_lng)
    lat1 = to_rad(a_lat)
    lat2 = to_rad(b_lat)
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lng / 2) ** 2
    return 2 * EARTH_RADIUS_METERS * math.asin(min(1.0, math.sqrt(h)))


# --- Places ---

def upsert_place(data: Dict[str, Any]) -> Dict[str, Any]:
    ensure_geo_schema()
    name = str(data.get("name") or "").strip()
    if not name:
        raise GeoError("name is required")
    category = _one_of(data.get("category"), PLACE_CATEGORIES, "category")
    status = _one_of(data.get("status", "active"), PLACE_STATUSES, "status", "active")
    lat, lng = _validate_lat_lng(data.get("lat"), data.get("lng"))
    source = _one_of(data.get("source") or data.get("location_source") or "manual", GEO_SOURCES, "source", "manual")
    pid = str(data.get("id") or f"place_{uuid.uuid4().hex[:12]}")
    now = _now()
    meta = data.get("metadata") or {}
    with _lock:
        conn = _conn()
        try:
            cur = conn.cursor()
            row = cur.execute("SELECT created_at FROM geo_places WHERE id=?", (pid,)).fetchone()
            created = row["created_at"] if row else now
            cur.execute(
                """INSERT INTO geo_places (id,name,category,lat,lng,accuracy,location_source,address,phone,currency,status,automaton_id,metadata,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,category=excluded.category,lat=excluded.lat,lng=excluded.lng,
                     accuracy=excluded.accuracy,location_source=excluded.location_source,address=excluded.address,phone=excluded.phone,
                     currency=excluded.currency,status=excluded.status,automaton_id=excluded.automaton_id,metadata=excluded.metadata,updated_at=excluded.updated_at""",
                (
                    pid, name, category, lat, lng,
                    data.get("accuracy"), source,
                    data.get("address"), data.get("phone"), data.get("currency"),
                    status, data.get("automaton_id") or data.get("automatonId"),
                    json.dumps(meta, ensure_ascii=False), created, now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return get_place(pid) or {"id": pid}


def get_place(place_id: str) -> Optional[Dict[str, Any]]:
    ensure_geo_schema()
    with _lock:
        conn = _conn()
        try:
            row = conn.execute("SELECT * FROM geo_places WHERE id=?", (place_id,)).fetchone()
            return _row_place(row) if row else None
        finally:
            conn.close()


def list_places(category: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    ensure_geo_schema()
    q = "SELECT * FROM geo_places"
    args: List[Any] = []
    if category:
        q += " WHERE category=?"
        args.append(category)
    q += " ORDER BY updated_at DESC LIMIT ?"
    args.append(max(1, min(limit, 1000)))
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(q, args).fetchall()
            return [_row_place(r) for r in rows]
        finally:
            conn.close()


def _row_place(r: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": r["id"], "name": r["name"], "category": r["category"],
        "location": {"lat": r["lat"], "lng": r["lng"], "accuracy": r["accuracy"], "source": r["location_source"]},
        "address": r["address"], "phone": r["phone"], "currency": r["currency"],
        "status": r["status"], "automaton_id": r["automaton_id"],
        "metadata": json.loads(r["metadata"] or "{}"),
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


# --- Automaton locations ---

def upsert_automaton_location(data: Dict[str, Any]) -> Dict[str, Any]:
    ensure_geo_schema()
    aid = str(data.get("automaton_id") or data.get("automatonId") or "").strip()
    if not aid:
        raise GeoError("automaton_id is required")
    name = str(data.get("name") or aid).strip()
    lat, lng = _validate_lat_lng(data.get("lat"), data.get("lng"))
    source = _one_of(data.get("source") or data.get("location_source") or "manual", GEO_SOURCES, "source", "manual")
    status = _one_of(data.get("status", "online"), PRESENCE, "status", "online")
    now = _now()
    meta = data.get("metadata") or {}
    with _lock:
        conn = _conn()
        try:
            conn.execute(
                """INSERT INTO geo_automaton_locations (automaton_id,name,address,chain_type,lat,lng,accuracy,location_source,icon,status,metadata,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(automaton_id) DO UPDATE SET name=excluded.name,address=excluded.address,chain_type=excluded.chain_type,
                     lat=excluded.lat,lng=excluded.lng,accuracy=excluded.accuracy,location_source=excluded.location_source,
                     icon=excluded.icon,status=excluded.status,metadata=excluded.metadata,updated_at=excluded.updated_at""",
                (
                    aid, name, str(data.get("address") or ""), str(data.get("chain_type") or data.get("chainType") or "n/a"),
                    lat, lng, data.get("accuracy"), source, str(data.get("icon") or "boda"), status,
                    json.dumps(meta, ensure_ascii=False), now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return get_automaton_location(aid) or {"automaton_id": aid}


def get_automaton_location(automaton_id: str) -> Optional[Dict[str, Any]]:
    ensure_geo_schema()
    with _lock:
        conn = _conn()
        try:
            row = conn.execute("SELECT * FROM geo_automaton_locations WHERE automaton_id=?", (automaton_id,)).fetchone()
            return _row_automaton(row) if row else None
        finally:
            conn.close()


def list_automaton_locations(limit: int = 200) -> List[Dict[str, Any]]:
    ensure_geo_schema()
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute("SELECT * FROM geo_automaton_locations ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 1000)),)).fetchall()
            return [_row_automaton(r) for r in rows]
        finally:
            conn.close()


def _row_automaton(r: sqlite3.Row) -> Dict[str, Any]:
    return {
        "automaton_id": r["automaton_id"], "name": r["name"], "address": r["address"], "chain_type": r["chain_type"],
        "location": {"lat": r["lat"], "lng": r["lng"], "accuracy": r["accuracy"], "source": r["location_source"]},
        "icon": r["icon"], "status": r["status"],
        "metadata": json.loads(r["metadata"] or "{}"), "updated_at": r["updated_at"],
    }


# --- Work items ---

def upsert_work_item(data: Dict[str, Any]) -> Dict[str, Any]:
    ensure_geo_schema()
    title = str(data.get("title") or "").strip()
    if not title:
        raise GeoError("title is required")
    kind = _one_of(data.get("kind", "task"), WORK_KINDS, "kind", "task")
    status = _one_of(data.get("status", "open"), WORK_STATUSES, "status", "open")
    priority = _one_of(data.get("priority", "normal"), WORK_PRIORITIES, "priority", "normal")
    lat = data.get("lat"); lng = data.get("lng")
    loc_source = data.get("source") or data.get("location_source")
    if lat is not None and lng is not None:
        lat, lng = _validate_lat_lng(lat, lng)
        loc_source = _one_of(loc_source or "manual", GEO_SOURCES, "source", "manual")
    else:
        lat, lng, loc_source = None, None, None
    wid = str(data.get("id") or f"work_{uuid.uuid4().hex[:12]}")
    now = _now()
    meta = data.get("metadata") or {}
    with _lock:
        conn = _conn()
        try:
            row = conn.execute("SELECT created_at FROM geo_work_items WHERE id=?", (wid,)).fetchone()
            created = row["created_at"] if row else now
            conn.execute(
                """INSERT INTO geo_work_items (id,kind,title,detail,lat,lng,location_source,place_id,automaton_id,status,priority,value_minor,currency,ref_type,ref_id,due_at,metadata,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,title=excluded.title,detail=excluded.detail,lat=excluded.lat,lng=excluded.lng,
                     location_source=excluded.location_source,place_id=excluded.place_id,automaton_id=excluded.automaton_id,status=excluded.status,
                     priority=excluded.priority,value_minor=excluded.value_minor,currency=excluded.currency,ref_type=excluded.ref_type,ref_id=excluded.ref_id,
                     due_at=excluded.due_at,metadata=excluded.metadata,updated_at=excluded.updated_at""",
                (
                    wid, kind, title, data.get("detail"), lat, lng, loc_source,
                    data.get("place_id") or data.get("placeId"),
                    data.get("automaton_id") or data.get("automatonId"),
                    status, priority, data.get("value_minor") or data.get("valueMinor"),
                    data.get("currency"), data.get("ref_type") or data.get("refType"), data.get("ref_id") or data.get("refId"),
                    data.get("due_at") or data.get("dueAt"),
                    json.dumps(meta, ensure_ascii=False), created, now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return get_work_item(wid) or {"id": wid}


def get_work_item(work_id: str) -> Optional[Dict[str, Any]]:
    ensure_geo_schema()
    with _lock:
        conn = _conn()
        try:
            row = conn.execute("SELECT * FROM geo_work_items WHERE id=?", (work_id,)).fetchone()
            return _row_work(row) if row else None
        finally:
            conn.close()


def list_work_items(status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    ensure_geo_schema()
    q = "SELECT * FROM geo_work_items"
    args: List[Any] = []
    if status:
        q += " WHERE status=?"
        args.append(status)
    q += " ORDER BY updated_at DESC LIMIT ?"
    args.append(max(1, min(limit, 1000)))
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(q, args).fetchall()
            return [_row_work(r) for r in rows]
        finally:
            conn.close()


def _row_work(r: sqlite3.Row) -> Dict[str, Any]:
    loc = None
    if r["lat"] is not None and r["lng"] is not None:
        loc = {"lat": r["lat"], "lng": r["lng"], "source": r["location_source"]}
    return {
        "id": r["id"], "kind": r["kind"], "title": r["title"], "detail": r["detail"],
        "location": loc, "place_id": r["place_id"], "automaton_id": r["automaton_id"],
        "status": r["status"], "priority": r["priority"],
        "value_minor": r["value_minor"], "currency": r["currency"],
        "ref_type": r["ref_type"], "ref_id": r["ref_id"], "due_at": r["due_at"],
        "metadata": json.loads(r["metadata"] or "{}"),
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


# --- Routes ---

def plan_route(data: Dict[str, Any]) -> Dict[str, Any]:
    ensure_geo_schema()
    from_lat, from_lng = _validate_lat_lng(data.get("from_lat", data.get("fromLat")), data.get("from_lng", data.get("fromLng")))
    to_lat, to_lng = _validate_lat_lng(data.get("to_lat", data.get("toLat")), data.get("to_lng", data.get("toLng")))
    mode = _one_of(data.get("mode", "foot"), ROUTE_MODES, "mode", "foot")
    distance = float(haversine_meters(from_lat, from_lng, to_lat, to_lng))
    # rough estimated speeds (m/s) for real-world relatability; estimates only
    speeds = {"foot": 1.4, "boda": 8.0, "car": 12.0, "momo": 0.0, "ussd": 0.0, "gsma": 0.0, "x402": 0.0, "interledger": 0.0, "bank_transfer": 0.0, "pix": 0.0, "other": 1.4}
    speed = speeds.get(mode, 1.4)
    duration = int(distance / speed) if speed > 0 else 0
    rid = str(data.get("id") or f"route_{uuid.uuid4().hex[:12]}")
    now = _now()
    status = _one_of(data.get("status", "planned"), ROUTE_STATUSES, "status", "planned")
    meta = data.get("metadata") or {}
    meta["estimate_basis"] = "haversine_straight_line"
    meta["estimated"] = True
    with _lock:
        conn = _conn()
        try:
            conn.execute(
                """INSERT INTO geo_routes (id,from_lat,from_lng,to_lat,to_lng,mode,distance_meters,duration_seconds,cost_minor,currency,estimated,status,place_id,automaton_id,work_item_id,purpose,metadata,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rid, from_lat, from_lng, to_lat, to_lng, mode, int(distance), int(duration),
                    int(data.get("cost_minor") or data.get("costMinor") or 0),
                    str(data.get("currency") or "USD"),
                    1 if data.get("estimated", True) else 0,
                    status,
                    data.get("place_id") or data.get("placeId"),
                    data.get("automaton_id") or data.get("automatonId"),
                    data.get("work_item_id") or data.get("workItemId"),
                    data.get("purpose"),
                    json.dumps(meta, ensure_ascii=False), now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return get_route(rid) or {"id": rid}


def get_route(route_id: str) -> Optional[Dict[str, Any]]:
    ensure_geo_schema()
    with _lock:
        conn = _conn()
        try:
            row = conn.execute("SELECT * FROM geo_routes WHERE id=?", (route_id,)).fetchone()
            return _row_route(row) if row else None
        finally:
            conn.close()


def list_routes(status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    ensure_geo_schema()
    q = "SELECT * FROM geo_routes"
    args: List[Any] = []
    if status:
        q += " WHERE status=?"
        args.append(status)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(max(1, min(limit, 1000)))
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(q, args).fetchall()
            return [_row_route(r) for r in rows]
        finally:
            conn.close()


def _row_route(r: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": r["id"],
        "from": {"lat": r["from_lat"], "lng": r["from_lng"]},
        "to": {"lat": r["to_lat"], "lng": r["to_lng"]},
        "mode": r["mode"], "distance_meters": r["distance_meters"], "duration_seconds": r["duration_seconds"],
        "cost_minor": r["cost_minor"], "currency": r["currency"],
        "estimated": bool(r["estimated"]), "status": r["status"],
        "place_id": r["place_id"], "automaton_id": r["automaton_id"], "work_item_id": r["work_item_id"], "purpose": r["purpose"],
        "metadata": json.loads(r["metadata"] or "{}"),
        "created_at": r["created_at"], "completed_at": r["completed_at"],
    }


def map_overview() -> Dict[str, Any]:
    ensure_geo_schema()
    return {
        "places": list_places(limit=200),
        "automaton_locations": list_automaton_locations(limit=200),
        "work_items": list_work_items(limit=200),
        "routes": list_routes(limit=200),
        "counts": {
            "places": len(list_places(limit=1000)),
            "automaton_locations": len(list_automaton_locations(limit=1000)),
            "work_items": len(list_work_items(limit=1000)),
            "routes": len(list_routes(limit=1000)),
        },
    }


def record_manifest(kind: str, data: Dict[str, Any]) -> Dict[str, Any]:
    k = (kind or "").strip().lower()
    if k == "place":
        return {"kind": "place", "manifest": upsert_place(data)}
    if k in ("automaton", "automaton_location", "agent", "location"):
        return {"kind": "automaton_location", "manifest": upsert_automaton_location(data)}
    if k in ("work", "work_item", "resource"):
        return {"kind": "work_item", "manifest": upsert_work_item(data)}
    if k == "route":
        return {"kind": "route", "manifest": plan_route(data)}
    raise GeoError(f"unknown manifest kind: {kind}")
