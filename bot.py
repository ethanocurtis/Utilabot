import os
import json
import random
import asyncio
import re
from datetime import datetime, timedelta, timezone, date
from typing import Optional, Dict, List, Tuple, Any

import discord
from discord.ext import tasks
from discord import app_commands

# External deps
import aiohttp
import html
import urllib.parse
import sqlite3  # NEW

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

# ---------- Config ----------
DATA_PATH = os.environ.get("DATA_PATH", "/app/data/db.json")
DB_PATH = os.environ.get("DB_PATH", "/app/data/bot.db")
GUILD_IDS: List[int] = []  # e.g., [123456789012345678] for faster guild sync

STARTING_DAILY = 250
PVP_TIMEOUT = 120
WORK_COOLDOWN_MINUTES = 60
WORK_MIN_PAY = 80
WORK_MAX_PAY = 160

STREAK_MAX_BONUS = 500
STREAK_STEP = 50

TRIVIA_REWARD = 120
TRIVIA_API = "https://opentdb.com/api.php"
TRIVIA_TOKEN_API = "https://opentdb.com/api_token.php"

# Secondary trivia API (fallback)
TRIVIA_API_FALLBACK = "https://the-trivia-api.com/v2/questions"

# HTTP headers to avoid 403s on some free APIs
HTTP_HEADERS = {
    "User-Agent": "UtilaBot/1.0 (+https://github.com/ethanocurtis/Utilabot)",
    "Accept": "application/json",
}

# Local offline fallback so trivia always works even if external APIs are down
OFFLINE_TRIVIA = [
    {"q": "What is the capital of France?", "choices": ["Berlin", "Madrid", "Paris", "Rome"], "answer_idx": 2},
    {"q": "Which planet is known as the Red Planet?", "choices": ["Venus", "Mars", "Jupiter", "Mercury"], "answer_idx": 1},
    {"q": "Who wrote '1984'?", "choices": ["George Orwell", "Aldous Huxley", "Ray Bradbury", "Ernest Hemingway"], "answer_idx": 0},
    {"q": "What gas do plants primarily absorb for photosynthesis?", "choices": ["Oxygen", "Carbon Dioxide", "Nitrogen", "Hydrogen"], "answer_idx": 1},
    {"q": "What is the largest ocean on Earth?", "choices": ["Atlantic", "Pacific", "Indian", "Arctic"], "answer_idx": 1},
    {"q": "How many continents are there?", "choices": ["5", "6", "7", "8"], "answer_idx": 2},
    {"q": "Which language has the most native speakers?", "choices": ["English", "Mandarin Chinese", "Spanish", "Hindi"], "answer_idx": 1},
    {"q": "What is H2O commonly known as?", "choices": ["Salt", "Water", "Hydrogen Peroxide", "Oxygen"], "answer_idx": 1},
    {"q": "Which metal is liquid at room temperature?", "choices": ["Mercury", "Iron", "Aluminum", "Copper"], "answer_idx": 0},
    {"q": "What is the smallest prime number?", "choices": ["0", "1", "2", "3"], "answer_idx": 2},
    {"q": "Which country gifted the Statue of Liberty to the USA?", "choices": ["France", "UK", "Spain", "Italy"], "answer_idx": 0},
    {"q": "Which instrument has keys, pedals, and strings?", "choices": ["Guitar", "Piano", "Violin", "Flute"], "answer_idx": 1},
]

DEFAULT_TZ_NAME = "America/Chicago"
REMINDER_TICK_SECONDS = 10

intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


# ---------- Weather styling helpers (icons, colors, formatting) ----------
WX_CODE_MAP = {
    0: ("☀️", "Clear sky"),
    1: ("🌤️", "Mainly clear"),
    2: ("⛅", "Partly cloudy"),
    3: ("☁️", "Overcast"),
    45: ("🌫️", "Fog"),
    48: ("🌫️", "Depositing rime fog"),
    51: ("🌦️", "Light drizzle"),
    53: ("🌦️", "Drizzle"),
    55: ("🌧️", "Heavy drizzle"),
    56: ("🌧️", "Freezing drizzle"),
    57: ("🌧️", "Heavy freezing drizzle"),
    61: ("🌦️", "Light rain"),
    63: ("🌧️", "Rain"),
    65: ("🌧️", "Heavy rain"),
    66: ("🌨️", "Freezing rain"),
    67: ("🌨️", "Heavy freezing rain"),
    71: ("🌨️", "Light snow"),
    73: ("🌨️", "Snow"),
    75: ("❄️", "Heavy snow"),
    77: ("❄️", "Snow grains"),
    80: ("🌧️", "Rain showers"),
    81: ("🌧️", "Heavy rain showers"),
    82: ("⛈️", "Violent rain showers"),
    85: ("🌨️", "Snow showers"),
    86: ("❄️", "Heavy snow showers"),
    95: ("⛈️", "Thunderstorm"),
    96: ("⛈️", "Thunderstorm with hail"),
    99: ("⛈️", "Severe thunderstorm with hail"),
}

def wx_icon_desc(code: int):
    icon, desc = WX_CODE_MAP.get(int(code), ("🌡️", "Weather"))
    return icon, desc

def wx_color_from_temp_f(temp_f: float):
    # Blue -> Teal -> Yellow -> Orange -> Red
    if temp_f is None:
        return discord.Colour.blurple()
    t = float(temp_f)
    if t <= 32:   return discord.Colour.from_rgb(80, 150, 255)
    if t <= 45:   return discord.Colour.from_rgb(100, 180, 255)
    if t <= 60:   return discord.Colour.from_rgb(120, 200, 200)
    if t <= 75:   return discord.Colour.from_rgb(255, 205, 120)
    if t <= 85:   return discord.Colour.from_rgb(255, 160, 80)
    if t <= 95:   return discord.Colour.from_rgb(255, 120, 80)
    return discord.Colour.from_rgb(230, 60, 60)

def fmt_sun(dt_str: str):
    try:
        # Open-Meteo returns local times when 'timezone=auto' is used, potentially without an offset.
        # We display the time as-is in that local timezone (or obey the embedded offset if present).
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%I:%M %p")
    except Exception:
        # Fallback: slice HH:MM
        try:
            return f"{dt_str[11:13]}:{dt_str[14:16]}"  # naive HH:MM
        except Exception:
            return dt_str

# ---------- NEW: Shop & Inventory Config ----------
# name -> dict(price, sell, desc)
SHOP_CATALOG: Dict[str, Dict[str, Any]] = {
    "Fishing Pole": {"price": 750, "sell": 375, "desc": "Required to /fish."},
    "Basic Bait": {"price": 40, "sell": 15, "desc": "Consumed by /fish (1 per cast)."},
    "Premium Bait": {"price": 120, "sell": 60, "desc": "Better odds for rarer fish."},
    "Backpack Upgrade": {"price": 1000, "sell": 500, "desc": "Pure flex. (No cap yet)"},
    # Sellables from fishing
    "Common Fish": {"price": None, "sell": 60, "desc": "A basic catch."},
    "Uncommon Fish": {"price": None, "sell": 160, "desc": "Tasty find."},
    "Rare Fish": {"price": None, "sell": 400, "desc": "Nice market value."},
    "Epic Fish": {"price": None, "sell": 1200, "desc": "A trophy catch."},
    "Old Boot": {"price": None, "sell": 5, "desc": "Kinda soggy…"},
}

FISH_TABLE_BASIC = [
    ("Old Boot", 0.10),
    ("Common Fish", 0.62),
    ("Uncommon Fish", 0.20),
    ("Rare Fish", 0.07),
    ("Epic Fish", 0.01),
]

FISH_TABLE_PREMIUM = [
    ("Old Boot", 0.05),
    ("Common Fish", 0.50),
    ("Uncommon Fish", 0.28),
    ("Rare Fish", 0.14),
    ("Epic Fish", 0.03),
]

def weighted_choice(pairs: List[Tuple[str, float]]) -> str:
    r = random.random(); acc = 0.0
    for name, p in pairs:
        acc += p
        if r <= acc:
            return name
    return pairs[-1][0]

# ---------- SQLite Store (drop-in replacement for JSON) ----------
class Store:
    def __init__(self, json_path: str):
        self.json_path = json_path
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL;")
        self.db.execute("PRAGMA foreign_keys=ON;")
        self._init_db()
        # Migrate data from JSON if present (first-ever run)
        self._maybe_migrate_from_json()
        # Ensure schema matches per-user ID model (id per user, not global)
        self._migrate_schema_if_needed()

    # ---------- schema ----------
    def _init_db(self):
        c = self.db.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS wallets (user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS inventory (user_id INTEGER NOT NULL, item TEXT NOT NULL, qty INTEGER NOT NULL, PRIMARY KEY(user_id,item))""")
        c.execute("""CREATE TABLE IF NOT EXISTS daily (user_id INTEGER PRIMARY KEY, last_iso TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS work (user_id INTEGER PRIMARY KEY, last_iso TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS streaks (user_id INTEGER PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0, last_date TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS stats (user_id INTEGER PRIMARY KEY, wins INTEGER NOT NULL DEFAULT 0, losses INTEGER NOT NULL DEFAULT 0, pushes INTEGER NOT NULL DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS achievements (user_id INTEGER NOT NULL, name TEXT NOT NULL, PRIMARY KEY(user_id, name))""")
        c.execute("""CREATE TABLE IF NOT EXISTS autodelete (channel_id INTEGER PRIMARY KEY, seconds INTEGER NOT NULL)""")
        # Notes: per-user IDs; keep text
        c.execute("""CREATE TABLE IF NOT EXISTS notes (user_id INTEGER NOT NULL, id INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY(user_id, id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS pins (channel_id INTEGER PRIMARY KEY, text TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS polls (message_id INTEGER PRIMARY KEY, json TEXT NOT NULL, is_open INTEGER NOT NULL DEFAULT 1)""")
        # Reminders: per-user IDs
        c.execute("""CREATE TABLE IF NOT EXISTS reminders (user_id INTEGER NOT NULL, id INTEGER NOT NULL, channel_id INTEGER, dm INTEGER NOT NULL, text TEXT NOT NULL, due_utc TEXT NOT NULL, PRIMARY KEY(user_id, id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS admin_allowlist (user_id INTEGER PRIMARY KEY)""")
        c.execute("""CREATE TABLE IF NOT EXISTS weather_zips (user_id INTEGER PRIMARY KEY, zip TEXT NOT NULL)""")
        # Weather subs: per-user IDs
        c.execute("""CREATE TABLE IF NOT EXISTS weather_subs (user_id INTEGER NOT NULL, id INTEGER NOT NULL, zip TEXT NOT NULL, cadence TEXT NOT NULL, hh INTEGER NOT NULL, mi INTEGER NOT NULL, weekly_days INTEGER NOT NULL DEFAULT 7, next_run_utc TEXT, PRIMARY KEY(user_id, id))""")

    # ---------- shape helper for JSON migration ----------
    def _ensure_shape(self, data: dict) -> dict:
        def gd(k): 
            if k not in data or not isinstance(data[k], dict): data[k] = {}
        def gl(k): 
            if k not in data or not isinstance(data[k], list): data[k] = []
        gd("wallets"); gd("daily"); gd("autodelete"); gd("stats"); gd("achievements"); gd("work"); gd("streaks"); gd("trivia")
        gd("weather"); gd("notes"); gd("pins"); gd("polls"); gd("reminders"); gl("admin_allowlist"); gd("inventory")
        if "subs" not in data["weather"] or not isinstance(data["weather"]["subs"], dict): data["weather"]["subs"] = {}
        if "zips" not in data["weather"] or not isinstance(data["weather"]["zips"], dict): data["weather"]["zips"] = {}
        if "seq" not in data["weather"] or not isinstance(data["weather"]["seq"], int): data["weather"]["seq"] = 0
        if "reminder_seq" not in data or not isinstance(data["reminder_seq"], int): data["reminder_seq"] = 0
        return data

    # ---------- JSON -> SQLite (one-time) ----------
    def _maybe_migrate_from_json(self):
        if not os.path.exists(self.json_path):
            return
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        # Heuristic: if wallets already populated, assume migrated
        if self.db.execute("SELECT COUNT(*) FROM wallets").fetchone()[0] > 0:
            return
        data = self._ensure_shape(data)

        # wallets
        for uid, bal in data["wallets"].items():
            self.db.execute("INSERT INTO wallets(user_id, balance) VALUES(?,?)", (int(uid), int(bal)))
        # inventory
        for uid, inv in data.get("inventory", {}).items():
            for item, qty in inv.items():
                self.db.execute("INSERT INTO inventory(user_id,item,qty) VALUES(?,?,?)", (int(uid), item, int(qty)))
        # cooldowns/streaks
        for uid, ts in data["daily"].items():
            self.db.execute("INSERT INTO daily(user_id,last_iso) VALUES(?,?)", (int(uid), ts))
        for uid, ts in data["work"].items():
            self.db.execute("INSERT INTO work(user_id,last_iso) VALUES(?,?)", (int(uid), ts))
        for uid, st in data["streaks"].items():
            self.db.execute("INSERT INTO streaks(user_id,count,last_date) VALUES(?,?,?)",
                            (int(uid), int(st.get("count", 0)), st.get("last_date")))
        # stats & achievements
        for uid, s in data["stats"].items():
            self.db.execute("INSERT INTO stats(user_id,wins,losses,pushes) VALUES(?,?,?,?)",
                            (int(uid), int(s.get("wins",0)), int(s.get("losses",0)), int(s.get("pushes",0))))
        for uid, arr in data["achievements"].items():
            for name in arr:
                self.db.execute("INSERT OR IGNORE INTO achievements(user_id,name) VALUES(?,?)", (int(uid), name))
        # autodelete
        for cid, secs in data["autodelete"].items():
            try: secs = int(secs)
            except Exception: secs = int(float(secs))
            self.db.execute("INSERT INTO autodelete(channel_id,seconds) VALUES(?,?)", (int(cid), secs))
        # notes -> assign per-user sequential IDs starting at 1
        for uid, arr in data["notes"].items():
            note_id = 1
            for text in arr:
                self.db.execute("INSERT INTO notes(user_id,id,text) VALUES(?,?,?)", (int(uid), note_id, text))
                note_id += 1
        # pins
        for cid, txt in data["pins"].items():
            self.db.execute("INSERT INTO pins(channel_id,text) VALUES(?,?)", (int(cid), txt))
        # polls
        for mid, p in data["polls"].items():
            self.db.execute("INSERT INTO polls(message_id,json,is_open) VALUES(?,?,?)",
                            (int(mid), json.dumps(p), 1 if p.get("open", True) else 0))
        # reminders -> per-user IDs, compact
        by_user = {}
        for rid, r in data["reminders"].items():
            uid = int(r.get("user_id",0))
            by_user.setdefault(uid, []).append(r)
        for uid, items in by_user.items():
            # preserve order by due_utc then text
            items.sort(key=lambda x: (str(x.get("due_utc")), str(x.get("text",""))))
            next_id = 1
            for r in items:
                self.db.execute("""INSERT INTO reminders(user_id,id,channel_id,dm,text,due_utc)
                                   VALUES(?,?,?,?,?,?)""",
                                (uid,
                                 next_id,
                                 (None if not r.get("channel_id") else int(r.get("channel_id"))),
                                 1 if r.get("dm") else 0,
                                 str(r.get("text","")),
                                 str(r.get("due_utc"))))
                next_id += 1
        # admin allowlist
        for uid in data["admin_allowlist"]:
            self.db.execute("INSERT OR IGNORE INTO admin_allowlist(user_id) VALUES(?)", (int(uid),))
        # weather defaults + subs
        for uid, z in data["weather"]["zips"].items():
            self.db.execute("INSERT INTO weather_zips(user_id,zip) VALUES(?,?)", (int(uid), str(z)))
        subs_by_user = {}
        for sid, s in data["weather"]["subs"].items():
            uid = int(s.get("user_id"))
            subs_by_user.setdefault(uid, []).append(s)
        for uid, items in subs_by_user.items():
            items.sort(key=lambda x: (str(x.get("zip","")), int(x.get("hh",8)), int(x.get("mi",0))))
            next_id = 1
            for s in items:
                self.db.execute("""INSERT INTO weather_subs(user_id,id,zip,cadence,hh,mi,weekly_days,next_run_utc)
                                   VALUES(?,?,?,?,?,?,?,?)""",
                                (uid, next_id, str(s.get("zip","")), str(s.get("cadence","daily")),
                                 int(s.get("hh",8)), int(s.get("mi",0)), int(s.get("weekly_days",7)), s.get("next_run_utc")))
                next_id += 1
        # trivia token
        tok = data.get("trivia", {}).get("token")
        if tok:
            self.db.execute("INSERT OR REPLACE INTO kv(key,value) VALUES('trivia_token',?)", (tok,))
        try:
            os.replace(self.json_path, self.json_path + ".migrated.bak")
        except Exception:
            pass

    # ---------- schema migrations (SQLite -> SQLite) ----------
    def _migrate_schema_if_needed(self):
        # notes: ensure (user_id,id,text) primary key (user_id,id)
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(notes)").fetchall()]
        if cols == ["user_id","text"]:  # old schema from first SQLite version
            self.db.execute("""CREATE TABLE IF NOT EXISTS notes_new (user_id INTEGER NOT NULL, id INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY(user_id,id))""")
            # assign per-user sequential ids by rowid order
            rows = self.db.execute("SELECT rowid, user_id, text FROM notes ORDER BY user_id, rowid").fetchall()
            current_uid = None
            next_id = 1
            for _, uid, text in rows:
                uid = int(uid)
                if uid != current_uid:
                    current_uid = uid
                    next_id = 1
                self.db.execute("INSERT INTO notes_new(user_id,id,text) VALUES(?,?,?)", (uid, next_id, text))
                next_id += 1
            self.db.execute("DROP TABLE notes")
            self.db.execute("ALTER TABLE notes_new RENAME TO notes")

        # reminders: move to composite PK (user_id,id) if needed
        cols_r = [r[1] for r in self.db.execute("PRAGMA table_info(reminders)").fetchall()]
        if cols_r == ["id","user_id","channel_id","dm","text","due_utc"]:
            self.db.execute("""CREATE TABLE IF NOT EXISTS reminders_new (user_id INTEGER NOT NULL, id INTEGER NOT NULL, channel_id INTEGER, dm INTEGER NOT NULL, text TEXT NOT NULL, due_utc TEXT NOT NULL, PRIMARY KEY(user_id,id))""")
            rows = self.db.execute("SELECT id,user_id,channel_id,dm,text,due_utc FROM reminders ORDER BY user_id, id").fetchall()
            for rid, uid, cid, dm, text, due in rows:
                self.db.execute("""INSERT INTO reminders_new(user_id,id,channel_id,dm,text,due_utc) VALUES(?,?,?,?,?,?)""",
                                (int(uid), int(rid), cid, dm, text, due))
            self.db.execute("DROP TABLE reminders")
            self.db.execute("ALTER TABLE reminders_new RENAME TO reminders")

        # weather_subs: move to composite PK (user_id,id) if needed
        cols_w = [r[1] for r in self.db.execute("PRAGMA table_info(weather_subs)").fetchall()]
        if cols_w == ["id","user_id","zip","cadence","hh","mi","weekly_days","next_run_utc"]:
            self.db.execute("""CREATE TABLE IF NOT EXISTS weather_subs_new (user_id INTEGER NOT NULL, id INTEGER NOT NULL, zip TEXT NOT NULL, cadence TEXT NOT NULL, hh INTEGER NOT NULL, mi INTEGER NOT NULL, weekly_days INTEGER NOT NULL DEFAULT 7, next_run_utc TEXT, PRIMARY KEY(user_id,id))""")
            rows = self.db.execute("SELECT id,user_id,zip,cadence,hh,mi,weekly_days,next_run_utc FROM weather_subs ORDER BY user_id, id").fetchall()
            for sid, uid, z, cad, hh, mi, wdays, next_run in rows:
                self.db.execute("""INSERT INTO weather_subs_new(user_id,id,zip,cadence,hh,mi,weekly_days,next_run_utc) VALUES(?,?,?,?,?,?,?,?)""",
                                (int(uid), int(sid), z, cad, hh, mi, wdays, next_run))
            self.db.execute("DROP TABLE weather_subs")
            self.db.execute("ALTER TABLE weather_subs_new RENAME TO weather_subs")

    # ---------- ID allocation helpers ----------

# ---------- per-user note helpers (used by Business system) ----------
def set_note(self, user_id: int, key: str, text: str) -> None:
    """Store a small text note for a user under a string key.
    Implemented on top of the existing KV table so we don't need a new schema.
    Namespaced as note:{user_id}:{key}. Empty/None text clears the value to empty string."""
    ns_key = f"note:{int(user_id)}:{key}"
    val = "" if text is None else str(text)
    self.db.execute(
        "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (ns_key, val),
    )

def get_note(self, user_id: int, key: str) -> str:
    """Fetch a per-user note stored via set_note. Returns '' if not set."""
    ns_key = f"note:{int(user_id)}:{key}"
    row = self.db.execute("SELECT value FROM kv WHERE key=?", (ns_key,)).fetchone()
    return row[0] if row and row[0] is not None else ""

    def _lowest_free_id_for_user(self, table: str, user_id: int) -> int:
        cur = self.db.execute(f"SELECT id FROM {table} WHERE user_id=? ORDER BY id", (int(user_id),))
        used = [row[0] for row in cur.fetchall()]
        i = 1
        for u in used:
            if u == i:
                i += 1
            elif u > i:
                break
        return i

    # ---------- Debug / Health ----------
    def get_backend_stats(self) -> dict:
        tables = ["wallets","inventory","daily","work","streaks","stats","achievements","autodelete","notes","pins","polls","reminders","admin_allowlist","weather_zips","weather_subs"]
        counts = {}
        for t in tables:
            try:
                counts[t] = int(self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
            except Exception:
                counts[t] = 0
        return {"backend":"sqlite","db_path":DB_PATH, "counts":counts}

    # ---------- wallets ----------
    def get_balance(self, user_id: int) -> int:
        row = self.db.execute("SELECT balance FROM wallets WHERE user_id=?", (int(user_id),)).fetchone()
        return int(row[0]) if row else 0

    def add_balance(self, user_id: int, amount: int):
        self.db.execute("""INSERT INTO wallets(user_id,balance) VALUES(?,?)
                           ON CONFLICT(user_id) DO UPDATE SET balance=wallets.balance+excluded.balance""",
                        (int(user_id), int(amount)))

    def set_balance(self, user_id: int, amount: int):
        self.db.execute("""INSERT INTO wallets(user_id,balance) VALUES(?,?)
                           ON CONFLICT(user_id) DO UPDATE SET balance=excluded.balance""", (int(user_id), int(amount)))

    # ---------- inventory ----------
    def get_inventory(self, user_id: int) -> dict:
        rows = self.db.execute("SELECT item, qty FROM inventory WHERE user_id=?", (int(user_id),)).fetchall()
        return {item: int(qty) for item, qty in rows}

    def add_item(self, user_id: int, item_name: str, qty: int = 1):
        if int(qty) == 0: return
        self.db.execute("""INSERT INTO inventory(user_id,item,qty) VALUES(?,?,?)
                           ON CONFLICT(user_id,item) DO UPDATE SET qty=inventory.qty+excluded.qty""",
                        (int(user_id), item_name, int(qty)))
        self.db.execute("DELETE FROM inventory WHERE user_id=? AND item=? AND qty<=0", (int(user_id), item_name))

    def remove_item(self, user_id: int, item_name: str, qty: int = 1) -> bool:
        inv = self.get_inventory(user_id)
        have = int(inv.get(item_name, 0))
        if have < int(qty): return False
        self.add_item(user_id, item_name, -int(qty))
        return True

    def has_item(self, user_id: int, item_name: str, qty: int = 1) -> bool:
        return self.get_inventory(user_id).get(item_name, 0) >= int(qty)

    # ---------- daily/work/streaks ----------
    def get_last_daily(self, user_id: int):
        row = self.db.execute("SELECT last_iso FROM daily WHERE user_id=?", (int(user_id),)).fetchone()
        return row[0] if row else None

    def set_last_daily(self, user_id: int, iso_ts: str):
        self.db.execute("""INSERT INTO daily(user_id,last_iso) VALUES(?,?)
                           ON CONFLICT(user_id) DO UPDATE SET last_iso=excluded.last_iso""", (int(user_id), iso_ts))

    def get_last_work(self, user_id: int):
        row = self.db.execute("SELECT last_iso FROM work WHERE user_id=?", (int(user_id),)).fetchone()
        return row[0] if row else None

    def set_last_work(self, user_id: int, iso_ts: str):
        self.db.execute("""INSERT INTO work(user_id,last_iso) VALUES(?,?)
                           ON CONFLICT(user_id) DO UPDATE SET last_iso=excluded.last_iso""", (int(user_id), iso_ts))

    def get_streak(self, user_id: int) -> dict:
        row = self.db.execute("SELECT count,last_date FROM streaks WHERE user_id=?", (int(user_id),)).fetchone()
        return {"count": int(row[0]), "last_date": row[1]} if row else {"count": 0, "last_date": None}

    def set_streak(self, user_id: int, count: int, last_date: str | None):
        self.db.execute("""INSERT INTO streaks(user_id,count,last_date) VALUES(?,?,?)
                           ON CONFLICT(user_id) DO UPDATE SET count=excluded.count,last_date=excluded.last_date""",
                        (int(user_id), int(count), last_date))

    # ---------- stats & achievements ----------
    def add_result(self, user_id: int, result: str):
        col = "wins" if result=="win" else ("losses" if result=="loss" else "pushes")
        self.db.execute("""INSERT INTO stats(user_id,wins,losses,pushes) VALUES(?,0,0,0)
                           ON CONFLICT(user_id) DO NOTHING""", (int(user_id),))
        self.db.execute(f"UPDATE stats SET {col}={col}+1 WHERE user_id=?", (int(user_id),))

    def get_stats(self, user_id: int) -> dict:
        row = self.db.execute("SELECT wins,losses,pushes FROM stats WHERE user_id=?", (int(user_id),)).fetchone()
        if not row: return {"wins":0,"losses":0,"pushes":0}
        return {"wins": int(row[0]), "losses": int(row[1]), "pushes": int(row[2])}

    def list_top(self, key: str, limit: int = 10):
        if key == "balance":
            rows = self.db.execute("SELECT user_id, balance FROM wallets ORDER BY balance DESC LIMIT ?", (int(limit),)).fetchall()
            return [(int(uid), int(bal)) for uid, bal in rows]
        if key == "wins":
            rows = self.db.execute("SELECT user_id, wins FROM stats ORDER BY wins DESC LIMIT ?", (int(limit),)).fetchall()
            return [(int(uid), int(w)) for uid, w in rows]
        return []

    def get_achievements(self, user_id: int) -> list[str]:
        rows = self.db.execute("SELECT name FROM achievements WHERE user_id=?", (int(user_id),)).fetchall()
        return [r[0] for r in rows]

    def award_achievement(self, user_id: int, name: str) -> bool:
        try:
            self.db.execute("INSERT INTO achievements(user_id,name) VALUES(?,?)", (int(user_id), name))
            return True
        except sqlite3.IntegrityError:
            return False

    # ---------- trivia token ----------
    def get_trivia_token(self):
        row = self.db.execute("SELECT value FROM kv WHERE key='trivia_token'").fetchone()
        return row[0] if row else None

    def set_trivia_token(self, token):
        if token is None:
            self.db.execute("DELETE FROM kv WHERE key='trivia_token'")
        else:
            self.db.execute("INSERT OR REPLACE INTO kv(key,value) VALUES('trivia_token',?)", (token,))

    # ---------- weather defaults & subscriptions ----------
    def set_user_zip(self, user_id: int, zip_code: str):
        self.db.execute("""INSERT INTO weather_zips(user_id,zip) VALUES(?,?)
                           ON CONFLICT(user_id) DO UPDATE SET zip=excluded.zip""", (int(user_id), str(zip_code)))

    def get_user_zip(self, user_id: int):
        row = self.db.execute("SELECT zip FROM weather_zips WHERE user_id=?", (int(user_id),)).fetchone()
        return row[0] if row else None

    def add_weather_sub(self, sub: dict) -> int:
        uid = int(sub["user_id"])
        sid = self._lowest_free_id_for_user("weather_subs", uid)
        self.db.execute("""INSERT INTO weather_subs(user_id,id,zip,cadence,hh,mi,weekly_days,next_run_utc)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (uid, sid, str(sub["zip"]), str(sub["cadence"]),
                         int(sub["hh"]), int(sub["mi"]), int(sub.get("weekly_days",7)), sub.get("next_run_utc")))
        return sid

    def list_weather_subs(self, user_id: int | None = None) -> list:
        if user_id is None:
            q = "SELECT user_id,id,zip,cadence,hh,mi,weekly_days,next_run_utc FROM weather_subs ORDER BY user_id, id"
            rows = self.db.execute(q).fetchall()
        else:
            q = "SELECT user_id,id,zip,cadence,hh,mi,weekly_days,next_run_utc FROM weather_subs WHERE user_id=? ORDER BY id"
            rows = self.db.execute(q, (int(user_id),)).fetchall()
        out = []
        for uid, sid, z, cad, hh, mi, wdays, next_run in rows:
            out.append({"user_id": int(uid), "id": int(sid), "zip": z, "cadence": cad,
                        "hh": int(hh), "mi": int(mi), "weekly_days": int(wdays), "next_run_utc": next_run})
        return out

    def remove_weather_sub(self, sid: int, requester_id: int) -> bool:
        cur = self.db.execute("DELETE FROM weather_subs WHERE user_id=? AND id=?", (int(requester_id), int(sid)))
        return cur.rowcount > 0

    def update_weather_sub(self, sid: int, **updates):
        uid = int(updates.pop("user_id", 0)) or None
        if uid is None:
            return False
        allowed = {"zip","cadence","hh","mi","weekly_days","next_run_utc"}
        cols, vals = [], []
        for k, v in updates.items():
            if k in allowed:
                cols.append(f"{k}=?"); vals.append(v)
        if not cols: return False
        vals.extend([uid, int(sid)])
        self.db.execute(f"UPDATE weather_subs SET {', '.join(cols)} WHERE user_id=? AND id=?", vals)
        return True

    # ---------- notes ----------
    def add_note(self, user_id: int, text: str) -> int:
        nid = self._lowest_free_id_for_user("notes", int(user_id))
        self.db.execute("INSERT INTO notes(user_id,id,text) VALUES(?,?,?)", (int(user_id), nid, text))
        return nid

    def list_notes(self, user_id: int) -> list[tuple[int,str]]:
        rows = self.db.execute("SELECT id, text FROM notes WHERE user_id=? ORDER BY id", (int(user_id),)).fetchall()
        return [(int(i), t) for i, t in rows]

    def delete_note(self, user_id: int, note_id: int) -> bool:
        cur = self.db.execute("DELETE FROM notes WHERE user_id=? AND id=?", (int(user_id), int(note_id)))
        return cur.rowcount > 0

    # ---------- pins ----------
    def set_pin(self, channel_id: int, text: str):
        self.db.execute("""INSERT INTO pins(channel_id,text) VALUES(?,?)
                           ON CONFLICT(channel_id) DO UPDATE SET text=excluded.text""", (int(channel_id), text))

    def get_pin(self, channel_id: int):
        row = self.db.execute("SELECT text FROM pins WHERE channel_id=?", (int(channel_id),)).fetchone()
        return row[0] if row else None

    def clear_pin(self, channel_id: int):
        self.db.execute("DELETE FROM pins WHERE channel_id=?", (int(channel_id),))

    # ---------- polls ----------
    def save_poll(self, message_id: int, poll: dict):
        is_open = 1 if poll.get("open", True) else 0
        if not is_open:
            try:
                self.delete_poll(int(message_id))
            except Exception:
                pass
            return
        self.db.execute("""INSERT INTO polls(message_id,json,is_open) VALUES(?,?,?)
                           ON CONFLICT(message_id) DO UPDATE SET json=excluded.json,is_open=excluded.is_open""",
                        (int(message_id), json.dumps(poll), is_open))

    def get_poll(self, message_id: int):
        row = self.db.execute("SELECT json FROM polls WHERE message_id=?", (int(message_id),)).fetchone()
        return json.loads(row[0]) if row else None

    def delete_poll(self, message_id: int):
        self.db.execute("DELETE FROM polls WHERE message_id=?", (int(message_id),))

    def list_open_polls(self) -> list[tuple[int, dict]]:
        rows = self.db.execute("SELECT message_id, json FROM polls WHERE is_open=1").fetchall()
        return [(int(mid), json.loads(js)) for mid, js in rows]

    # ---------- reminders ----------
    def add_reminder(self, rem: dict) -> int:
        uid = int(rem["user_id"])
        rid = self._lowest_free_id_for_user("reminders", uid)
        self.db.execute("""INSERT INTO reminders(user_id,id,channel_id,dm,text,due_utc)
                           VALUES(?,?,?,?,?,?)""",
                        (uid, rid,
                         (None if rem.get("channel_id") in (None,"","None") else int(rem["channel_id"])),
                         1 if rem.get("dm") else 0, str(rem.get("text","")), str(rem["due_utc"])))
        return rid

    def list_reminders(self, user_id: int | None = None) -> list[dict]:
        if user_id is None:
            rows = self.db.execute("SELECT user_id,id,channel_id,dm,text,due_utc FROM reminders ORDER BY user_id,id").fetchall()
        else:
            rows = self.db.execute("SELECT user_id,id,channel_id,dm,text,due_utc FROM reminders WHERE user_id=? ORDER BY id",
                                   (int(user_id),)).fetchall()
        out = []
        for uid, rid, cid, dm, text, due in rows:
            out.append({"user_id": int(uid), "id": int(rid), "channel_id": (None if cid is None else int(cid)),
                        "dm": bool(dm), "text": text, "due_utc": due})
        return out

    def cancel_reminder(self, rid: int, requester_id: int, is_mod: bool) -> bool:
        # Since IDs are per-user, target by both user_id and id
        if is_mod:
            cur = self.db.execute("DELETE FROM reminders WHERE user_id=? AND id=?", (int(requester_id), int(rid)))
            return cur.rowcount > 0
        else:
            cur = self.db.execute("DELETE FROM reminders WHERE user_id=? AND id=?", (int(requester_id), int(rid)))
            return cur.rowcount > 0

    # ---------- admin allowlist ----------
    def is_allowlisted(self, user_id: int) -> bool:
        return self.db.execute("SELECT 1 FROM admin_allowlist WHERE user_id=?", (int(user_id),)).fetchone() is not None

    def add_allowlisted(self, user_id: int) -> bool:
        try:
            self.db.execute("INSERT INTO admin_allowlist(user_id) VALUES(?)", (int(user_id),))
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_allowlisted(self, user_id: int) -> bool:
        cur = self.db.execute("DELETE FROM admin_allowlist WHERE user_id=?", (int(user_id),))
        return cur.rowcount > 0

    def list_allowlisted(self) -> list[int]:
        rows = self.db.execute("SELECT user_id FROM admin_allowlist ORDER BY user_id").fetchall()
        return [int(r[0]) for r in rows]

    # ---------- autodelete ----------
    def get_autodelete(self) -> dict:
        rows = self.db.execute("SELECT channel_id, seconds FROM autodelete").fetchall()
        return {str(int(cid)): int(secs) for cid, secs in rows}

    def set_autodelete(self, channel_id: int, seconds: int):
        self.db.execute("""INSERT INTO autodelete(channel_id,seconds) VALUES(?,?)
                           ON CONFLICT(channel_id) DO UPDATE SET seconds=excluded.seconds""",
                        (int(channel_id), int(seconds)))

    def remove_autodelete(self, channel_id: int):
        self.db.execute("DELETE FROM autodelete WHERE channel_id=?", (int(channel_id),))

store = Store(DATA_PATH)

# ---------- Permissions helper ----------
def require_manage_messages():
    def predicate(inter: discord.Interaction):
        perms = inter.channel.permissions_for(inter.user) if isinstance(inter.channel, (discord.TextChannel, discord.Thread)) else None
        if not perms or not perms.manage_messages:
            raise app_commands.CheckFailure("You need the **Manage Messages** permission here.")
        return True
    return app_commands.check(predicate)

def _has_guild_admin_perms(inter: discord.Interaction) -> bool:
    """True if the user has Administrator or Manage Server in this channel/guild."""
    try:
        if isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
            perms = inter.channel.permissions_for(inter.user)
            return bool(perms.administrator or perms.manage_guild)
    except Exception:
        pass
    return False

def require_admin_or_allowlisted():
    """
    Allow real server admins OR users on the admin allowlist.
    Use this on sensitive commands like purge and autodelete_set/disable.
    """
    def predicate(inter: discord.Interaction):
        if _has_guild_admin_perms(inter) or store.is_allowlisted(inter.user.id):
            return True
        raise app_commands.CheckFailure("You need **Administrator/Manage Server** or be on the bot's admin allowlist.")
    return app_commands.check(predicate)

def require_real_admin():
    """Only actual server admins can manage the allowlist."""
    def predicate(inter: discord.Interaction):
        if _has_guild_admin_perms(inter):
            return True
        raise app_commands.CheckFailure("Only users with **Administrator** or **Manage Server** can manage the allowlist.")
    return app_commands.check(predicate)


# ---------- Cards ----------
SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
VALUES = {**{str(i): i for i in range(2, 11)}, "J": 10, "Q": 10, "K": 10, "A": 11}

def deal_deck():
    deck = [(r, s) for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck

def hand_value(cards: List[Tuple[str, str]]) -> int:
    total = sum(VALUES[r] for r, _ in cards)
    aces = sum(1 for r, _ in cards if r == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total

def fmt_hand(cards: List[Tuple[str, str]]) -> str:
    return " ".join(f"{r}{s}" for r, s in cards) + f" (={hand_value(cards)})"


# ---------- Economy & Utility ----------
@tree.command(name="balance", description="Check your balance.")
async def balance(inter: discord.Interaction, user: Optional[discord.User] = None):
    target = user or inter.user
    bal = store.get_balance(target.id)
    await inter.response.send_message(f"💰 **{target.display_name}** has **{bal}** credits.")

def _update_streak(user_id: int) -> int:
    st = store.get_streak(user_id)
    today = date.today().isoformat()
    if st["last_date"] == today:
        return st["count"]
    if st["last_date"]:
        last = date.fromisoformat(st["last_date"])
        if date.fromisoformat(today) - last == timedelta(days=1):
            count = int(st["count"]) + 1
        else:
            count = 1
    else:
        count = 1
    store.set_streak(user_id, count=count, last_date=today)
    return count

@tree.command(name="daily", description="Claim your daily free credits (with streak bonus).")
async def daily(inter: discord.Interaction):
    now = datetime.now(timezone.utc)
    last_iso = store.get_last_daily(inter.user.id)
    if last_iso:
        last = datetime.fromisoformat(last_iso)
        if now - last < timedelta(hours=20):
            remaining = timedelta(hours=20) - (now - last)
            hrs = int(remaining.total_seconds() // 3600)
            mins = int((remaining.total_seconds() % 3600) // 60)
            return await inter.response.send_message(f"⏳ You already claimed. Try again in **{hrs}h {mins}m**.", ephemeral=True)
    amount = STARTING_DAILY
    streak = _update_streak(inter.user.id)
    bonus = min(STREAK_STEP * max(0, streak - 1), STREAK_MAX_BONUS)
    amount += bonus
    store.add_balance(inter.user.id, amount)
    store.set_last_daily(inter.user.id, now.isoformat())
    emb = discord.Embed(title="✅ Daily Claimed")
    emb.add_field(name="Base", value=str(STARTING_DAILY), inline=True)
    emb.add_field(name="Streak Bonus", value=f"+{bonus} (Streak: {streak}🔥)", inline=True)
    emb.add_field(name="Total", value=f"**{amount}** credits", inline=False)
    await inter.response.send_message(embed=emb)

@tree.command(name="work", description="Work a quick virtual job for credits (1h cooldown).")
async def work(inter: discord.Interaction):
    now = datetime.now(timezone.utc)
    last_iso = store.get_last_work(inter.user.id)
    if last_iso:
        last = datetime.fromisoformat(last_iso)
        cd = timedelta(minutes=WORK_COOLDOWN_MINUTES)
        if now - last < cd:
            remaining = cd - (now - last)
            m = int(remaining.total_seconds() // 60)
            s = int(remaining.total_seconds() % 60)
            return await inter.response.send_message(f"⏳ You’re tired. Try again in **{m}m {s}s**.", ephemeral=True)
    amount = random.randint(WORK_MIN_PAY, WORK_MAX_PAY)
    store.add_balance(inter.user.id, amount)
    store.set_last_work(inter.user.id, now.isoformat())
    job = random.choice(["bug squash", "barge fueling", "code review", "data entry", "ticket triage", "river nav calc", "crate stacking"])
    await inter.response.send_message(f"💼 You did a **{job}** shift and earned **{amount}** credits!")

@tree.command(name="pay", description="Transfer credits to another user.")
@app_commands.describe(user="Recipient", amount="Credits to send")
async def pay(inter: discord.Interaction, user: discord.User, amount: app_commands.Range[int, 1, 10_000_000]):
    if user.id == inter.user.id or user.bot:
        return await inter.response.send_message("Pick a real recipient.", ephemeral=True)
    bal = store.get_balance(inter.user.id)
    if bal < amount:
        return await inter.response.send_message("❌ You don't have that many credits.", ephemeral=True)
    store.add_balance(inter.user.id, -amount)
    store.add_balance(user.id, amount)
    await inter.response.send_message(f"✅ Sent **{amount}** credits to **{user.display_name}**.")

@tree.command(name="cooldowns", description="See your time left for daily and work.")
async def cooldowns(inter: discord.Interaction):
    now = datetime.now(timezone.utc)
    daily_left = "Ready ✅"
    last_iso = store.get_last_daily(inter.user.id)
    if last_iso:
        last = datetime.fromisoformat(last_iso)
        cd = timedelta(hours=20) - (now - last)
        if cd.total_seconds() > 0:
            h = int(cd.total_seconds() // 3600)
            m = int((cd.total_seconds() % 3600) // 60)
            daily_left = f"{h}h {m}m"
    work_left = "Ready ✅"
    wlast_iso = store.get_last_work(inter.user.id)
    if wlast_iso:
        wlast = datetime.fromisoformat(wlast_iso)
        wcd = timedelta(minutes=WORK_COOLDOWN_MINUTES) - (now - wlast)
        if wcd.total_seconds() > 0:
            mm = int(wcd.total_seconds() // 60)
            ss = int(wcd.total_seconds() % 60)
            work_left = f"{mm}m {ss}s"
    emb = discord.Embed(title="⏱️ Cooldowns")
    emb.add_field(name="Daily", value=daily_left, inline=True)
    emb.add_field(name="Work", value=work_left, inline=True)
    await inter.response.send_message(embed=emb, ephemeral=True)

@tree.command(name="stats", description="Show your game stats and streak.")
async def stats_cmd(inter: discord.Interaction, user: Optional[discord.User] = None):
    target = user or inter.user
    s = store.get_stats(target.id)
    bal = store.get_balance(target.id)
    st = store.get_streak(target.id)
    emb = discord.Embed(title=f"📊 Stats — {target.display_name}")
    emb.add_field(name="Balance", value=f"{bal} credits", inline=True)
    emb.add_field(name="Record", value=f"{s.get('wins',0)}W / {s.get('losses',0)}L / {s.get('pushes',0)}P", inline=True)
    emb.add_field(name="Daily Streak", value=f"{st.get('count',0)} days", inline=True)
    await inter.response.send_message(embed=emb)

# ---------- NEW: Weather by ZIP ----------

@tree.command(name="weather", description="Current weather by ZIP. Uses your saved ZIP if omitted.")
@app_commands.describe(zip="Optional ZIP; uses your saved default if omitted")
async def weather_cmd(inter: discord.Interaction, zip: Optional[str] = None):
    await inter.response.defer()
    # Resolve ZIP: prefer provided, else saved default
    if not zip or not str(zip).strip():
        saved = store.get_user_zip(inter.user.id)
        if not saved or len(str(saved)) != 5:
            return await inter.followup.send(
                "You didn’t provide a ZIP and no default is saved. Set one with `/weather_set_zip 60601` or pass a ZIP.",
                ephemeral=True
            )
        z = str(saved)
    else:
        z = re.sub(r"[^0-9]", "", str(zip))
        if len(z) != 5:
            return await inter.followup.send("Please give a valid 5‑digit US ZIP.", ephemeral=True)
    try:
        async with aiohttp.ClientSession(headers=HTTP_HEADERS) as session:
            # 1) ZIP -> lat/lon
            async with session.get(f"https://api.zippopotam.us/us/{z}", timeout=aiohttp.ClientTimeout(total=12)) as r:
                if r.status != 200:
                    return await inter.followup.send("Couldn't look up that ZIP.", ephemeral=True)
                zp = await r.json()
            place = zp["places"][0]
            lat = float(place["latitude"]); lon = float(place["longitude"])
            city = place["place name"]; state = place["state abbreviation"]

            # 2) Weather: current + today's daily (for sunrise/sunset/uv and description)
            params = {
                "latitude": lat, "longitude": lon,
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "precipitation_unit": "inch",
                "timezone": "auto",
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,wind_gusts_10m,precipitation,weather_code",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,uv_index_max,sunrise,sunset,wind_speed_10m_max",
            }
            async with session.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=aiohttp.ClientTimeout(total=15)) as r2:
                if r2.status != 200:
                    return await inter.followup.send("Weather service is unavailable right now.", ephemeral=True)
                wx = await r2.json()

        cur = wx.get("current") or wx.get("current_weather") or {}
        # Normalize
        t = cur.get("temperature_2m") or cur.get("temperature")
        feels = cur.get("apparent_temperature", t)
        rh = cur.get("relative_humidity_2m")
        wind = cur.get("wind_speed_10m") or cur.get("windspeed")
        gust = cur.get("wind_gusts_10m")
        pcp = cur.get("precipitation", 0.0)
        code_now = cur.get("weather_code")
        # Daily today
        daily = wx.get("daily") or {}
        icon, desc = wx_icon_desc((daily.get("weather_code") or [code_now or 0])[0])
        hi = (daily.get("temperature_2m_max") or [None])[0]
        lo = (daily.get("temperature_2m_min") or [None])[0]
        prcp_sum = (daily.get("precipitation_sum") or [0.0])[0]
        prcp_prob = (daily.get("precipitation_probability_max") or [None])[0]
        uv = (daily.get("uv_index_max") or [None])[0]
        sunrise = (daily.get("sunrise") or [None])[0]
        sunset = (daily.get("sunset") or [None])[0]
        wind_max = (daily.get("wind_speed_10m_max") or [None])[0]

        emb = discord.Embed(
            title=f"{icon} Weather — {city}, {state} {z}",
            description=f"**{desc}**",
            colour=wx_color_from_temp_f(t if t is not None else (hi if hi is not None else 70))
        )
        if t is not None:
            emb.add_field(name="Now", value=f"**{round(t)}°F** (feels {round(feels)}°)", inline=True)
        if hi is not None and lo is not None:
            emb.add_field(name="Today", value=f"High **{round(hi)}°** / Low **{round(lo)}°**", inline=True)
        if rh is not None:
            emb.add_field(name="Humidity", value=f"{int(rh)}%", inline=True)
        if wind is not None:
            wind_txt = f"{round(wind)} mph"
            if gust is not None:
                wind_txt += f" (gusts {round(gust)} mph)"
            emb.add_field(name="Wind", value=wind_txt, inline=True)
        emb.add_field(name="Precip (now)", value=f"{pcp:.2f} in", inline=True)
        if prcp_prob is not None:
            emb.add_field(name="Precip Chance", value=f"{int(prcp_prob)}%", inline=True)
        if wind_max is not None:
            emb.add_field(name="Max Wind Today", value=f"{round(wind_max)} mph", inline=True)
        if uv is not None:
            emb.add_field(name="UV Index (max)", value=str(round(uv, 1)), inline=True)
        if sunrise:
            emb.add_field(name="Sunrise", value=fmt_sun(sunrise), inline=True)
        if sunset:
            emb.add_field(name="Sunset", value=fmt_sun(sunset), inline=True)
        await inter.followup.send(embed=emb)
    except Exception as e:
        await inter.followup.send(f"⚠️ Weather error: {e}", ephemeral=True)



# ---------- Weather Subscriptions (Daily/Weekly via DM, Chicago time) ----------
def _next_local_run(now_local: datetime, hh: int, mi: int, cadence: str) -> datetime:
    target = now_local.replace(hour=hh, minute=mi, second=0, microsecond=0)
    if target <= now_local:
        target += timedelta(days=1 if cadence == "daily" else 7)
    return target

async def _zip_to_place_and_coords(session: aiohttp.ClientSession, zip_code: str):
    async with session.get(f"https://api.zippopotam.us/us/{zip_code}", timeout=aiohttp.ClientTimeout(total=12)) as r:
        if r.status != 200:
            raise RuntimeError("Invalid ZIP or lookup failed.")
        zp = await r.json()
    place = zp["places"][0]
    city = place["place name"]; state = place["state abbreviation"]
    lat = float(place["latitude"]); lon = float(place["longitude"])
    return city, state, lat, lon


async def _fetch_outlook(session: aiohttp.ClientSession, lat: float, lon: float, days: int, tz_name: str = "auto"):
    params = {
        "latitude": lat, "longitude": lon,
        "timezone": tz_name,
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,sunrise,sunset,uv_index_max",
    }
    async with session.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
        if r.status != 200:
            raise RuntimeError("Weather API unavailable.")
        data = await r.json()
    daily = data.get("daily") or {}
    out = []
    dates = (daily.get("time") or [])[:days]
    tmax = (daily.get("temperature_2m_max") or [])[:days]
    tmin = (daily.get("temperature_2m_min") or [])[:days]
    prec = (daily.get("precipitation_sum") or [])[:days]
    pop  = (daily.get("precipitation_probability_max") or [])[:days]
    wmax = (daily.get("wind_speed_10m_max") or [])[:days]
    codes = (daily.get("weather_code") or [])[:days]
    rises = (daily.get("sunrise") or [])[:days]
    sets  = (daily.get("sunset") or [])[:days]
    uvs   = (daily.get("uv_index_max") or [])[:days]

    for i, d in enumerate(dates):
        hi = tmax[i] if i < len(tmax) else None
        lo = tmin[i] if i < len(tmin) else None
        pr = prec[i] if i < len(prec) else 0.0
        pp = pop[i] if i < len(pop) else None
        wm = wmax[i] if i < len(wmax) else None
        code = codes[i] if i < len(codes) else 0
        sunrise = rises[i] if i < len(rises) else None
        sunset = sets[i] if i < len(sets) else None
        uv = uvs[i] if i < len(uvs) else None
        icon, desc = wx_icon_desc(code)
        # Build a compact, informative line
        parts = []
        if hi is not None and lo is not None:
            parts.append(f"**{round(hi)}° / {round(lo)}°**")
        if wm is not None:
            parts.append(f"💨 {round(wm)} mph")
        if pp is not None:
            parts.append(f"☔ {int(pp)}%")
        parts.append(f"📏 {pr:.2f} in")
        line = f"{icon} {desc} — " + " - ".join(parts)
        out.append((d, line, sunrise, sunset, uv, hi))
    return out
def _fmt_local(dt_utc: datetime):
    return dt_utc.astimezone(_chicago_tz_for(datetime.now())).strftime("%m-%d-%Y %H:%M %Z")

CADENCE_CHOICES = [
    app_commands.Choice(name="daily", value="daily"),
    app_commands.Choice(name="weekly (send on this weekday)", value="weekly"),
]

@tree.command(name="weather_set_zip", description="Set your default ZIP code for weather features.")
async def weather_set_zip(inter: discord.Interaction, zip: app_commands.Range[str, 5, 10]):
    z = re.sub(r"[^0-9]", "", zip)
    if len(z) != 5:
        return await inter.response.send_message("Please provide a valid 5‑digit US ZIP.", ephemeral=True)
    store.set_user_zip(inter.user.id, z)
    await inter.response.send_message(f"✅ Saved default ZIP: **{z}**", ephemeral=True)

@tree.command(name="weather_subscribe", description="Subscribe to a daily or weekly weather DM at a Chicago-time hour.")
@app_commands.describe(
    time="HH:MM (24h), HHMM, or h:mma/pm in Chicago time",
    cadence="daily or weekly",
    zip="Optional ZIP; uses your saved ZIP if omitted",
    weekly_days="For weekly: number of days to include (3, 7, or 10)"
)
@app_commands.choices(cadence=CADENCE_CHOICES)
async def weather_subscribe(
    inter: discord.Interaction,
    time: str,
    cadence: app_commands.Choice[str],
    zip: Optional[app_commands.Range[str, 5, 10]] = None,
    weekly_days: Optional[app_commands.Range[int, 3, 10]] = 7
):
    await inter.response.defer(ephemeral=True)
    try:
        hh, mi = _parse_time(time)
        z = re.sub(r"[^0-9]", "", zip) if zip else (store.get_user_zip(inter.user.id) or "")
        if len(z) != 5:
            return await inter.followup.send("Set a ZIP with `/weather_set_zip` or provide it here.", ephemeral=True)
        now_local = datetime.now(_chicago_tz_for(datetime.now()))
        first_local = _next_local_run(now_local, hh, mi, cadence.value)
        next_run_utc = first_local.astimezone(timezone.utc)
        sub = {
            "user_id": inter.user.id,
            "zip": z,
            "cadence": cadence.value,
            "hh": int(hh),
            "mi": int(mi),
            "weekly_days": int(weekly_days or 7),
            "next_run_utc": next_run_utc.isoformat(),
        }
        sid = store.add_weather_sub(sub)
        await inter.followup.send(
            f"🌤️ Subscribed **#{sid}** — {cadence.value} at **{first_local.strftime('%I:%M %p %Z')}** for ZIP **{z}**.\n"
            + ("Weekly outlook length: **{} days**.".format(sub['weekly_days']) if cadence.value == "weekly" else "Daily: Today & Tomorrow."),
            ephemeral=True
        )
    except Exception as e:
        await inter.followup.send(f"⚠️ {type(e).__name__}: {e}", ephemeral=True)

@tree.command(name="weather_subscriptions", description="List your weather subscriptions and next send time.")
async def weather_subscriptions(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True)
    items = store.list_weather_subs(inter.user.id)
    if not items:
        return await inter.followup.send("You have no weather subscriptions.", ephemeral=True)

    out_lines = []
    tz = _chicago_tz_for(datetime.now())
    now_local = datetime.now(tz)

    for s in items:
        hh = int(s.get("hh", 8))
        mi = int(s.get("mi", 0))
        cadence = s.get("cadence", "daily") if s.get("cadence") in {"daily", "weekly"} else "daily"

        raw = s.get("next_run_utc")
        nxt = None
        needs = False
        if not raw or str(raw).strip().lower() == "none":
            needs = True
        else:
            try:
                nxt = datetime.fromisoformat(str(raw)).replace(tzinfo=timezone.utc)
            except Exception:
                needs = True

        if not needs and nxt is not None and nxt <= datetime.now(timezone.utc):
            needs = True

        if needs:
            first_local = _next_local_run(now_local, hh, mi, cadence)
            nxt = first_local.astimezone(timezone.utc)
            store.update_weather_sub(s["id"], next_run_utc=nxt.isoformat())

        out_lines.append(
            f"**#{s['id']}** — {cadence} at {hh:02d}:{mi:02d} CT - ZIP {s.get('zip','?????')} - next: {_fmt_local(nxt)}"
        )

    await inter.followup.send("\n".join(out_lines), ephemeral=True)

@tree.command(name="weather_unsubscribe", description="Unsubscribe from weather DMs by ID.")
async def weather_unsubscribe(inter: discord.Interaction, sub_id: int):
    await inter.response.defer(ephemeral=True)
    ok = store.remove_weather_sub(sub_id, requester_id=inter.user.id)
    await inter.followup.send("Removed." if ok else "Couldn't remove that ID.", ephemeral=True)


@tasks.loop(seconds=60)
async def weather_scheduler():
    try:
        now_utc = datetime.now(timezone.utc)
        subs = store.list_weather_subs(None)
        if not subs:
            return
        async with aiohttp.ClientSession(headers=HTTP_HEADERS) as session:
            for s in subs:
                due = datetime.fromisoformat(s["next_run_utc"]).replace(tzinfo=timezone.utc)
                if due <= now_utc:
                    try:
                        user = await bot.fetch_user(int(s["user_id"]))
                        city, state, lat, lon = await _zip_to_place_and_coords(session, s["zip"])
                        if s["cadence"] == "daily":
                            outlook = await _fetch_outlook(session, lat, lon, days=2)
                            # Outlook is list of tuples: (date, line, sunrise, sunset, uv, hi)
                            title_icon = wx_icon_desc(0)[0]
                            first_hi = outlook[0][5] if outlook and outlook[0][5] is not None else None
                            emb = discord.Embed(
                                title=f"🌤️ Daily Outlook — {city}, {state} {s['zip']}",
                                colour=wx_color_from_temp_f(first_hi if first_hi is not None else 70)
                            )
                            for (d, line, sunrise, sunset, uv, _hi) in outlook:
                                # Include sunrise/sunset + UV for "daily" cadence
                                extras = []
                                if sunrise: extras.append(f"🌅 {fmt_sun(sunrise)}")
                                if sunset: extras.append(f"🌇 {fmt_sun(sunset)}")
                                if uv is not None: extras.append(f"🔆 UV {round(uv,1)}")
                                value = "\n".join([line, " - ".join(extras)]) if extras else line
                                emb.add_field(name=d, value=value, inline=False)
                            emb.set_footer(text="Chicago time schedule")
                            await user.send(embed=emb)
                            # schedule next
                            next_local = datetime.now(_chicago_tz_for(datetime.now()))
                            next_local = next_local.replace(hour=s["hh"], minute=s["mi"], second=0, microsecond=0)
                            if next_local <= datetime.now(_chicago_tz_for(datetime.now())):
                                next_local += timedelta(days=1)
                            store.update_weather_sub(s["id"], user_id=int(s["user_id"]), next_run_utc=next_local.astimezone(timezone.utc).isoformat())
                        else:
                            days = int(s.get("weekly_days", 7))
                            days = 10 if days > 10 else (3 if days < 3 else days)
                            outlook = await _fetch_outlook(session, lat, lon, days=days)
                            first_hi = outlook[0][5] if outlook and outlook[0][5] is not None else None
                            emb = discord.Embed(
                                title=f"🗓️ Weekly Outlook ({days} days) — {city}, {state} {s['zip']}",
                                colour=wx_color_from_temp_f(first_hi if first_hi is not None else 70)
                            )
                            for (d, line, _sunrise, _sunset, _uv, _hi) in outlook:
                                emb.add_field(name=d, value=line, inline=False)
                            emb.set_footer(text="Chicago time schedule")
                            await user.send(embed=emb)
                            # schedule next week
                            next_local = datetime.now(_chicago_tz_for(datetime.now()))
                            next_local = next_local.replace(hour=s["hh"], minute=s["mi"], second=0, microsecond=0)
                            if next_local <= datetime.now(_chicago_tz_for(datetime.now())):
                                next_local += timedelta(days=7)
                            else:
                                next_local += timedelta(days=7)
                            store.update_weather_sub(s["id"], user_id=int(s["user_id"]), next_run_utc=next_local.astimezone(timezone.utc).isoformat())
                    except Exception:
                        fallback = now_utc + timedelta(minutes=5)
                        store.update_weather_sub(s["id"], next_run_utc=fallback.isoformat())
    except Exception:
        pass

@weather_scheduler.before_loop
async def before_weather():
    await bot.wait_until_ready()

# ---------- NEW: Shop / Inventory / Fishing ----------
def _find_catalog_item(name: str) -> Optional[str]:
    # case-insensitive name match
    name_norm = name.strip().lower()
    for k in SHOP_CATALOG.keys():
        if k.lower() == name_norm:
            return k
    # partial match convenience
    matches = [k for k in SHOP_CATALOG if name_norm in k.lower()]
    return matches[0] if len(matches) == 1 else None

# ========= Reaction Shop UI =========
NUMS = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣"]
CTRL_PREV = "◀️"
CTRL_NEXT = "▶️"
CTRL_BUY  = "🛒"
CTRL_SELL = "💵"
CTRL_PLUS = "➕"
CTRL_MINUS= "➖"
CTRL_OK   = "✅"
CTRL_CANCEL="❌"

def _shop_pages():
    items = list(SHOP_CATALOG.items())
    page, pages = [], []
    for i, pair in enumerate(items, 1):
        page.append(pair)
        if len(page) == 9 or i == len(items):
            pages.append(page)
            page = []
    return pages

def _shop_embed(user: discord.User, page_idx: int, mode: str, sel_idx: int, qty: int) -> discord.Embed:
    pages = _shop_pages()
    page = pages[page_idx]
    bal = store.get_balance(user.id)

    title = f"🛒 Shop — Balance: **{bal}** cr"
    mode_txt = "Buy" if mode == "buy" else "Sell"
    emb = discord.Embed(title=title, description=f"Mode: **{mode_txt}**   •   Qty: **{qty}**")
    lines = []
    for i, (name, meta) in enumerate(page, 1):
        buy_str = f"{meta['price']} cr" if meta.get("price") is not None else "—"
        sell_str = f"{meta.get('sell',0)} cr" if meta.get("sell") is not None else "—"
        pointer = "👉 " if (sel_idx == i-1) else ""
        desc = meta.get('desc','')
        lines.append(f"{pointer}{NUMS[i-1]} **{name}** — Buy: {buy_str} • Sell: {sell_str}\n_{desc}_")
    emb.add_field(name=f"Page {page_idx+1}/{len(pages)}", value="\n".join(lines), inline=False)

    try:
        name, meta = page[sel_idx]
        price = meta.get("price")
        sell = meta.get("sell")
        total_buy = (price or 0) * qty
        total_sell = (sell or 0) * qty
        if mode == "buy":
            emb.add_field(name="Selected (Buy)", value=f"**{name}** × {qty}  →  **{total_buy}** cr", inline=False)
        else:
            have = store.get_inventory(user.id).get(name, 0)
            emb.add_field(name="Selected (Sell)", value=f"**{name}** × {qty} (you have {have})  →  **{total_sell}** cr", inline=False)
    except Exception:
        pass

    emb.set_footer(text="1️⃣–9️⃣ select • ◀️▶️ page • 🛒 buy • 💵 sell • ➕➖ qty • ✅ confirm • ❌ close")
    return emb

@tree.command(name="shop", description="Interactive shop: show balance and buy/sell with reactions.")
async def shop_cmd(inter: discord.Interaction):
    await inter.response.defer()
    user = inter.user
    pages = _shop_pages()
    page_idx = 0
    sel_idx = 0
    qty = 1
    mode = "buy"  # 'buy' or 'sell'

    msg = await inter.followup.send(embed=_shop_embed(user, page_idx, mode, sel_idx, qty))

    # Add reactions
    for em in NUMS[:len(pages[page_idx])]:
        try: await msg.add_reaction(em)
        except discord.HTTPException: pass
    for em in [CTRL_PREV, CTRL_NEXT, CTRL_BUY, CTRL_SELL, CTRL_MINUS, CTRL_PLUS, CTRL_OK, CTRL_CANCEL]:
        try: await msg.add_reaction(em)
        except discord.HTTPException: pass

    def check(reaction: discord.Reaction, reactor: discord.User):
        return (
            reactor.id == user.id and
            reaction.message.id == msg.id and
            str(reaction.emoji) in NUMS + [CTRL_PREV, CTRL_NEXT, CTRL_BUY, CTRL_SELL, CTRL_MINUS, CTRL_PLUS, CTRL_OK, CTRL_CANCEL]
        )

    try:
        while True:
            try:
                reaction, reactor = await bot.wait_for("reaction_add", timeout=120.0, check=check)
            except asyncio.TimeoutError:
                break

            emoji = str(reaction.emoji)

            # Clear user's reaction (tidy UI)
            try:
                await msg.remove_reaction(emoji, user)
            except Exception:
                pass

            if emoji in NUMS:
                idx = NUMS.index(emoji)
                if idx < len(pages[page_idx]):
                    sel_idx = idx

            elif emoji == CTRL_PREV:
                if page_idx > 0:
                    page_idx -= 1
                    sel_idx = 0
            elif emoji == CTRL_NEXT:
                if page_idx < len(pages)-1:
                    page_idx += 1
                    sel_idx = 0

            elif emoji == CTRL_BUY:
                mode = "buy"
            elif emoji == CTRL_SELL:
                mode = "sell"

            elif emoji == CTRL_PLUS:
                qty = min(qty + 1, 1000)
            elif emoji == CTRL_MINUS:
                qty = max(qty - 1, 1)

            elif emoji == CTRL_CANCEL:
                await msg.edit(content="🛑 Shop closed.", embed=None)
                return

            elif emoji == CTRL_OK:
                name, meta = pages[page_idx][sel_idx]
                if mode == "buy":
                    price = meta.get("price")
                    if price is None:
                        await inter.followup.send("That item cannot be purchased.", ephemeral=True)
                    else:
                        total = price * qty
                        bal = store.get_balance(user.id)
                        if bal < total:
                            await inter.followup.send(f"Not enough credits. Need **{total}**.", ephemeral=True)
                        else:
                            store.add_balance(user.id, -total)
                            store.add_item(user.id, name, qty)
                            await inter.followup.send(f"✅ Bought **{qty}× {name}** for **{total}** credits.", ephemeral=True)
                else:  # sell
                    sell_val = meta.get("sell")
                    if sell_val is None:
                        await inter.followup.send("That item cannot be sold.", ephemeral=True)
                    else:
                        inv = store.get_inventory(user.id)
                        have = inv.get(name, 0)
                        if have < qty:
                            await inter.followup.send(f"You only have **{have}× {name}**.", ephemeral=True)
                        else:
                            store.remove_item(user.id, name, qty)
                            total = sell_val * qty
                            store.add_balance(user.id, total)
                            await inter.followup.send(f"💸 Sold **{qty}× {name}** for **{total}** credits.", ephemeral=True)

            try:
                await msg.edit(embed=_shop_embed(user, page_idx, mode, sel_idx, qty))
            except discord.HTTPException:
                pass

    finally:
        try:
            await msg.clear_reactions()
        except Exception:
            pass
        try:
            await msg.edit(content="⌛ Session expired.", embed=None)
        except Exception:
            pass
@tree.command(name="buy", description="Buy an item from the shop.")
@app_commands.describe(item="Exact item name (e.g., 'Fishing Pole')", quantity="Defaults to 1")
async def buy_cmd(inter: discord.Interaction, item: str, quantity: app_commands.Range[int, 1, 100] = 1):
    key = _find_catalog_item(item)
    if not key:
        return await inter.response.send_message("Item not found. Use `/shop` to see names.", ephemeral=True)
    meta = SHOP_CATALOG[key]
    if meta["price"] is None:
        return await inter.response.send_message("That item cannot be purchased.", ephemeral=True)
    total = int(meta["price"]) * int(quantity)
    bal = store.get_balance(inter.user.id)
    if bal < total:
        return await inter.response.send_message(f"Not enough credits. Need **{total}**.", ephemeral=True)
    store.add_balance(inter.user.id, -total)
    store.add_item(inter.user.id, key, quantity)
    await inter.response.send_message(f"✅ Bought **{quantity}× {key}** for **{total}** credits.")

@tree.command(name="sell", description="Sell an item from your inventory.")
@app_commands.describe(item="Exact item name", quantity="How many to sell")
async def sell_cmd(inter: discord.Interaction, item: str, quantity: app_commands.Range[int, 1, 1000]):
    key = _find_catalog_item(item)
    if not key:
        return await inter.response.send_message("Item not found.", ephemeral=True)
    meta = SHOP_CATALOG[key]
    sell_val = meta.get("sell")
    if sell_val is None:
        return await inter.response.send_message("That item cannot be sold.", ephemeral=True)
    if not store.has_item(inter.user.id, key, quantity):
        return await inter.response.send_message("You don't have that many.", ephemeral=True)
    ok = store.remove_item(inter.user.id, key, quantity)
    if not ok:
        return await inter.response.send_message("You don't have that many.", ephemeral=True)
    total = int(sell_val) * int(quantity)
    store.add_balance(inter.user.id, total)
    await inter.response.send_message(f"💸 Sold **{quantity}× {key}** for **{total}** credits.")

@tree.command(name="inventory", description="View your inventory (or another user's).")
async def inventory_cmd(inter: discord.Interaction, user: Optional[discord.User] = None):
    target = user or inter.user
    inv = store.get_inventory(target.id)
    if not inv:
        return await inter.response.send_message(f"{target.display_name} has an empty inventory.")
    lines = [f"- **{name}** × {qty}" for name, qty in sorted(inv.items())]
    emb = discord.Embed(title=f"🎒 Inventory — {target.display_name}", description="\n".join(lines))
    await inter.response.send_message(embed=emb)

@tree.command(name="fish", description="Go fishing! Requires a Fishing Pole and 1 bait (Basic or Premium).")
async def fish_cmd(inter: discord.Interaction):
    """Fishing with a 'Fish again' button. Consumes bait each time."""
    uid = inter.user.id

    def _fish_once(uid: int):
        # Checks
        if not store.has_item(uid, "Fishing Pole", 1):
            return None, None, None, None, "You need a **Fishing Pole**. Buy one with `/buy Fishing Pole`."
        bait_type = None
        if store.has_item(uid, "Premium Bait", 1):
            bait_type = "Premium Bait"
            table = FISH_TABLE_PREMIUM
        elif store.has_item(uid, "Basic Bait", 1):
            bait_type = "Basic Bait"
            table = FISH_TABLE_BASIC
        else:
            return None, None, None, None, "You need **Basic Bait** or **Premium Bait**."
        # Consume bait
        store.remove_item(uid, bait_type, 1)
        catch = weighted_choice(table)
        store.add_item(uid, catch, 1)
        sell_val = SHOP_CATALOG.get(catch, {}).get("sell", 0) or 0
        flair = "🎣"
        if "Rare" in catch: flair = "💎"
        if "Epic" in catch: flair = "🌟"
        return bait_type, catch, sell_val, flair, None

    class FishAgainView(discord.ui.View):
        def __init__(self, uid: int, timeout: float = 180):
            super().__init__(timeout=timeout)
            self.uid = uid

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.uid:
                await interaction.response.send_message("This isn’t your fishing session.", ephemeral=True)
                return False
            return True

        @discord.ui.button(label="🎣 Fish again", style=discord.ButtonStyle.primary)
        async def fish_again(self, interaction: discord.Interaction, button: discord.ui.Button):
            bait_type, catch, sell_val, flair, err = _fish_once(self.uid)
            if err:
                # Disable button if they can't fish anymore
                button.disabled = True
                await interaction.response.edit_message(content=err, view=self)
                return
            emb = discord.Embed(
                title="Gone Fishin'",
                description=f"{flair} You cast your line using **{bait_type}** and caught **{catch}**! (Sell value: **{sell_val}** cr)"
            )
            await interaction.response.edit_message(embed=emb, view=self)

    # Run the first cast and send initial message with the view
    bait_type, catch, sell_val, flair, err = _fish_once(uid)
    view = FishAgainView(uid=uid)
    if err:
        await inter.response.send_message(err, ephemeral=True)
        return
    emb = discord.Embed(
        title="Gone Fishin'",
        description=f"{flair} You cast your line using **{bait_type}** and caught **{catch}**! (Sell value: **{sell_val}** cr)"
    )
    await inter.response.send_message(embed=emb, view=view)
# ---------- Notes ----------
@tree.command(name="note_add", description="Save a personal note.")
async def note_add(inter: discord.Interaction, text: str):
    store.add_note(inter.user.id, text)
    await inter.response.send_message("📝 Saved.", ephemeral=True)

@tree.command(name="notes", description="List or delete your notes.")
async def notes(inter: discord.Interaction, delete_index: Optional[app_commands.Range[int, 1, 999]] = None):
    if delete_index:
        ok = store.delete_note(inter.user.id, int(delete_index))
        if ok:
            return await inter.response.send_message("🗑️ Deleted.", ephemeral=True)
        else:
            return await inter.response.send_message("ID not found.", ephemeral=True)
    arr = store.list_notes(inter.user.id)  # returns [(id, text)]
    if not arr:
        return await inter.response.send_message("No notes.", ephemeral=True)
    lines = [f"{i}. {t}" for i, t in arr]
    await inter.response.send_message("\n".join(lines), ephemeral=True)

# ---------- Channel Pin ----------
@tree.command(name="pin_set", description="Set a sticky pin for this channel.")
async def pin_set(inter: discord.Interaction, text: str):
    if not isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
        return await inter.response.send_message("Use this in a text channel.", ephemeral=True)
    store.set_pin(inter.channel.id, text)
    await inter.response.send_message("📌 Pin set.", ephemeral=True)

@tree.command(name="pin_show", description="Show the sticky pin for this channel.")
async def pin_show(inter: discord.Interaction):
    if not isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
        return await inter.response.send_message("Use this in a text channel.", ephemeral=True)
    text = store.get_pin(inter.channel.id)
    await inter.response.send_message(text or "No pin.", ephemeral=True)

@tree.command(name="pin_clear", description="Clear the sticky pin for this channel.")
async def pin_clear(inter: discord.Interaction):
    if not isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
        return await inter.response.send_message("Use this in a text channel.", ephemeral=True)
    store.clear_pin(inter.channel.id)
    await inter.response.send_message("🧹 Pin cleared.", ephemeral=True)

# ---------- Polls (persistent) ----------
class PollView(discord.ui.View):
    def __init__(self, message_id: int, options: List[str], creator_id: int, timeout: Optional[float] = None):
        super().__init__(timeout=timeout)  # persistent
        self.message_id = message_id
        self.creator_id = creator_id
        for i, label in enumerate(options):
            self.add_item(self._make_vote_button(i, label))
        self.add_item(self._make_close_button())

    def _make_vote_button(self, idx: int, label: str):
        custom_id = f"poll_vote:{self.message_id}:{idx}"
        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, custom_id=custom_id)
        async def cb(inter: discord.Interaction):
            p = store.get_poll(self.message_id)
            if not p or not p.get("open", True):
                return await inter.response.send_message("Poll is closed.", ephemeral=True)
            p["options"][idx]["votes"] += 1
            store.save_poll(self.message_id, p)
            await inter.response.defer()
            await update_poll_message(inter.channel, self.message_id, p)
        btn.callback = cb
        return btn

    def _make_close_button(self):
        custom_id = f"poll_close:{self.message_id}"
        btn = discord.ui.Button(label="Close Poll", style=discord.ButtonStyle.danger, custom_id=custom_id)
        async def cb(inter: discord.Interaction):
            p = store.get_poll(self.message_id)
            if not p: return
            is_mod = False
            try:
                if isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
                    is_mod = inter.channel.permissions_for(inter.user).manage_messages
            except Exception:
                pass
            if (inter.user.id != p.get("creator_id")) and not is_mod:
                return await inter.response.send_message("Only the creator or a mod can close this poll.", ephemeral=True)
            p["open"] = False
            store.save_poll(self.message_id, p)
            await inter.response.defer()
            await update_poll_message(inter.channel, self.message_id, p)
        btn.callback = cb
        return btn

async def update_poll_message(channel: discord.abc.Messageable, message_id: int, poll: dict):
    try:
        if hasattr(channel, "fetch_message"):
            msg = await channel.fetch_message(message_id)
        else:
            return
        bars = []
        total = sum(o["votes"] for o in poll["options"])
        for o in poll["options"]:
            pct = 0 if total == 0 else int((o["votes"] / total) * 100)
            bars.append(f"**{o['label']}** — {o['votes']} ({pct}%)")
        emb = discord.Embed(title="📊 " + poll["question"], description="\n".join(bars))
        emb.set_footer(text="Open" if poll.get("open", True) else "Closed")
        view = PollView(message_id=message_id, options=[o["label"] for o in poll["options"]], creator_id=poll.get("creator_id", 0))
        await msg.edit(embed=emb, view=(view if poll.get("open", True) else None))
    except Exception:
        pass

@tree.command(name="poll", description="Create a quick poll. Options separated by semicolons.")
async def poll(inter: discord.Interaction, question: str, options: str):
    opts = [o.strip() for o in options.split(";") if o.strip()]
    if len(opts) < 2 or len(opts) > 5:
        return await inter.response.send_message("Provide 2–5 options separated by semicolons.", ephemeral=True)
    emb = discord.Embed(title="📊 " + question, description="\n".join(f"**{o}** — 0 (0%)" for o in opts))
    await inter.response.send_message(embed=emb)
    msg = await inter.original_response()
    poll_data = {"question": question, "options": [{"label": o, "votes": 0} for o in opts], "creator_id": inter.user.id, "open": True}
    store.save_poll(msg.id, poll_data)
    view = PollView(message_id=msg.id, options=opts, creator_id=inter.user.id, timeout=None)
    await msg.edit(view=view)

# ---------- Helpers: choose & timer ----------
@tree.command(name="choose", description="Pick a random choice from a comma-separated list.")
async def choose(inter: discord.Interaction, options: str):
    arr = [x.strip() for x in options.split(",") if x.strip()]
    if len(arr) < 2:
        return await inter.response.send_message("Give me at least two options separated by commas.", ephemeral=True)
    pick = random.choice(arr)
    await inter.response.send_message(f"🎯 I pick: **{pick}**")

@tree.command(name="timer", description="Start a countdown timer.")
async def timer(inter: discord.Interaction, seconds: app_commands.Range[int, 1, 36000]):
    total = int(seconds)
    await inter.response.send_message(f"⏳ Timer: **{total}s**")
    msg = await inter.original_response()
    step = 1 if total <= 60 else 5
    remaining = total
    while remaining > 0:
        await asyncio.sleep(step)
        remaining = max(0, remaining - step)
        try:
            await msg.edit(content=f"⏳ Timer: **{remaining}s**")
        except discord.HTTPException:
            break
    try:
        await msg.edit(content="✅ Time!")
    except discord.HTTPException:
        pass

# ---------- Trivia via OpenTDB ----------
async def _get_or_create_trivia_token(session: aiohttp.ClientSession) -> Optional[str]:
    token = store.get_trivia_token()
    if token:
        return token
    try:
        async with session.get(TRIVIA_TOKEN_API, params={"command": "request"}) as resp:
            data = await resp.json()
            t = data.get("token")
            if t:
                store.set_trivia_token(t)
                return t
    except Exception:
        return None
    return None

async def _reset_trivia_token(session: aiohttp.ClientSession) -> Optional[str]:
    token = store.get_trivia_token()
    if not token:
        return await _get_or_create_trivia_token(session)
    try:
        async with session.get(TRIVIA_TOKEN_API, params={"command": "reset", "token": token}) as resp:
            _ = await resp.json()
        return token
    except Exception:
        return None

async def _fetch_from_opentdb(session: aiohttp.ClientSession, difficulty: Optional[str] = None):
    token = await _get_or_create_trivia_token(session)
    params = {"amount": 1, "type": "multiple", "encode": "url3986"}
    if difficulty in {"easy", "medium", "hard"}:
        params["difficulty"] = difficulty
    if token:
        params["token"] = token

    async def _do_request():
        async with session.get(TRIVIA_API, params=params, timeout=aiohttp.ClientTimeout(total=15), headers=HTTP_HEADERS) as resp:
            if resp.status != 200:
                return None
            try:
                return await resp.json()
            except Exception:
                return None

    try:
        data = await _do_request()
        if not data:
            return None
        rc = data.get("response_code", 1)
        if rc == 4 and token:
            # Token empty; reset and try once more
            await _reset_trivia_token(session)
            data = await _do_request()
            if not data:
                return None
            rc = data.get("response_code", 1)
        if rc != 0 or not data.get("results"):
            return None
        item = data["results"][0]
        q = html.unescape(urllib.parse.unquote(item.get("question", "")))
        correct = html.unescape(urllib.parse.unquote(item.get("correct_answer", "")))
        incorrect = [html.unescape(urllib.parse.unquote(x)) for x in item.get("incorrect_answers", [])]
        if not q or not correct or len(incorrect) < 1:
            return None
        choices = incorrect + [correct]
        # Ensure exactly 4 options if possible
        while len(choices) < 4:
            choices.append("None of the above")
        choices = choices[:4]
        random.shuffle(choices)
        correct_idx = choices.index(correct) if correct in choices else 3
        return q, choices, correct_idx
    except Exception:
        return None
    except Exception:
        return None

async def _fetch_from_the_trivia_api(session: aiohttp.ClientSession, difficulty: Optional[str] = None):
    # Docs: https://the-trivia-api.com/docs/v2/#get-questions
    params = {"limit": 1, "types": "multiple"}
    if difficulty in {"easy", "medium", "hard"}:
        params["difficulties"] = difficulty
    try:
        async with session.get(TRIVIA_API_FALLBACK, params=params, timeout=aiohttp.ClientTimeout(total=15), headers=HTTP_HEADERS) as resp:
            if resp.status != 200:
                return None
            try:
                data = await resp.json()
            except Exception:
                return None
        if not isinstance(data, list) or not data:
            return None
        item = data[0]
        q = str(item.get("question", {}).get("text", "")).strip()
        correct = str(item.get("correctAnswer", "")).strip()
        incorrect_raw = item.get("incorrectAnswers", [])
        incorrect = [str(x).strip() for x in incorrect_raw] if isinstance(incorrect_raw, list) else []
        if not q or not correct or not incorrect:
            return None
        choices = incorrect + [correct]
        while len(choices) < 4:
            choices.append("None of the above")
        choices = choices[:4]
        random.shuffle(choices)
        # Unescape possible HTML entities
        q = html.unescape(q)
        choices = [html.unescape(c) for c in choices]
        correct_idx = choices.index(correct) if correct in choices else 3
        return q, choices, correct_idx
    except Exception:
        return None
    except Exception:
        return None

async def _fetch_from_offline():
    try:
        item = random.choice(OFFLINE_TRIVIA)
        q = item["q"]
        choices = list(item["choices"])
        correct_idx = int(item["answer_idx"])
        # shuffle but keep track of the correct index
        paired = list(enumerate(choices))
        random.shuffle(paired)
        choices = [c for _, c in paired]
        for new_idx, (old_idx, _) in enumerate(paired):
            if old_idx == correct_idx:
                correct_idx = new_idx
                break
        return q, choices, correct_idx
    except Exception:
        return None

async def fetch_trivia_question(session: aiohttp.ClientSession, difficulty: Optional[str] = None):
    # Try OpenTDB first
    primary = await _fetch_from_opentdb(session, difficulty)
    if primary:
        return primary
    # Then The Trivia API
    secondary = await _fetch_from_the_trivia_api(session, difficulty)
    if secondary:
        return secondary
    # Finally, offline pack
    return await _fetch_from_offline()

DIFF_CHOICES = [
    app_commands.Choice(name="Any", value=""),
    app_commands.Choice(name="Easy", value="easy"),
    app_commands.Choice(name="Medium", value="medium"),
    app_commands.Choice(name="Hard", value="hard"),
]

@tree.command(name="trivia", description="Answer a multiple-choice question for credits (powered by OpenTDB).")
@app_commands.describe(difficulty="Pick a difficulty (default Any).")
@app_commands.choices(difficulty=DIFF_CHOICES)
async def trivia(inter: discord.Interaction, difficulty: Optional[app_commands.Choice[str]] = None):
    await inter.response.defer()
    diff_val = difficulty.value if difficulty else None
    async with aiohttp.ClientSession() as session:
        fetched = await fetch_trivia_question(session, diff_val or None)
    if not fetched:
        await inter.followup.send("⚠️ Couldn't fetch a trivia question right now. Try again in a bit.")
        return
    q, choices, correct_idx = fetched
    emb = discord.Embed(title="🧠 Trivia Time", description=q)
    letters = ["A","B","C","D"]
    for i, c in enumerate(choices):
        emb.add_field(name=letters[i], value=html.unescape(c), inline=False)
    emb.set_footer(text=f"Correct = +{TRIVIA_REWARD} credits")

    class TriviaView(discord.ui.View):
        def __init__(self, uid: int, timeout: float = 30):
            super().__init__(timeout=timeout)
            self.uid = uid
            self.choice: Optional[int] = None
            for i, lab in enumerate(letters):
                self.add_item(self._make_button(lab, i))
        def _make_button(self, label: str, idx: int):
            async def cb(interaction: discord.Interaction, idx=idx):
                if interaction.user.id != self.uid:
                    await interaction.response.send_message("This question isn't for you.", ephemeral=True)
                    return
                self.choice = idx
                await interaction.response.defer()
                self.stop()
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
            btn.callback = cb
            return btn

    view = TriviaView(uid=inter.user.id, timeout=30)
    msg = await inter.followup.send(embed=emb, view=view)
    await view.wait()
    if view.choice is None:
        await msg.edit(content="⌛ Time's up!", embed=None, view=None)
        return
    if view.choice == correct_idx:
        store.add_balance(inter.user.id, TRIVIA_REWARD)
        await msg.edit(content=f"✅ Correct! You earned **{TRIVIA_REWARD}** credits.", embed=None, view=None)
    else:
        await msg.edit(content=f"❌ Nope. Correct answer was **{letters[correct_idx]}**.", embed=None, view=None)

# ---------- Blackjack (Dealer or PvP) ----------
def is_blackjack(cards: List[Tuple[str, str]]) -> bool:
    return len(cards) == 2 and hand_value(cards) == 21

class PvPBlackjackView(discord.ui.View):
    def __init__(self, current_player_id: int, timeout: float = 120):
        super().__init__(timeout=timeout)
        self.current_player_id = current_player_id
        self.choice: Optional[str] = None
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.current_player_id:
            await interaction.response.send_message("It's not your turn.", ephemeral=True); return False
        return True
    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "hit"; await interaction.response.defer(); self.stop()
    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "stand"; await interaction.response.defer(); self.stop()

class PvPChallengeView(discord.ui.View):
    def __init__(self, challenger_id: int, challenged_id: int, timeout: float = PVP_TIMEOUT):
        super().__init__(timeout=timeout); self.challenger_id = challenger_id; self.challenged_id = challenged_id; self.accepted: Optional[bool] = None
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.challenged_id:
            await interaction.response.send_message("Only the challenged user can respond.", ephemeral=True); return False
        return True
    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.accepted = True; await interaction.response.defer(); self.stop()
    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.accepted = False; await interaction.response.defer(); self.stop()

@tree.command(name="blackjack", description="Play Blackjack vs dealer or challenge another user (spectator-friendly).")
@app_commands.describe(bet="Amount to wager", opponent="Optional: challenge another user for PvP")
async def blackjack(inter: discord.Interaction, bet: app_commands.Range[int, 1, 1_000_000], opponent: Optional[discord.User] = None):
    bettor_id = inter.user.id; bal = store.get_balance(bettor_id)
    if bal < bet: return await inter.response.send_message("❌ Not enough credits for that bet.", ephemeral=True)
    if opponent and opponent.id != inter.user.id and not opponent.bot:
        opp_bal = store.get_balance(opponent.id)
        if opp_bal < bet: return await inter.response.send_message(f"❌ {opponent.mention} doesn’t have enough credits to accept.", ephemeral=True)
        view_challenge = PvPChallengeView(challenger_id=inter.user.id, challenged_id=opponent.id)
        await inter.response.send_message(f"🎲 {opponent.mention}, **{inter.user.display_name}** challenges you to Blackjack for **{bet}** credits each. Accept?", view=view_challenge)
        await view_challenge.wait()
        if view_challenge.accepted is None: return await inter.followup.send("⌛ Challenge expired.")
        if not view_challenge.accepted: return await inter.followup.send("🚫 Challenge declined.")
        deck = deal_deck(); p1 = [deck.pop(), deck.pop()]; p2 = [deck.pop(), deck.pop()]; current_player_id = inter.user.id
        def embed_state(title_suffix: str = ""):
            title = f"♠ PvP Blackjack {title_suffix}".strip()
            emb = discord.Embed(title=title, description=f"Bet each: **{bet}**")
            emb.add_field(name=f"{inter.user.display_name}", value=fmt_hand(p1), inline=True)
            emb.add_field(name=f"{opponent.display_name}", value=fmt_hand(p2), inline=True)
            emb.add_field(name="Turn", value=f"▶️ **{inter.user.display_name if current_player_id==inter.user.id else opponent.display_name}**", inline=False)
            return emb
        view = PvPBlackjackView(current_player_id=current_player_id)
        msg = await inter.followup.send(embed=embed_state("— Game Start"), view=view)
        async def play_turn(player_id: int, hand: List[Tuple[str, str]], name: str):
            nonlocal view, msg, current_player_id
            current_player_id = player_id; view = PvPBlackjackView(current_player_id=current_player_id)
            try: await msg.edit(embed=embed_state(), view=view)
            except discord.HTTPException: pass
            while hand_value(hand) < 21:
                await view.wait(); choice = view.choice; view.choice = None
                if choice == "hit":
                    hand.append(deck.pop())
                    try: await msg.edit(embed=embed_state())
                    except discord.HTTPException: pass
                    view = PvPBlackjackView(current_player_id=current_player_id)
                    try: await msg.edit(view=view)
                    except discord.HTTPException: pass
                    continue
                else: break
            for child in view.children:
                if isinstance(child, discord.ui.Button): child.disabled = True
            try: await msg.edit(view=view)
            except discord.HTTPException: pass
        await play_turn(inter.user.id, p1, inter.user.display_name); await play_turn(opponent.id, p2, opponent.display_name)
        v1 = hand_value(p1); v2 = hand_value(p2); outcome = "Tie! It’s a push."
        if v1 > 21 and v2 > 21: store.add_result(inter.user.id, "push"); store.add_result(opponent.id, "push")
        elif v1 > 21:
            outcome = f"**{opponent.display_name}** wins!"; store.add_balance(inter.user.id, -bet); store.add_balance(opponent.id, bet); store.add_result(inter.user.id, "loss"); store.add_result(opponent.id, "win")
        elif v2 > 21:
            outcome = f"**{inter.user.display_name}** wins!"; store.add_balance(opponent.id, -bet); store.add_balance(inter.user.id, bet); store.add_result(opponent.id, "loss"); store.add_result(inter.user.id, "win")
        else:
            if v1 > v2:
                outcome = f"**{inter.user.display_name}** wins!"; store.add_balance(opponent.id, -bet); store.add_balance(inter.user.id, bet); store.add_result(opponent.id, "loss"); store.add_result(inter.user.id, "win")
            elif v2 > v1:
                outcome = f"**{opponent.display_name}** wins!"; store.add_balance(inter.user.id, -bet); store.add_balance(opponent.id, bet); store.add_result(inter.user.id, "loss"); store.add_result(opponent.id, "win")
            else: store.add_result(inter.user.id, "push"); store.add_result(opponent.id, "push")
        emb = discord.Embed(title="♠ PvP Blackjack — Result", description=f"Bet each: **{bet}**")
        emb.add_field(name=inter.user.display_name, value=fmt_hand(p1), inline=True)
        emb.add_field(name=opponent.display_name, value=fmt_hand(p2), inline=True)
        emb.add_field(name="Outcome", value=outcome, inline=False)
        try: await msg.edit(embed=emb, view=None)
        except discord.HTTPException: await inter.followup.send(embed=emb)
        return
    # Dealer
    deck = deal_deck(); player = [deck.pop(), deck.pop()]; dealer = [deck.pop(), deck.pop()]
    class DealerView(discord.ui.View):
        def __init__(self, uid: int, timeout: float = 120):
            super().__init__(timeout=timeout); self.uid = uid; self.choice: Optional[str] = None
        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.uid: await interaction.response.send_message("This isn’t your game.", ephemeral=True); return False
            return True
        @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
        async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.choice = "hit"; await interaction.response.defer(); self.stop()
        @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
        async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.choice = "stand"; await interaction.response.defer(); self.stop()
    def dealer_embed(title="♣ Blackjack vs Dealer"):
        emb = discord.Embed(title=title, description=f"Bet: **{bet}**")
        emb.add_field(name="Your Hand", value=fmt_hand(player), inline=True)
        emb.add_field(name="Dealer Shows", value=f"{dealer[0][0]}{dealer[0][1]} ??", inline=True)
        return emb
    await inter.response.send_message(embed=dealer_embed(), view=DealerView(uid=inter.user.id))
    msg = await inter.original_response()
    view = DealerView(uid=inter.user.id); await msg.edit(view=view)
    while True:
        if hand_value(player) >= 21: break
        await view.wait(); choice = view.choice; view.choice = None
        if choice == "hit":
            player.append(deck.pop())
            try: await msg.edit(embed=dealer_embed(), view=view)
            except discord.HTTPException: pass
            view = DealerView(uid=inter.user.id)
            try: await msg.edit(view=view)
            except discord.HTTPException: pass
            continue
        else: break
    while hand_value(dealer) < 17: dealer.append(deck.pop())
    pv = hand_value(player); dv = hand_value(dealer)
    if pv > 21: result = "You busted. Dealer wins."; store.add_balance(inter.user.id, -bet); store.add_result(inter.user.id, "loss")
    elif dv > 21 or pv > dv: result = "You win!"; store.add_balance(inter.user.id, bet); store.add_result(inter.user.id, "win")
    elif dv > pv: result = "Dealer wins."; store.add_balance(inter.user.id, -bet); store.add_result(inter.user.id, "loss")
    else: result = "Push."; store.add_result(inter.user.id, "push")
    final = discord.Embed(title="♣ Blackjack vs Dealer — Result", description=f"Bet: **{bet}**")
    final.add_field(name="Your Hand", value=fmt_hand(player), inline=True)
    final.add_field(name="Dealer Hand", value=fmt_hand(dealer), inline=True)
    final.add_field(name="Outcome", value=result, inline=False)
    await msg.edit(embed=final, view=None)

# ---------- High/Low ----------
@tree.command(name="highlow", description="Guess if the next card is higher or lower (1:1 payout).")
@app_commands.describe(bet="Amount to wager")
async def highlow(inter: discord.Interaction, bet: app_commands.Range[int, 1, 1_000_000]):
    if store.get_balance(inter.user.id) < bet:
        return await inter.response.send_message("❌ Not enough credits for that bet.", ephemeral=True)
    deck = deal_deck(); first = deck.pop()
    def rank_value(r: str) -> int:
        order = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]
        return order.index(r)
    class HLView(discord.ui.View):
        def __init__(self, uid: int, timeout: float = 60):
            super().__init__(timeout=timeout); self.uid = uid; self.choice: Optional[str] = None
        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.uid: await interaction.response.send_message("This isn’t your round.", ephemeral=True); return False
            return True
        @discord.ui.button(label="Higher", style=discord.ButtonStyle.primary)
        async def higher(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.choice = "higher"; await interaction.response.defer(); self.stop()
        @discord.ui.button(label="Lower", style=discord.ButtonStyle.secondary)
        async def lower(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.choice = "lower"; await interaction.response.defer(); self.stop()
    def make_embed(title="♦ High/Low"):
        emb = discord.Embed(title=title, description=f"Bet: **{bet}**")
        emb.add_field(name="Current Card", value=f"{first[0]}{first[1]}", inline=True)
        emb.set_footer(text="Guess if the next card is higher or lower."); return emb
    view = HLView(uid=inter.user.id)
    await inter.response.send_message(embed=make_embed(), view=view); msg = await inter.original_response()
    await view.wait()
    second = deck.pop(); first_v = rank_value(first[0]); second_v = rank_value(second[0])
    outcome = "Push."; result_tag = "push"
    if view.choice is None: pass
    elif second_v == first_v: outcome = "Equal rank! Push."
    else:
        is_higher = second_v > first_v
        if (is_higher and view.choice == "higher") or ((not is_higher) and view.choice == "lower"):
            outcome = "You win!"; result_tag = "win"; store.add_balance(inter.user.id, bet)
        else:
            outcome = "You lose."; result_tag = "loss"; store.add_balance(inter.user.id, -bet)
    store.add_result(inter.user.id, result_tag)
    emb = discord.Embed(title="♦ High/Low — Result", description=f"Bet: **{bet}**")
    emb.add_field(name="First Card", value=f"{first[0]}{first[1]}", inline=True)
    emb.add_field(name="Second Card", value=f"{second[0]}{second[1]}", inline=True)
    emb.add_field(name="Outcome", value=outcome, inline=False)
    await msg.edit(embed=emb, view=None)

# ---------- Dice Duel ----------
@tree.command(name="diceduel", description="Challenge another player to a quick dice duel (2d6 vs 2d6).")
@app_commands.describe(bet="Optional wager (both must afford it)", opponent="User to challenge")
async def diceduel(inter: discord.Interaction, opponent: discord.User, bet: Optional[app_commands.Range[int, 1, 1_000_000]] = None):
    if opponent.bot or opponent.id == inter.user.id:
        return await inter.response.send_message("Pick a real opponent.", ephemeral=True)
    if bet:
        if store.get_balance(inter.user.id) < bet:
            return await inter.response.send_message("❌ You don't have enough credits for that bet.", ephemeral=True)
        if store.get_balance(opponent.id) < bet:
            return await inter.response.send_message(f"❌ {opponent.mention} doesn't have enough credits.", ephemeral=True)
    view = PvPChallengeView(challenger_id=inter.user.id, challenged_id=opponent.id)
    await inter.response.send_message(f"🎲 {opponent.mention}, **{inter.user.display_name}** challenges you to a Dice Duel{' for **'+str(bet)+'** credits' if bet else ''}! Accept?", view=view)
    await view.wait()
    if view.accepted is None: return await inter.followup.send("⌛ Challenge expired.")
    if not view.accepted: return await inter.followup.send("🚫 Challenge declined.")
    def roll2(): return random.randint(1,6), random.randint(1,6)
    rerolls = 3; history = []
    while True:
        a = roll2(); b = roll2(); sa, sb = sum(a), sum(b); history.append((a, sa, b, sb))
        if sa != sb or rerolls == 0: break
        rerolls -= 1
    if sa > sb:
        outcome = f"**{inter.user.display_name}** wins! ({a[0]}+{a[1]}={sa} vs {b[0]}+{b[1]}={sb})"
        if bet: store.add_balance(inter.user.id, bet); store.add_balance(opponent.id, -bet)
        store.add_result(inter.user.id, "win"); store.add_result(opponent.id, "loss")
    elif sb > sa:
        outcome = f"**{opponent.display_name}** wins! ({b[0]}+{b[1]}={sb} vs {a[0]}+{a[1]}={sa})"
        if bet: store.add_balance(inter.user.id, -bet); store.add_balance(opponent.id, bet)
        store.add_result(opponent.id, "win"); store.add_result(inter.user.id, "loss")
    else:
        outcome = f"Tie after rerolls ({sa}={sb}). It's a push."; store.add_result(opponent.id, "push"); store.add_result(inter.user.id, "push")
    desc_lines = [f"Round {i+1}: 🎲 {h[0][0]}+{h[0][1]}={h[1]}  vs  🎲 {h[2][0]}+{h[2][1]}={h[3]}" for i, h in enumerate(history)]
    emb = discord.Embed(title="🎲 Dice Duel Results", description="\n".join(desc_lines))
    emb.add_field(name="Outcome", value=outcome, inline=False)
    await inter.followup.send(embed=emb)

#----- Coin Flip----
@tree.command(name="coinflip", description="50/50 coin flip. Win 2x on a correct call.")
@app_commands.describe(bet="Amount to wager", choice="heads or tails")
async def coinflip(inter: discord.Interaction, bet: app_commands.Range[int, 1, 1_000_000], choice: str):
    choice = choice.strip().lower()
    if choice not in ("heads", "tails"):
        return await inter.response.send_message("Pick **heads** or **tails**.", ephemeral=True)

    bal = store.get_balance(inter.user.id)  # wallet helpers already exist 
    if bet > bal:
        return await inter.response.send_message("❌ You don't have that many credits.", ephemeral=True)
    store.add_balance(inter.user.id, -bet)        # take bet 

    import random
    result = random.choice(["heads", "tails"])
    if result == choice:
        payout = bet * 2
        store.add_balance(inter.user.id, payout)  # pay out 
        store.add_result(inter.user.id, "win")    # stats W/L/P are tracked 
        msg = f"🪙 It’s **{result}**! You won **{payout - bet}**. Balance: **{store.get_balance(inter.user.id)}**"
    else:
        store.add_result(inter.user.id, "loss")
        msg = f"🪙 It’s **{result}**. You lost **{bet}**. Balance: **{store.get_balance(inter.user.id)}**"

    await inter.response.send_message(msg)
# ============================
#   PASSIVE BUSINESSES SYSTEM
# ============================
# Features:
#  - /businesses           -> list owned businesses, levels, timers, yields
#  - /buy_business         -> dropdown purchase flow with preview + confirm
#  - /collect_business     -> collect payouts (with random events)
#  - /sell_business        -> sell a business for partial refund
#  - /upgrade_business     -> increase business level (higher yield)
#  - /business_catalog     -> list all available businesses with ROI
#  - /business_info        -> detailed panel w/ levels & upgrade math
#  - /business_events      -> show random event table
#
# Storage:
#  - Inventory item: "Business: <name>"
#  - Notes (per business):
#      key f"biz_{name}_ts"  -> last-collect ISO timestamp
#      key f"biz_{name}_lvl" -> int level >= 1
#
# Drop-in: uses your existing `store` helpers.

import random, math
from datetime import datetime, timezone, timedelta

# ---- Catalog (tweak freely) ----
BUSINESSES = {
    "Lemonade Stand":  {"cost": 5_000,     "yield": 500,     "hours": 6},
    "Food Truck":      {"cost": 20_000,    "yield": 2_500,   "hours": 6},
    "Car Wash":        {"cost": 50_000,    "yield": 7_500,   "hours": 8},
    "Mini-Mart":       {"cost": 100_000,   "yield": 15_000,  "hours": 8},
    "Arcade":          {"cost": 250_000,   "yield": 40_000,  "hours": 12},
    "Restaurant":      {"cost": 500_000,   "yield": 90_000,  "hours": 12},
    "Tech Startup":    {"cost": 1_000_000, "yield": 225_000, "hours": 24},
    "Casino":          {"cost": 5_000_000, "yield": 1_000_000, "hours": 24},
}

# Level scaling (applies to 'yield' only). Max level = len(LEVEL_MULTIPLIER)
LEVEL_MULTIPLIER = [1.00, 1.60, 2.20, 3.00, 4.00]  # L1..L5
MAX_LEVEL = len(LEVEL_MULTIPLIER)

# Upgrade cost factor: base_cost * level * UPGRADE_FACTOR
UPGRADE_FACTOR = 0.9

# Sellback baseline: ~50% at level 1, +15% per extra level
def sell_value(base_cost: int, level: int) -> int:
    return int(base_cost * (0.50 + 0.15 * (max(level,1)-1)))

# Random events — applied to THIS collection only
# Each entry: (label, multiplier, probability)
BUSINESS_EVENTS = [
    ("Booming Day 🎉",       1.50, 0.10),
    ("Slow Foot Traffic 🐢", 0.70, 0.12),
    ("Supply Discount 📦",   1.20, 0.10),
    ("Staff Shortage 😵",    0.85, 0.10),
    ("VIP Visit 🌟",         1.30, 0.06),
    ("Ad Flopped 🪫",        0.90, 0.12),
    # implicit default: ("Normal Day", 1.00, remaining probability)
]

# -------- Helpers --------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _note_key_ts(name: str) -> str:  return f"biz_{name}_ts"
def _note_key_lvl(name: str) -> str: return f"biz_{name}_lvl"
def _inv_name(name: str) -> str:     return f"Business: {name}"

def _get_level(uid: int, name: str) -> int:
    s = store.get_note(uid, _note_key_lvl(name))
    try:
        return max(1, min(MAX_LEVEL, int(s)))
    except Exception:
        return 1

def _set_level(uid: int, name: str, lvl: int):
    lvl = max(1, min(MAX_LEVEL, int(lvl)))
    store.set_note(uid, _note_key_lvl(name), str(lvl))

def _get_last_ts(uid: int, name: str) -> datetime | None:
    s = store.get_note(uid, _note_key_ts(name))
    if not s: return None
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def _set_ts(uid: int, name: str, ts: datetime | None):
    store.set_note(uid, _note_key_ts(name), (ts or now_utc()).isoformat())

def _owns(uid: int, name: str) -> bool:
    inv = store.get_inventory(uid) or {}
    return inv.get(_inv_name(name), 0) > 0

def _hours_until_ready(uid: int, name: str) -> float:
    last = _get_last_ts(uid, name)
    if not last: return 0.0
    hrs = BUSINESSES[name]["hours"]
    delta = now_utc() - last
    rem = hrs - (delta.total_seconds() / 3600.0)
    return max(0.0, rem)

def _weighted_event() -> tuple[str, float]:
    roll = random.random()
    acc = 0.0
    for label, mult, p in BUSINESS_EVENTS:
        acc += p
        if roll < acc:
            return (label, mult)
    return ("Normal Day", 1.0)

def _daily_yield(base_yield: int, hours: int, level_mult: float = 1.0) -> int:
    return int((base_yield * level_mult) * (24 / hours))

def _roi_days(cost: int, per_day: int) -> str:
    if per_day <= 0: return "—"
    return f"{cost / per_day:.1f}d"

# -------- Core Commands --------
@tree.command(name="businesses", description="List your businesses, levels, yields, and timers.")
async def businesses(inter: discord.Interaction, user: discord.User | None = None):
    target = user or inter.user
    inv = store.get_inventory(target.id) or {}
    owned = [k.replace("Business: ", "") for k,v in inv.items() if k.startswith("Business: ") and v > 0]
    if not owned:
        return await inter.response.send_message(f"🔎 **{target.display_name}** owns no businesses.")

    lines = []
    for name in owned:
        base = BUSINESSES.get(name)
        if not base:
            lines.append(f"• **{name}** (unknown)")
            continue
        lvl = _get_level(target.id, name)
        mult = LEVEL_MULTIPLIER[lvl-1]
        hourly = base["yield"] * mult / base["hours"]
        hrs_left = _hours_until_ready(target.id, name)
        status = "Ready ✅" if hrs_left == 0 else f"Ready in ~{hrs_left:.1f}h"
        lines.append(
            f"• **{name}** — L{lvl} | yield **{int(base['yield']*mult)}** every **{base['hours']}h** "
            f"(~{int(hourly)}/h) — {status}"
        )

    emb = discord.Embed(title=f"🏢 Businesses — {target.display_name}", description="\n".join(lines))
    await inter.response.send_message(embed=emb)

@tree.command(name="sell_business", description="Sell one of your businesses for a partial refund.")
@app_commands.describe(name="Exact business name (see /businesses)", confirm="Type YES to confirm selling")
async def sell_business(inter: discord.Interaction, name: str, confirm: str = "NO"):
    name = name.strip()
    base = BUSINESSES.get(name)
    if not base or not _owns(inter.user.id, name):
        return await inter.response.send_message("❌ You don't own that.", ephemeral=True)
    lvl = _get_level(inter.user.id, name)
    value = sell_value(base["cost"], lvl)
    if confirm.upper() != "YES":
        return await inter.response.send_message(f"Type `YES` to confirm selling **{name} (L{lvl})** for **{value}**.", ephemeral=True)

    store.remove_item(inter.user.id, _inv_name(name), 1)
    store.add_balance(inter.user.id, value)
    # clear notes
    store.set_note(inter.user.id, _note_key_ts(name), "")
    store.set_note(inter.user.id, _note_key_lvl(name), "")
    await inter.response.send_message(f"💸 Sold **{name} (L{lvl})** for **{value}**.")

@tree.command(name="upgrade_business", description="Upgrade a business to increase its yield.")
@app_commands.describe(name="Exact business name (see /businesses)")
async def upgrade_business(inter: discord.Interaction, name: str):
    name = name.strip()
    if not _owns(inter.user.id, name):
        return await inter.response.send_message("❌ You don't own that business.", ephemeral=True)
    base = BUSINESSES.get(name)
    lvl = _get_level(inter.user.id, name)
    if lvl >= MAX_LEVEL:
        return await inter.response.send_message(f"⚠️ **{name}** is already max level (L{MAX_LEVEL}).", ephemeral=True)

    cost = int(base["cost"] * (lvl) * UPGRADE_FACTOR)
    if store.get_balance(inter.user.id) < cost:
        return await inter.response.send_message(f"Need **{cost}** credits to upgrade to L{lvl+1}.", ephemeral=True)

    store.add_balance(inter.user.id, -cost)
    _set_level(inter.user.id, name, lvl+1)
    new_yield = int(base["yield"] * LEVEL_MULTIPLIER[lvl])  # lvl+1 index
    await inter.response.send_message(
        f"⬆️ Upgraded **{name}** to **L{lvl+1}**. New yield: **{new_yield}** every **{base['hours']}h**."
    )

@tree.command(name="collect_business", description="Collect earnings from all your businesses (random events may apply).")
async def collect_business(inter: discord.Interaction):
    inv = store.get_inventory(inter.user.id) or {}
    owned = [k.replace("Business: ", "") for k,v in inv.items() if k.startswith("Business: ") and v > 0]
    if not owned:
        return await inter.response.send_message("You don't own any businesses.", ephemeral=True)

    total_earned = 0
    lines = []

    for name in owned:
        base = BUSINESSES.get(name)
        if not base:
            continue
        lvl = _get_level(inter.user.id, name)
        mult = LEVEL_MULTIPLIER[lvl-1]
        per_cycle = int(base["yield"] * mult)

        last = _get_last_ts(inter.user.id, name)
        if not last:
            _set_ts(inter.user.id, name, now_utc())
            lines.append(f"• **{name} (L{lvl})** — timer started. Come back later.")
            continue

        hours = base["hours"]
        elapsed_h = (now_utc() - last).total_seconds() / 3600.0
        cycles = int(elapsed_h // hours)

        if cycles <= 0:
            rem = hours - elapsed_h
            lines.append(f"• **{name} (L{lvl})** — not ready. (~{rem:.1f}h left)")
            continue

        label, event_mult = _weighted_event()
        earned = int(per_cycle * cycles * event_mult)
        total_earned += earned
        store.add_balance(inter.user.id, earned)
        _set_ts(inter.user.id, name, last + timedelta(hours=cycles*hours))

        base_amt = per_cycle * cycles
        note_event = "" if event_mult == 1.0 else f" × {event_mult:.2f} **{label}**"
        lines.append(f"• **{name} (L{lvl})** — {cycles}× cycles: {base_amt}{note_event} → **{earned}**")

    if total_earned == 0:
        return await inter.response.send_message("\n".join(lines))

    bal = store.get_balance(inter.user.id)
    emb = discord.Embed(title="💰 Business Collection")
    emb.add_field(name="Collections", value="\n".join(lines), inline=False)
    emb.set_footer(text=f"Total earned: {total_earned} • New balance: {bal}")
    await inter.response.send_message(embed=emb)

# ============================
#   BUY BUSINESS (DROPDOWN)
# ============================

class BuyBusinessView(discord.ui.View):
    def __init__(self, user_id: int, affordable_only: bool = False, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.affordable_only = affordable_only
        self.selected_name: str | None = None
        self.confirm.disabled = True  # disabled until a selection is made

        # Build select options
        bal = store.get_balance(user_id)
        options: list[discord.SelectOption] = []
        for name, cfg in BUSINESSES.items():
            if _owns(user_id, name):  # one of each business in this model
                continue
            if affordable_only and bal < cfg["cost"]:
                continue
            label = name
            desc = f"Cost {cfg['cost']} • {cfg['yield']}/{cfg['hours']}h"
            options.append(discord.SelectOption(label=label, description=desc, value=name))

        if not options:
            options = [discord.SelectOption(label="No businesses available", value="__none__", description=" ")]
            self.select.disabled = True

        self.select.options = options

    async def interaction_check(self, inter: discord.Interaction) -> bool:
        if inter.user.id != self.user_id:
            await inter.response.send_message("This menu isn’t for you.", ephemeral=True)
            return False
        return True

    @discord.ui.select(placeholder="Choose a business to buy…", min_values=1, max_values=1, options=[])
    async def select(self, inter: discord.Interaction, select: discord.ui.Select):
        choice = select.values[0]
        if choice == "__none__":
            return await inter.response.send_message("No businesses available to buy.", ephemeral=True)

        self.selected_name = choice
        cfg = BUSINESSES.get(choice)
        bal = store.get_balance(self.user_id)

        # Enable confirm if they can afford it right now
        can_afford = bal >= cfg["cost"]
        self.confirm.disabled = not can_afford

        per_day = _daily_yield(cfg["yield"], cfg["hours"])
        roi = _roi_days(cfg["cost"], per_day)

        emb = discord.Embed(title=f"🏢 {choice} — Preview")
        emb.add_field(name="Price", value=f"{cfg['cost']}")
        emb.add_field(name="Payout", value=f"{cfg['yield']} every {cfg['hours']}h (~{per_day}/day)")
        emb.add_field(name="Est. ROI", value=roi)
        emb.set_footer(text=f"Your balance: {bal} • {'OK to buy' if can_afford else 'Insufficient funds'}")

        await inter.response.edit_message(embed=emb, view=self)

    @discord.ui.button(label="Buy", style=discord.ButtonStyle.success)
    async def confirm(self, inter: discord.Interaction, button: discord.ui.Button):
        if not self.selected_name:
            return await inter.response.send_message("Pick a business first.", ephemeral=True)

        name = self.selected_name
        cfg = BUSINESSES.get(name)
        if not cfg:
            return await inter.response.send_message("That business no longer exists.", ephemeral=True)

        # Re-validate current state (no race wins)
        if _owns(self.user_id, name):
            return await inter.response.send_message("You already own this business.", ephemeral=True)

        bal = store.get_balance(self.user_id)
        if bal < cfg["cost"]:
            return await inter.response.send_message("You don’t have enough credits anymore.", ephemeral=True)

        # Perform purchase
        store.add_balance(self.user_id, -cfg["cost"])
        store.add_item(self.user_id, _inv_name(name), 1)
        _set_level(self.user_id, name, 1)
        _set_ts(self.user_id, name, now_utc())

        # Finish UI
        self.select.disabled = True
        self.confirm.disabled = True
        try:
            await inter.response.edit_message(
                embed=discord.Embed(
                    title="✅ Purchased",
                    description=(f"You bought **{name} (L1)** for **{cfg['cost']}**.\n"
                                 f"It pays **{cfg['yield']}** every **{cfg['hours']}h**.\n"
                                 f"New balance: **{store.get_balance(self.user_id)}**")
                ),
                view=self
            )
        except Exception:
            await inter.followup.send(
                f"✅ Bought **{name}**. New balance: **{store.get_balance(self.user_id)}**",
                ephemeral=True
            )

@tree.command(name="buy_business", description="Buy a business from a dropdown.")
@app_commands.describe(affordable_only="Show only what you can currently afford")
async def buy_business(inter: discord.Interaction, affordable_only: bool = False):
    view = BuyBusinessView(inter.user.id, affordable_only=affordable_only)
    emb = discord.Embed(
        title="🛒 Buy a Business",
        description=("Select a business below to preview its details, then press **Buy**.\n"
                     f"{'Showing only affordable options.' if affordable_only else 'Showing all you don’t own.'}")
    )
    await inter.response.send_message(embed=emb, view=view, ephemeral=True)

# ============================
#   BUSINESS CATALOG + INFO
# ============================

def _fmt_hours(h: float) -> str:
    if h < 1:
        return f"{int(h*60)}m"
    d, rem = divmod(h, 24)
    if d >= 1:
        return f"{int(d)}d {int(rem)}h"
    return f"{int(rem)}h"

# ---- Autocomplete for business names ----
async def business_name_autocomplete(inter: discord.Interaction, current: str):
    current = (current or "").lower()
    names = [name for name in BUSINESSES.keys() if current in name.lower()]
    return [app_commands.Choice(name=n, value=n) for n in names[:25]]

@tree.command(name="business_catalog", description="View all available businesses and their stats.")
@app_commands.describe(affordable_only="Show only businesses you can afford right now")
async def business_catalog(inter: discord.Interaction, affordable_only: bool = False):
    bal = store.get_balance(inter.user.id)
    rows = []
    for name, cfg in BUSINESSES.items():
        cost, base_y, hrs = cfg["cost"], cfg["yield"], cfg["hours"]
        if affordable_only and bal < cost:
            continue
        per_day = _daily_yield(base_y, hrs, 1.0)
        rows.append((name, cost, base_y, hrs, per_day, _roi_days(cost, per_day)))

    if not rows:
        msg = "Nothing you can afford yet." if affordable_only else "No businesses configured."
        return await inter.response.send_message(msg, ephemeral=True)

    rows.sort(key=lambda r: r[1])  # by cost
    lines = []
    for (name, cost, base_y, hrs, per_day, roi) in rows:
        lines.append(
            f"**{name}** — Cost **{cost}** • Pays **{base_y}** / **{hrs}h** "
            f"(~{per_day}/day) • ROI ~ {roi}"
        )

    emb = discord.Embed(title="🏢 Business Catalog", description="\n".join(lines))
    emb.set_footer(text="Tip: Use /business_info <name> for level math & upgrade costs.")
    await inter.response.send_message(embed=emb)

@tree.command(name="business_info", description="Detailed info for a specific business.")
@app_commands.describe(name="Business name")
@app_commands.autocomplete(name=business_name_autocomplete)
async def business_info(inter: discord.Interaction, name: str):
    cfg = BUSINESSES.get(name)
    if not cfg:
        return await inter.response.send_message("Unknown business.", ephemeral=True)

    cost, base_y, hrs = cfg["cost"], cfg["yield"], cfg["hours"]
    you_own = _owns(inter.user.id, name)
    lvl = _get_level(inter.user.id, name) if you_own else 1
    mult = LEVEL_MULTIPLIER[lvl-1]
    per_cycle = int(base_y * mult)
    per_day  = _daily_yield(base_y, hrs, mult)

    # Next upgrade
    if you_own and lvl < MAX_LEVEL:
        next_cost = int(cost * (lvl) * UPGRADE_FACTOR)
        next_mult = LEVEL_MULTIPLIER[lvl]  # lvl+1 index
        next_cycle = int(base_y * next_mult)
        next_day = _daily_yield(base_y, hrs, next_mult)
        upgrade_line = (
            f"L{lvl} → L{lvl+1}: costs **{next_cost}**, "
            f"pays **{next_cycle}** / {hrs}h (~{next_day}/day)"
        )
    else:
        upgrade_line = "Max level reached." if you_own else "Buy to unlock upgrades."

    # Timer / readiness
    if you_own:
        hrs_left = _hours_until_ready(inter.user.id, name)
        ready_line = "Ready now ✅" if hrs_left == 0 else f"Ready in ~{hrs_left:.1f}h"
        sell_val = sell_value(cost, lvl)
        ownership = f"You own this at **L{lvl}** • Sell value **{sell_val}**"
    else:
        ready_line = "You don't own this yet."
        ownership = "You don't own this."

    emb = discord.Embed(title=f"🏢 {name}")
    emb.add_field(name="Base", value=f"Cost **{cost}** • Pays **{base_y}** / {hrs}h (~{_daily_yield(base_y, hrs)}/day)", inline=False)
    emb.add_field(name="Your Stats" if you_own else "Projected (L1)", 
                  value=f"Level **L{lvl if you_own else 1}** • Pays **{per_cycle}** / {hrs}h (~{per_day}/day)\n{ownership}\n{ready_line}",
                  inline=False)
    emb.add_field(name="Upgrades", value=upgrade_line, inline=False)

    # Level table
    table_lines = []
    for i, m in enumerate(LEVEL_MULTIPLIER, start=1):
        cyc = int(base_y * m)
        perday = _daily_yield(base_y, hrs, m)
        mark = " ← you" if you_own and i == lvl else ""
        table_lines.append(f"L{i}: {cyc} / {hrs}h (~{perday}/day){mark}")
    emb.add_field(name="Level Yields", value="\n".join(table_lines), inline=False)

    await inter.response.send_message(embed=emb)

@tree.command(name="business_events", description="Show possible random events and their effects.")
async def business_events(inter: discord.Interaction):
    lines = [f"• {label}: ×{mult:.2f} • p={p*100:.0f}%" for (label, mult, p) in BUSINESS_EVENTS]
    lines.append("• Normal Day: ×1.00 • remaining probability")
    await inter.response.send_message(
        embed=discord.Embed(title="🎲 Business Random Events", description="\n".join(lines))
    )

# ---------- Connect 4 (AI or PvP, wagerable) ----------
C4_ROWS, C4_COLS = 6, 7
C4_EMPTY, C4_P1, C4_P2 = 0, 1, 2
C4_EMOJI = {C4_EMPTY: "⚪", C4_P1: "🔴", C4_P2: "🟡"}

def c4_new_board():
    return [[C4_EMPTY for _ in range(C4_COLS)] for __ in range(C4_ROWS)]

def c4_legal_moves(board):
    return [c for c in range(C4_COLS) if board[-1][c] == C4_EMPTY]

def c4_drop(board, col, piece):
    for r in range(C4_ROWS):
        if board[r][col] == C4_EMPTY:
            board[r][col] = piece
            return r, col
    return None  # column full

def c4_check_winner(board, piece):
    # Horizontal
    for r in range(C4_ROWS):
        for c in range(C4_COLS-3):
            if all(board[r][c+i] == piece for i in range(4)): return True
    # Vertical
    for c in range(C4_COLS):
        for r in range(C4_ROWS-3):
            if all(board[r+i][c] == piece for i in range(4)): return True
    # Diagonal up-right
    for r in range(C4_ROWS-3):
        for c in range(C4_COLS-3):
            if all(board[r+i][c+i] == piece for i in range(4)): return True
    # Diagonal down-right
    for r in range(3, C4_ROWS):
        for c in range(C4_COLS-3):
            if all(board[r-i][c+i] == piece for i in range(4)): return True
    return False

def c4_full(board):
    return all(board[C4_ROWS-1][c] != C4_EMPTY for c in range(C4_COLS))

def c4_render(board):
    # Show top row first (reverse rows for display)
    lines = []
    for r in range(C4_ROWS-1, -1, -1):
        lines.append("".join(C4_EMOJI[board[r][c]] for c in range(C4_COLS)))
    footer = "1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣"
    return "\n".join(lines) + "\n" + footer

def c4_winning_move(board, piece):
    for c in c4_legal_moves(board):
        # simulate
        temp = [row[:] for row in board]
        c4_drop(temp, c, piece)
        if c4_check_winner(temp, piece):
            return c
    return None

def c4_ai_move(board):
    # 1) If AI can win now, do it
    move = c4_winning_move(board, C4_P2)
    if move is not None:
        return move
    # 2) Block player's immediate win
    move = c4_winning_move(board, C4_P1)
    if move is not None:
        return move
    # 3) Prefer center, then adjacent columns
    order = [3,2,4,1,5,0,6]
    legal = c4_legal_moves(board)
    for c in order:
        if c in legal:
            return c
    # 4) Fallback random
    return random.choice(legal) if legal else None

class Connect4View(discord.ui.View):
    def __init__(self, mode: str, bet: int, starter_id: int, opponent_id: Optional[int] = None, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.mode = mode  # 'ai' or 'pvp'
        self.bet = int(bet)
        self.starter_id = int(starter_id)
        self.opponent_id = int(opponent_id) if opponent_id else None
        self.board = c4_new_board()
        self.turn = C4_P1  # Player 1 (starter) uses 🔴
        self.current_player_id = self.starter_id
        # Build 7 column buttons + resign
        for idx in range(7):
            self.add_item(self._make_col_button(idx))
        self.add_item(self._make_resign_button())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.mode == "ai":
            allowed = interaction.user.id == self.starter_id
        else:
            allowed = interaction.user.id in {self.starter_id, self.opponent_id} and interaction.user.id == self.current_player_id
        if not allowed:
            await interaction.response.send_message("It's not your turn.", ephemeral=True)
            return False
        return True

    def _make_col_button(self, col_idx: int):
        custom_id = f"c4_col:{col_idx}"
        btn = discord.ui.Button(label=str(col_idx+1), style=discord.ButtonStyle.primary, custom_id=custom_id, row=col_idx//4)
        async def cb(inter: discord.Interaction, col=col_idx):
            await self._handle_move(inter, col)
        btn.callback = cb
        return btn

    def _make_resign_button(self):
        btn = discord.ui.Button(label="Resign", style=discord.ButtonStyle.danger, emoji="🏳️", row=1)
        async def cb(inter: discord.Interaction):
            # current player resigns
            if self.mode == "ai":
                # player resigns -> loses bet
                store.add_balance(self.starter_id, -self.bet)
                await self._end(inter, f"🏳️ **You resigned.** You lose **{self.bet}** credits.", disable=True)
            else:
                loser = self.current_player_id
                winner = self.starter_id if loser == self.opponent_id else self.opponent_id
                store.add_balance(loser, -self.bet)
                store.add_balance(winner, self.bet)
                try:
                    if loser == self.starter_id:
                        store.add_result(self.starter_id, "loss"); store.add_result(self.opponent_id, "win")
                    else:
                        store.add_result(self.opponent_id, "loss"); store.add_result(self.starter_id, "win")
                except Exception:
                    pass
                await self._end(inter, f"🏳️ <@{loser}> resigned. **<@{winner}>** wins **{self.bet}** credits.", disable=True)
        btn.callback = cb
        return btn

    async def _handle_move(self, inter: discord.Interaction, col: int):
        # drop for current human
        if self.board[-1][col] != C4_EMPTY:
            await inter.response.send_message("That column is full.", ephemeral=True)
            return
        c4_drop(self.board, col, self.turn)

        # Check human win
        if c4_check_winner(self.board, self.turn):
            if self.mode == "ai":
                store.add_balance(self.starter_id, self.bet)
                store.add_result(self.starter_id, "win")
                await self._end(inter, f"✅ **You win!** +{self.bet} credits.", disable=True)
            else:
                # current player wins PvP
                winner = self.current_player_id
                loser = self.starter_id if winner == self.opponent_id else self.opponent_id
                store.add_balance(winner, self.bet); store.add_balance(loser, -self.bet)
                store.add_result(winner, "win"); store.add_result(loser, "loss")
                await self._end(inter, f"✅ **<@{winner}> wins!** Takes **{self.bet}** from <@{loser}>.", disable=True)
            return

        if c4_full(self.board):
            await self._end(inter, "🤝 Draw! No credits exchanged.", disable=True)
            try:
                if self.mode == "ai":
                    store.add_result(self.starter_id, "push")
                else:
                    store.add_result(self.starter_id, "push"); store.add_result(self.opponent_id, "push")
            except Exception:
                pass
            return

        # Switch turn or do AI move
        if self.mode == "ai":
            # AI move (🟡)
            self.turn = C4_P2
            board_text = c4_render(self.board)
            await inter.response.edit_message(embed=self._embed("Your move…"), view=self)
            # small pause for UX
            await asyncio.sleep(0.6)
            ai_col = c4_ai_move(self.board)
            if ai_col is None:
                await self._end(inter, "🤝 Draw! No credits exchanged.", disable=True)
                return
            c4_drop(self.board, ai_col, C4_P2)
            if c4_check_winner(self.board, C4_P2):
                store.add_balance(self.starter_id, -self.bet)
                store.add_result(self.starter_id, "loss")
                await self._end(inter, f"❌ **AI wins.** You lose **{self.bet}** credits.", disable=True)
                return
            if c4_full(self.board):
                await self._end(inter, "🤝 Draw! No credits exchanged.", disable=True)
                store.add_result(self.starter_id, "push")
                return
            # back to human
            self.turn = C4_P1
            await inter.edit_original_response(embed=self._embed("Your turn"), view=self)
        else:
            # PvP: swap player
            self.turn = C4_P2 if self.turn == C4_P1 else C4_P1
            self.current_player_id = self.starter_id if self.current_player_id == self.opponent_id else self.opponent_id
            await inter.response.edit_message(embed=self._embed(f"Turn: <@{self.current_player_id}>"), view=self)

    def _embed(self, subtitle: str):
        title = "🟨 Connect 4" if self.mode == "ai" else "🟨 Connect 4 — PvP"
        emb = discord.Embed(title=title, description=c4_render(self.board))
        if self.mode == "ai":
            emb.add_field(name="Bet", value=str(self.bet), inline=True)
            emb.add_field(name="You", value="🔴 (goes first)", inline=True)
            emb.add_field(name="AI", value="🟡", inline=True)
        else:
            emb.add_field(name="Bet (each)", value=str(self.bet), inline=True)
            emb.add_field(name="Red", value=f"<@{self.starter_id}>", inline=True)
            emb.add_field(name="Yellow", value=f"<@{self.opponent_id}>", inline=True)
            emb.set_footer(text=f"Turn: {('🔴 ' if self.turn==C4_P1 else '🟡 ')}<@{self.current_player_id}>")
        return emb

    async def _end(self, inter: discord.Interaction, result_text: str, disable: bool = True):
        if disable:
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
        emb = self._embed("Game Over")
        emb.add_field(name="Result", value=result_text, inline=False)
        try:
            if inter.response.is_done():
                await inter.edit_original_response(embed=emb, view=self if not disable else None)
            else:
                await inter.response.edit_message(embed=emb, view=self if not disable else None)
        except Exception:
            pass
        self.stop()

@tree.command(name="connect4", description="Play Connect 4 vs AI or another user (wager your balance).")
@app_commands.describe(bet="Amount to wager", opponent="Omit to play AI; mention a user to play PvP")
async def connect4_cmd(inter: discord.Interaction, bet: app_commands.Range[int, 1, 1_000_000], opponent: Optional[discord.User] = None):
    # Validate balances
    if opponent is None:
        # AI mode
        if store.get_balance(inter.user.id) < bet:
            return await inter.response.send_message("❌ You don't have enough credits for that bet.", ephemeral=True)
        view = Connect4View(mode="ai", bet=int(bet), starter_id=inter.user.id, opponent_id=None)
        await inter.response.send_message(embed=view._embed("Your turn"), view=view)
        return

    # PvP mode validations
    if opponent.bot or opponent.id == inter.user.id:
        return await inter.response.send_message("Pick a real opponent.", ephemeral=True)
    if store.get_balance(inter.user.id) < bet:
        return await inter.response.send_message("❌ You don't have enough credits for that bet.", ephemeral=True)
    if store.get_balance(opponent.id) < bet:
        return await inter.response.send_message(f"❌ {opponent.mention} doesn't have enough credits for that bet.", ephemeral=True)

    # Challenge flow
    challenge = PvPChallengeView(challenger_id=inter.user.id, challenged_id=opponent.id, timeout=PVP_TIMEOUT)
    await inter.response.send_message(f"🟨 {opponent.mention}, **{inter.user.display_name}** challenges you to **Connect 4** for **{bet}** credits each. Accept?", view=challenge)
    await challenge.wait()
    if challenge.accepted is None:
        return await inter.followup.send("⌛ Challenge expired.")
    if not challenge.accepted:
        return await inter.followup.send("🚫 Challenge declined.")

    # Start PvP game
    view = Connect4View(mode="pvp", bet=int(bet), starter_id=inter.user.id, opponent_id=opponent.id)
    await inter.followup.send(embed=view._embed(f"Turn: <@{view.current_player_id}>"), view=view)

# ---------- Slots (animated) ----------
SLOT_SYMBOLS_BASE = ["🍒", "🍋", "🔔", "⭐", "🍀", "7️⃣"]
WILD = "🃏"
SLOT_SYMBOLS = SLOT_SYMBOLS_BASE + [WILD]
SLOT_PAYOUTS = {
    ("7️⃣","7️⃣","7️⃣"): 20,
    ("🍀","🍀","🍀"): 10,
    ("⭐","⭐","⭐"): 6,
    ("🔔","🔔","🔔"): 5,
    ("🍋","🍋","🍋"): 4,
    ("🍒","🍒","🍒"): 3,
    (WILD, WILD, WILD): 25,
}
NUDGE_UPGRADE_CHANCE = 0.10

def _best_triplet_with_wilds(reels: List[str]) -> Optional[Tuple[str, int]]:
    trip = tuple(reels)
    if trip in SLOT_PAYOUTS:
        mult = SLOT_PAYOUTS[trip]; label = "Jackpot" if mult >= 20 else "Win"; return (f"{label} x{mult}!", mult)
    for sym in ["7️⃣","🍀","⭐","🔔","🍋","🍒"]:
        match = sum(1 for r in reels if r == sym or r == WILD)
        if match == 3:
            mult = SLOT_PAYOUTS[(sym, sym, sym)]; label = "Jackpot" if mult >= 20 else "Win"; return (f"{label} x{mult}!", mult)
    for i in range(3):
        for j in range(i+1, 3):
            if reels[i] == reels[j] or WILD in (reels[i], reels[j]):
                return ("Nice! Two of a kind x2.", 2)
    return None

class SpinAgainView(discord.ui.View):
    def __init__(self, uid: int, bet: int, timeout: float = 30):
        super().__init__(timeout=timeout); self.uid = uid; self.bet = bet
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.uid: await interaction.response.send_message("Only the original spinner can use this.", ephemeral=True); return False
        return True
    @discord.ui.button(label="Spin Again", style=discord.ButtonStyle.primary, emoji="🔁")
    async def spin_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(); await slots_internal(interaction, self.bet)

async def slots_internal(inter_or_ctx: discord.Interaction, bet: int):
    if store.get_balance(inter_or_ctx.user.id) < bet:
        return await inter_or_ctx.followup.send("❌ Not enough credits for that bet.", ephemeral=True)
    final_reels = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
    spinning = ["⬜", "⬜", "⬜"]
    emb = discord.Embed(title="🎰 Slot Machine")
    emb.add_field(name="Spin", value=" | ".join(spinning), inline=False)
    emb.add_field(name="Bet", value=str(bet), inline=True); emb.set_footer(text="Spinning...")
    try:
        msg = await inter_or_ctx.original_response(); await msg.edit(embed=emb)
    except discord.NotFound:
        await inter_or_ctx.response.send_message(embed=emb); msg = await inter_or_ctx.original_response()
    reels = spinning[:]; stops = [12,16,20]
    for t in range(max(stops)):
        for i in range(3):
            if t < stops[i]-1: reels[i] = random.choice(SLOT_SYMBOLS)
            elif t == stops[i]-1: reels[i] = final_reels[i]
        try:
            anim = discord.Embed(title="🎰 Slot Machine")
            anim.add_field(name="Spin", value=" | ".join(reels), inline=False)
            anim.add_field(name="Bet", value=str(bet), inline=True)
            anim.set_footer(text="Spinning..." if t < max(stops)-1 else "Result"); await msg.edit(embed=anim)
        except discord.HTTPException: pass
        await asyncio.sleep(0.18)
    best = _best_triplet_with_wilds(final_reels)
    if best is None:
        pair = any(final_reels[i] == final_reels[j] or WILD in (final_reels[i], final_reels[j]) for i in range(3) for j in range(i+1,3))
        if pair and random.random() < NUDGE_UPGRADE_CHANCE:
            target_sym = "7️⃣"
            for sym in ["7️⃣","🍀","⭐","🔔","🍋","🍒"]:
                c = sum(1 for r in final_reels if r == sym or r == WILD)
                if c >= 2: target_sym = sym; break
            for i in range(3):
                if final_reels[i] != target_sym and final_reels[i] != WILD:
                    final_reels[i] = target_sym; break
            best = _best_triplet_with_wilds(final_reels)
    if best is None:
        result_text, mult = ("You lose.", 0)
    else:
        result_text, mult = best
    if mult == 0: store.add_balance(inter_or_ctx.user.id, -bet); net = -bet
    else:
        win_amount = bet * mult; store.add_balance(inter_or_ctx.user.id, win_amount - bet); net = win_amount - bet
    final = discord.Embed(title="🎰 Slot Machine — Result")
    final.add_field(name="Spin", value=" | ".join(final_reels), inline=False)
    final.add_field(name="Bet", value=str(bet), inline=True)
    final.add_field(name="Result", value=result_text, inline=True)
    final.add_field(name="Net", value=(f"+{net}" if net >= 0 else str(net)), inline=True)
    view = SpinAgainView(uid=inter_or_ctx.user.id, bet=bet)
    try: await msg.edit(embed=final, view=view)
    except discord.HTTPException: await inter_or_ctx.followup.send(embed=final, view=view)

@tree.command(name="slots", description="Spin the slot machine. Matching symbols (with 🃏 wilds) pay big!")
@app_commands.describe(bet="Amount to wager")
async def slots(inter: discord.Interaction, bet: app_commands.Range[int, 1, 1_000_000]):
    await inter.response.defer(); await slots_internal(inter, bet)

# ---------- Moderation (restricted to admins/allowlist) ----------
@tree.command(name="purge", description="Bulk delete recent messages (max 1000).")
@app_commands.describe(limit="Number of recent messages to scan (1-1000)", user="Only delete messages by this user")
@require_admin_or_allowlisted()
async def purge(inter: discord.Interaction, limit: app_commands.Range[int, 1, 1000], user: Optional[discord.User] = None):
    if not isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
        return await inter.response.send_message("This command can only be used in text channels.", ephemeral=True)
    def check(m: discord.Message):
        if getattr(m, 'pinned', False):
            return False
        return (user is None) or (m.author.id == user.id)
    await inter.response.defer(ephemeral=True)
    try:
        deleted = await inter.channel.purge(limit=limit, check=check, bulk=True)
        await inter.followup.send(f"🧹 Deleted **{len(deleted)}** messages.", ephemeral=True)
    except discord.Forbidden:
        await inter.followup.send("I need the **Manage Messages** and **Read Message History** permissions.", ephemeral=True)
    except discord.HTTPException as e:
        await inter.followup.send(f"Error while deleting: {e}", ephemeral=True)

@tree.command(name="autodelete_set", description="Enable auto-delete for this channel after N seconds or minutes.")
@app_commands.describe(duration="Delete after this many seconds (e.g., 10s) or minutes (e.g., 2m or just 2)")
@require_admin_or_allowlisted()
async def autodelete_set(inter: discord.Interaction, duration: str):
    if not isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
        return await inter.response.send_message("Use this in a text channel.", ephemeral=True)

    s = duration.strip().lower()
    # Parse duration -> seconds
    if s.endswith("s"):
        try:
            seconds = int(s[:-1])
        except ValueError:
            return await inter.response.send_message("Invalid seconds format. Try like **10s**.", ephemeral=True)
    elif s.endswith("m"):
        try:
            seconds = int(s[:-1]) * 60
        except ValueError:
            return await inter.response.send_message("Invalid minutes format. Try like **2m**.", ephemeral=True)
    else:
        try:
            seconds = int(s) * 60  # default to minutes if no unit
        except ValueError:
            return await inter.response.send_message("Invalid format. Use **10s**, **2m**, or a number for minutes.", ephemeral=True)

    # Validate range
    if seconds < 5 or seconds > 86400:
        return await inter.response.send_message("Range must be **5 seconds** to **24 hours**.", ephemeral=True)

    store.set_autodelete(inter.channel.id, int(seconds))

    # Nice confirmation text
    if seconds < 60:
        time_str = f"{seconds} seconds"
    elif seconds % 3600 == 0:
        time_str = f"{seconds // 3600} hours"
    elif seconds % 60 == 0:
        time_str = f"{seconds // 60} minutes"
    else:
        time_str = f"{seconds // 60} minutes {seconds % 60} seconds"

    await inter.response.send_message(f"🗑️ Auto-delete enabled: older than **{time_str}**.", ephemeral=True)
@tree.command(name="autodelete_disable", description="Disable auto-delete for this channel.")
@require_admin_or_allowlisted()
async def autodelete_disable(inter: discord.Interaction):
    if not isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
        return await inter.response.send_message("Use this in a text channel.", ephemeral=True)
    store.remove_autodelete(inter.channel.id); await inter.response.send_message("🛑 Auto-delete disabled for this channel.", ephemeral=True)

@tree.command(name="autodelete_status", description="Show auto-delete settings for this channel.")
async def autodelete_status(inter: discord.Interaction):
    if not isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
        return await inter.response.send_message("Use this in a text channel.", ephemeral=True)
    conf = store.get_autodelete(); secs = conf.get(str(inter.channel.id))
    if secs:
        try:
            secs = int(secs)
        except Exception:
            secs = int(float(secs))
        if secs < 60:
            time_str = f"{secs} seconds"
        elif secs % 3600 == 0:
            time_str = f"{secs // 3600} hours"
        elif secs % 60 == 0:
            time_str = f"{secs // 60} minutes"
        else:
            time_str = f"{secs // 60} minutes {secs % 60} seconds"
        await inter.response.send_message(f"✅ Auto-delete is **ON**: older than **{time_str}**.", ephemeral=True)
    else:
        await inter.response.send_message("❌ Auto-delete is **OFF** for this channel.", ephemeral=True)

@tree.command(name="autodelete_list", description="List all channels with auto-delete enabled (clean view).")
@require_admin_or_allowlisted()
async def autodelete_list(inter: discord.Interaction):
    conf = store.get_autodelete()
    if not conf:
        return await inter.response.send_message("No auto-delete rules are set.", ephemeral=True)

    # Normalize into list of (channel_object_or_none, channel_id_str, seconds)
    rows = []
    for chan_id, secs in conf.items():
        try:
            secs = int(secs)
        except Exception:
            secs = int(float(secs))
        try:
            channel = await bot.fetch_channel(int(chan_id))
        except Exception:
            channel = None
        rows.append((channel, str(chan_id), int(secs)))

    # Human formatter
    def _fmt_secs(secs: int) -> str:
        if secs < 60:
            return f"{secs} seconds"
        if secs % 3600 == 0:
            return f"{secs // 3600} hours"
        if secs % 60 == 0:
            return f"{secs // 60} minutes"
        return f"{secs // 60} minutes {secs % 60} seconds"

    instant = [r for r in rows if r[2] < 60]
    scheduled = [r for r in rows if r[2] >= 60]

    # Sort by channel name then ID
    def _key(row):
        ch, cid, _ = row
        name = getattr(ch, "name", None)
        return (name or cid).lower()

    instant.sort(key=_key)
    scheduled.sort(key=_key)

    emb = discord.Embed(title="🗑️ Auto-Delete Rules")
    emb.set_footer(text="Only visible to you")

    if instant:
        lines = []
        for ch, cid, secs in instant:
            label = f"#{ch.name}" if getattr(ch, "name", None) else f"<#{cid}>"
            lines.append(f"{label} — {_fmt_secs(secs)}")
        emb.add_field(name="Instant (< 60s)", value="\n".join(lines)[:1024], inline=False)

    if scheduled:
        lines = []
        for ch, cid, secs in scheduled:
            label = f"#{ch.name}" if getattr(ch, "name", None) else f"<#{cid}>"
            lines.append(f"{label} — {_fmt_secs(secs)}")
        # Discord field limit handling (split if long)
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 1 > 1024:
                emb.add_field(name="Scheduled (≥ 60s)", value=chunk.rstrip(), inline=False)
                chunk = ""
            chunk += line + "\n"
        if chunk:
            emb.add_field(name="Scheduled (≥ 60s)", value=chunk.rstrip(), inline=False)

    total = len(rows)
    emb.description = f"**{total}** channel{'s' if total != 1 else ''} with auto-delete enabled."

    await inter.response.send_message(embed=emb, ephemeral=True)

# ---------- Leaderboard & Achievements ----------
@tree.command(name="leaderboard", description="Show the top players.")
@app_commands.describe(category="Choose 'balance' or 'wins'")
@app_commands.choices(category=[app_commands.Choice(name="balance", value="balance"), app_commands.Choice(name="wins", value="wins")])
async def leaderboard(inter: discord.Interaction, category: app_commands.Choice[str]):
    top = store.list_top(category.value, 10)
    if not top: return await inter.response.send_message("No data yet.")
    lines = []
    for i, (uid, val) in enumerate(top, start=1):
        try:
            user = await bot.fetch_user(uid); uname = user.display_name
        except Exception:
            uname = f"User {uid}"
        lines.append(f"**{i}. {uname}** — {val} {'credits' if category.value=='balance' else 'wins'}")
    emb = discord.Embed(title=f"🏆 Leaderboard — {category.value.capitalize()}", description="\n".join(lines))
    await inter.response.send_message(embed=emb)

@tree.command(name="achievements", description="Show your achievements (or another user's).")
async def achievements(inter: discord.Interaction, user: Optional[discord.User] = None):
    target = user or inter.user; ach = store.get_achievements(target.id)
    emb = discord.Embed(title=f"🏆 Achievements — {target.display_name}")
    if not ach: emb.description = "None yet — go win some games!"
    else: emb.description = ", ".join(sorted(ach))
    stats = store.get_stats(target.id)
    emb.add_field(name="Record", value=f"{stats.get('wins',0)}W / {stats.get('losses',0)}L / {stats.get('pushes',0)}P", inline=False)
    await inter.response.send_message(embed=emb)

# ---------- Background Cleaner ----------
@tasks.loop(minutes=2)
async def cleanup_loop():
    conf = store.get_autodelete()
    if not conf: return
    now = datetime.now(timezone.utc)
    for chan_id, secs in list(conf.items()):
        channel = bot.get_channel(int(chan_id))
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            continue
        try:
            secs = int(secs)
        except Exception:
            secs = int(float(secs))
        # Skip short TTLs (<60s); those are handled by per-message deletes.
        if secs < 60:
            continue
        cutoff = now - timedelta(seconds=int(secs))
        try:
            await channel.purge(
                limit=1000,
                check=lambda m: (not getattr(m, "pinned", False)) and (m.created_at < cutoff),
                bulk=True,
            )
        except (discord.Forbidden, discord.HTTPException):
            continue

@cleanup_loop.before_loop
async def before_cleanup():
    await bot.wait_until_ready()

# ---------- Reminders ----------
def _chicago_tz_for(dt_naive: datetime):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(DEFAULT_TZ_NAME)
        except Exception:
            pass
    y = dt_naive.year
    march8 = datetime(y, 3, 8)
    second_sun_march = march8 + timedelta(days=(6 - march8.weekday()) % 7)
    nov1 = datetime(y, 11, 1)
    first_sun_nov = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    is_dst = second_sun_march <= dt_naive < first_sun_nov
    return timezone(timedelta(hours=-5 if is_dst else -6))

def _parse_date(date_str: str):
    s = date_str.strip()
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", s)
    if not m:
        m = re.match(r"^(\d{2})(\d{2})(\d{4})$", s)
    if not m:
        raise ValueError("Date must be MM-DD-YYYY (also accepts MM/DD/YYYY or MMDDYYYY).")
    mm, dd, yyyy = map(int, m.groups())
    return yyyy, mm, dd

def _parse_time(time_str: str):
    t = time_str.strip().lower().replace(" ", "")
    m = re.match(r"^(\d{1,2}):(\d{2})(am|pm)?$", t) or re.match(r"^(\d{2})(\d{2})(am|pm)?$", t)
    if not m:
        raise ValueError("Time must be HH:MM (24h), HHMM, or h:mma/pm.")
    hh, mi, ampm = m.groups()
    hh, mi = int(hh), int(mi)
    if ampm:
        hh = (hh % 12) + (12 if ampm == "pm" else 0)
    if not (0 <= hh <= 23 and 0 <= mi <= 59):
        raise ValueError("Invalid time.")
    return hh, mi

def _parse_offset(s: Optional[str]) -> Optional[timezone]:
    if not s: return None
    off = re.match(r"^([+-])(\d{1,2}):?(\d{2})$", s.strip())
    if not off: raise ValueError("Timezone offset must look like -05:00 or +0530.")
    sign, oh, om = off.groups()
    delta = timedelta(hours=int(oh), minutes=int(om))
    if sign == "-": delta = -delta
    return timezone(delta)

@tree.command(name="remind_in", description="Remind yourself in N minutes.")
@app_commands.describe(minutes="How many minutes from now", message="What to remind you about", dm="Deliver via DM")
async def remind_in(inter: discord.Interaction, minutes: app_commands.Range[int, 1, 60*24*30], message: str, dm: bool = False):
    await inter.response.defer(ephemeral=True)
    due_utc = datetime.now(timezone.utc) + timedelta(minutes=int(minutes))
    rid = store.add_reminder({
        "user_id": inter.user.id,
        "channel_id": None if dm else inter.channel.id,
        "dm": bool(dm),
        "text": message,
        "due_utc": due_utc.isoformat(),
    })
    await inter.followup.send(f"⏰ Reminder **#{rid}** set for in **{minutes}m** — _{message}_", ephemeral=True)

@tree.command(name="remind_at", description="Schedule a reminder at a specific date/time.")
@app_commands.describe(date="MM-DD-YYYY (also OK: MM/DD/YYYY or MMDDYYYY)", time="HH:MM 24h, HHMM, or h:mma/pm", message="What should I remind you about?", tz_offset="Optional ±HH:MM (defaults to America/Chicago)", dm="Send via DM instead of the channel")
async def remind_at(inter: discord.Interaction, date: str, time: str, message: str, tz_offset: Optional[str] = None, dm: bool = False):
    await inter.response.defer(ephemeral=True)
    try:
        yyyy, mm, dd = _parse_date(date)
        hh, mi = _parse_time(time)
        tz = _parse_offset(tz_offset) or _chicago_tz_for(datetime(yyyy, mm, dd))
        local_dt = datetime(yyyy, mm, dd, hh, mi, tzinfo=tz)
        due_utc = local_dt.astimezone(timezone.utc)
        if due_utc <= datetime.now(timezone.utc) + timedelta(seconds=5):
            return await inter.followup.send("That time is in the past. Pick something in the future.", ephemeral=True)
        rid = store.add_reminder({
            "user_id": inter.user.id,
            "channel_id": None if dm else inter.channel.id,
            "dm": bool(dm),
            "text": message,
            "due_utc": due_utc.isoformat(),
        })
        when_text = local_dt.strftime("%m-%d-%Y %H:%M %Z")
        await inter.followup.send(f"⏰ Reminder **#{rid}** set for **{when_text}** — _{message}_", ephemeral=True)
    except Exception as e:
        await inter.followup.send(f"⚠️ {type(e).__name__}: {e}", ephemeral=True)

@tree.command(name="reminders", description="List your pending reminders.")
async def reminders_cmd(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True)
    now_ct = datetime.now(timezone.utc)
    items = store.list_reminders(inter.user.id)
    if not items:
        return await inter.followup.send("No pending reminders.", ephemeral=True)
    lines = []
    for r in items:
        due = datetime.fromisoformat(r["due_utc"]).replace(tzinfo=timezone.utc)
        remaining = int((due - now_ct).total_seconds())
        if remaining < 0: remaining = 0
        local = due.astimezone(_chicago_tz_for(datetime.now()))
        lines.append(f"**#{r.get('id','?')}** — {local.strftime('%m-%d-%Y %H:%M %Z')}  -  in ~{remaining//3600}h {(remaining%3600)//60}m — _{r['text']}_")
    await inter.followup.send("\n".join(lines), ephemeral=True)

@tree.command(name="remind_cancel", description="Cancel a reminder by id.")
@app_commands.describe(reminder_id="The #id from /reminders")
async def remind_cancel(inter: discord.Interaction, reminder_id: int):
    await inter.response.defer(ephemeral=True)
    is_mod = False
    try:
        if isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
            is_mod = inter.channel.permissions_for(inter.user).manage_messages
    except Exception:
        pass
    ok = store.cancel_reminder(reminder_id, requester_id=inter.user.id, is_mod=is_mod)
    await inter.followup.send("Canceled." if ok else "Couldn't cancel.", ephemeral=True)

@tasks.loop(seconds=REMINDER_TICK_SECONDS)
async def reminders_scheduler():
    try:
        now = datetime.now(timezone.utc)
        items = store.list_reminders(None)
        for r in items:
            due = datetime.fromisoformat(r["due_utc"]).replace(tzinfo=timezone.utc)
            if due <= now:
                try:
                    user = await bot.fetch_user(int(r["user_id"]))
                    text = f"⏰ Reminder: {r['text']}"
                    if r.get("dm") or not r.get("channel_id"):
                        await user.send(text)
                    else:
                        chan = bot.get_channel(int(r["channel_id"]))
                        if chan:
                            await chan.send(f"{user.mention} {text}")
                        else:
                            await user.send(text)
                except Exception:
                    pass
                store.cancel_reminder(int(r.get("id", 0)), requester_id=int(r.get("user_id")), is_mod=True)
    except Exception:
        pass

# ---------- Per-message auto-delete for short TTL channels (<60s) ----------
async def _schedule_autodelete(message: discord.Message, seconds: int):
    try:
        await asyncio.sleep(max(1, int(seconds)))
        # Re-fetch before delete to ensure we respect pin status or late edits
        try:
            msg = await message.channel.fetch_message(message.id)
        except Exception:
            return
        if getattr(msg, "pinned", False):
            return  # never delete pins
        await msg.delete()
    except (discord.Forbidden, discord.HTTPException):
        # Lack of perms or already deleted — ignore
        pass
    except Exception:
        pass

@bot.event
async def on_message(message: discord.Message):
    # Skip system messages and if we can't determine a channel
    if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
        return
    # Don't act if the bot lacks perms here
    try:
        perms = message.channel.permissions_for(message.guild.me) if message.guild else None
        if not perms or not perms.manage_messages:
            return
    except Exception:
        pass
    try:
        conf = store.get_autodelete()
        secs = conf.get(str(message.channel.id))
        if not secs:
            return
        try:
            secs = int(secs)
        except Exception:
            secs = int(float(secs))
        if secs < 60:
            # Schedule a per-message delete; we will re-check pin before deletion
            asyncio.create_task(_schedule_autodelete(message, secs))
    except Exception:
        pass

@reminders_scheduler.before_loop
async def before_reminders():
    await bot.wait_until_ready()

# ---------- Admin Allowlist Commands (real admins only) ----------
@tree.command(name="admin_allow", description="Allow a user to use admin bot commands.")
@require_real_admin()
@app_commands.describe(user="User to allow")
async def admin_allow(inter: discord.Interaction, user: discord.User):
    added = store.add_allowlisted(user.id)
    if added:
        await inter.response.send_message(f"✅ **{user.display_name}** added to the admin allowlist.", ephemeral=True)
    else:
        await inter.response.send_message(f"ℹ️ **{user.display_name}** is already on the admin allowlist.", ephemeral=True)

@tree.command(name="admin_revoke", description="Remove a user from the admin bot allowlist.")
@require_real_admin()
@app_commands.describe(user="User to remove")
async def admin_revoke(inter: discord.Interaction, user: discord.User):
    removed = store.remove_allowlisted(user.id)
    if removed:
        await inter.response.send_message(f"✅ **{user.display_name}** removed from the admin allowlist.", ephemeral=True)
    else:
        await inter.response.send_message(f"ℹ️ **{user.display_name}** was not on the admin allowlist.", ephemeral=True)

@tree.command(name="admin_list", description="Show users allowed to use admin bot commands.")
@require_real_admin()
async def admin_list(inter: discord.Interaction):
    ids = store.list_allowlisted()
    if not ids:
        return await inter.response.send_message("Allowlist is empty.", ephemeral=True)
    lines = []
    for uid in ids:
        try:
            u = await bot.fetch_user(uid)
            lines.append(f"- {u.mention} ({u.display_name})")
        except Exception:
            lines.append(f"- <@{uid}> (User {uid})")
    await inter.response.send_message("**Admin allowlist:**\n" + "\n".join(lines), ephemeral=True)


# ---------- Debug / Health ----------
@tree.command(name="debug_store", description="Show backend status and table counts.")
async def debug_store(inter: discord.Interaction):
    stats = store.get_backend_stats()
    emb = discord.Embed(title="🧪 Store Health")
    emb.add_field(name="Backend", value=stats["backend"], inline=True)
    emb.add_field(name="DB Path", value=stats["db_path"], inline=False)
    counts = stats["counts"]
    # Show a compact summary
    groups = [
        ("Economy", ["wallets","inventory","stats","streaks","daily","work","achievements"]),
        ("Weather", ["weather_zips","weather_subs"]),
        ("Reminders", ["reminders"]),
        ("Moderation", ["autodelete","pins","notes"]),
        ("Polls", ["polls"]),
        ("Admin", ["admin_allowlist"]),
    ]
    for title, keys in groups:
        val = ", ".join(f"{k}:{counts.get(k,0)}" for k in keys)
        if val:
            emb.add_field(name=title, value=val, inline=False)
    await inter.response.send_message(embed=emb, ephemeral=True)


# ---------- Startup ----------
@bot.event
async def on_ready():
    # Sync slash commands
    if GUILD_IDS:
        for gid in GUILD_IDS:
            guild = discord.Object(id=gid)
            await tree.sync(guild=guild)
    else:
        await tree.sync()

    if not cleanup_loop.is_running():
        cleanup_loop.start()
    if not reminders_scheduler.is_running():
        reminders_scheduler.start()
    if not weather_scheduler.is_running():
        weather_scheduler.start()

    # Re-register persistent poll views
    for mid, p in store.list_open_polls():
        try:
            view = PollView(message_id=mid, options=[o["label"] for o in p["options"]], creator_id=p.get("creator_id", 0), timeout=None)
            bot.add_view(view)
        except Exception:
            pass

    print(f"Logged in as {bot.user} ({bot.user.id})")

# ---------- Main ----------
def main():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN not set")
    bot.run(token)
# ========================= NEW GAMES: Roulette & Texas Hold'em =========================

# ---------- Roulette ----------
ROULETTE_RED = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
ROULETTE_BLACK = {2,4,6,8,10,11,13,15,17,20,22,24,26,28,29,31,33,35}

def _roulette_spin_sequence(final_number: int):
    # Generate a simple animation sequence that "spins" towards final_number
    import itertools
    wheel = [0,32,15,19,4,21,2,25,17,34,6,27,13,36,11,30,8,23,10,5,24,16,33,1,20,14,31,9,22,18,29,7,28,12,35,3,26]
    # Find index of final_number and build a path that ends there
    try:
        idx = wheel.index(final_number)
    except ValueError:
        idx = 0
    seq = []
    # Spin a couple of full rotations then slow down to the final slot
    path = list(itertools.islice(itertools.cycle(wheel), idx, idx + len(wheel)*2 + 12))
    # ensure it ends exactly on final_number
    path.append(final_number)
    return path

def _roulette_color(n: int) -> str:
    if n == 0:
        return "🟢 Green"
    if n in ROULETTE_RED:
        return "🔴 Red"
    if n in ROULETTE_BLACK:
        return "⚫ Black"
    return "Unknown"

def _roulette_win_multiplier(choice: str, result: int) -> int:
    c = choice.strip().lower()
    if c.isdigit():
        return 36 if int(c) == result else 0  # 35:1 net -> 36x return vs bet accounting like slots; we'll credit net later
    if c in {"red","r"}:
        return 2 if (result in ROULETTE_RED) else 0
    if c in {"black","b"}:
        return 2 if (result in ROULETTE_BLACK) else 0
    if c in {"odd","o"}:
        return 2 if (result != 0 and (result % 2 == 1)) else 0
    if c in {"even","e"}:
        return 2 if (result != 0 and (result % 2 == 0)) else 0
    return -1  # invalid

@tree.command(name="roulette", description="Spin the roulette wheel (red/black/odd/even or a single number 0-36).")
@app_commands.describe(bet="Amount to wager", choice="red/black/odd/even or 0-36")
async def roulette_cmd(inter: discord.Interaction, bet: app_commands.Range[int, 1, 1_000_000], choice: str):
    choice_norm = choice.strip().lower()
    if choice_norm.isdigit():
        if not (0 <= int(choice_norm) <= 36):
            return await inter.response.send_message("Number must be between 0 and 36.", ephemeral=True)
    elif choice_norm not in {"red","r","black","b","odd","o","even","e"}:
        return await inter.response.send_message("Choice must be red/black/odd/even or a number 0-36.", ephemeral=True)

    if store.get_balance(inter.user.id) < bet:
        return await inter.response.send_message("❌ Not enough credits for that bet.", ephemeral=True)

    # Determine the result first for consistency
    result = random.randint(0, 36)
    path = _roulette_spin_sequence(result)

    emb = discord.Embed(title="🎡 Roulette", description="Spinning the wheel...", colour=discord.Colour.dark_teal())
    emb.add_field(name="Bet", value=str(bet), inline=True)
    emb.add_field(name="Your Pick", value=choice_norm.upper(), inline=True)
    emb.add_field(name="Result", value="—", inline=False)
    await inter.response.send_message(embed=emb)
    msg = await inter.original_response()

    # Animate the spin
    for i, n in enumerate(path):
        try:
            anim = discord.Embed(title="🎡 Roulette", description=("Spinning..." if i < len(path)-1 else "Result"), colour=discord.Colour.dark_teal())
            anim.add_field(name="Bet", value=str(bet), inline=True)
            anim.add_field(name="Your Pick", value=choice_norm.upper(), inline=True)
            anim.add_field(name="Wheel", value=f"➡️ **{n}** ({_roulette_color(n)})", inline=False)
            await msg.edit(embed=anim)
        except discord.HTTPException:
            pass
        await asyncio.sleep(0.12 if i < len(path)-10 else 0.22)

    mult = _roulette_win_multiplier(choice_norm, result)
    if mult == -1:
        return await inter.followup.send("Invalid bet choice.", ephemeral=True)

    if mult == 0:
        store.add_balance(inter.user.id, -bet)
        net = -bet
        text = "❌ You lose."
    else:
        # mult represents total return factor; net win = bet * (mult - 1)
        net = bet * (mult - 1)
        store.add_balance(inter.user.id, net)
        text = f"✅ You win! Payout x{mult}"
        store.add_result(inter.user.id, "win")

    final = discord.Embed(title="🎡 Roulette — Final", colour=discord.Colour.green() if net>0 else discord.Colour.red())
    final.add_field(name="Result", value=f"**{result}** ({_roulette_color(result)})", inline=True)
    final.add_field(name="Net", value=(f"+{net}" if net >= 0 else str(net)), inline=True)
    await msg.edit(embed=final)

# ---------- Texas Hold'em (Heads-up, fixed-stake, animated reveal) ----------

POKER_RANK_ORDER = {"2":2,"3":3,"4":4,"5":5,"6":6,"7":7,"8":8,"9":9,"10":10,"J":11,"Q":12,"K":13,"A":14}

def _poker_rank_value(r: str) -> int:
    return POKER_RANK_ORDER.get(r, 0)

def _is_straight(vals: list) -> int:
    """Return high card of straight or 0 if none. vals should be sorted unique ascending"""
    if not vals: return 0
    # wheel A-2-3-4-5
    if set([14,5,4,3,2]).issubset(set(vals)):
        return 5
    streak = 1; best = 0
    for i in range(1, len(vals)):
        if vals[i] == vals[i-1] + 1:
            streak += 1
            if streak >= 5:
                best = vals[i]
        elif vals[i] != vals[i-1]:
            streak = 1
    return best

def _evaluate_best_5(cards: list) -> tuple:
    """
    cards: list of (rank, suit). Returns comparable tuple:
    (category, tiebreaker list...) higher better.
    Categories: 8=Straight Flush,7=Four,6=Full House,5=Flush,4=Straight,3=Trips,2=TwoPair,1=Pair,0=High
    """
    ranks = [r for r,s in cards]
    suits = [s for r,s in cards]
    vals = sorted([_poker_rank_value(r) for r in ranks], reverse=True)

    # counts
    from collections import Counter
    rc = Counter(ranks)
    sc = Counter(suits)

    # Flush?
    flush_suit = None
    for s, c in sc.items():
        if c >= 5:
            flush_suit = s
            break

    # Straight & Straight Flush
    unique_vals = sorted(set(vals))
    straight_high = _is_straight(unique_vals)
    if flush_suit:
        flush_vals = sorted([_poker_rank_value(r) for r,s in cards if s == flush_suit])
        flush_unique = sorted(set(flush_vals))
        sf_high = _is_straight(flush_unique)
        if sf_high:
            return (8, sf_high)

    # Four of a kind
    fours = [r for r,c in rc.items() if c == 4]
    if fours:
        fourv = max(_poker_rank_value(r) for r in fours)
        kickers = sorted([_poker_rank_value(r) for r,c in rc.items() if r not in fours], reverse=True)
        return (7, fourv, kickers[0])

    # Full house
    trips = sorted([_poker_rank_value(r) for r,c in rc.items() if c == 3], reverse=True)
    pairs = sorted([_poker_rank_value(r) for r,c in rc.items() if c == 2], reverse=True)
    if trips and (pairs or len(trips) >= 2):
        t = trips[0]
        p = pairs[0] if pairs else trips[1]
        return (6, t, p)

    # Flush
    if flush_suit:
        top5 = sorted([_poker_rank_value(r) for r,s in cards if s == flush_suit], reverse=True)[:5]
        return (5, *top5)

    # Straight
    if straight_high:
        return (4, straight_high)

    # Trips
    if trips:
        t = trips[0]
        kickers = sorted([_poker_rank_value(r) for r,c in rc.items() if c == 1], reverse=True)[:2]
        return (3, t, *kickers)

    # Two Pair
    if len(pairs) >= 2:
        p1, p2 = pairs[:2]
        kicker = max([_poker_rank_value(r) for r,c in rc.items() if c == 1])
        return (2, p1, p2, kicker)

    # One Pair
    if len(pairs) == 1:
        p = pairs[0]
        kickers = sorted([_poker_rank_value(r) for r,c in rc.items() if c == 1], reverse=True)[:3]
        return (1, p, *kickers)

    # High card
    top5 = sorted([_poker_rank_value(r) for r in ranks], reverse=True)[:5]
    return (0, *top5)

def _best_of_seven(hole: list, board: list) -> tuple:
    allcards = hole + board
    return _evaluate_best_5(allcards)

def _fmt_cards(cards: list) -> str:
    return " ".join(f"{r}{s}" for r,s in cards)

def _hand_name(cat: int) -> str:
    return ["High Card","Pair","Two Pair","Three of a Kind","Straight","Flush","Full House","Four of a Kind","Straight Flush"][cat]

class HoldemGame:
    def __init__(self, bet: int, p1_id: int, p2_id: Optional[int] = None):
        self.bet = int(bet)
        self.p1_id = int(p1_id)
        self.p2_id = int(p2_id) if p2_id else None
        # Build and shuffle deck
        self.deck = deal_deck()
        # Hole cards
        self.p1 = [self.deck.pop(), self.deck.pop()]
        self.p2 = [self.deck.pop(), self.deck.pop()] if self.p2_id else [self.deck.pop(), self.deck.pop()]
        # Board
        self.flop = []; self.turn = []; self.river = []

    def reveal_flop(self):
        # burn one (pop) then flop 3
        _ = self.deck.pop()
        self.flop = [self.deck.pop(), self.deck.pop(), self.deck.pop()]

    def reveal_turn(self):
        _ = self.deck.pop()
        self.turn = [self.deck.pop()]

    def reveal_river(self):
        _ = self.deck.pop()
        self.river = [self.deck.pop()]

    @property
    def board(self):
        return self.flop + self.turn + self.river

    def result(self):
        p1_eval = _best_of_seven(self.p1, self.board)
        p2_eval = _best_of_seven(self.p2, self.board)
        if p1_eval > p2_eval: return 1, p1_eval, p2_eval
        if p2_eval > p1_eval: return 2, p1_eval, p2_eval
        return 0, p1_eval, p2_eval

def _holdem_embed(title: str, game: HoldemGame, reveal_hands: bool = False):
    emb = discord.Embed(title=title)
    # Show cards
    if reveal_hands:
        emb.add_field(name="Player 1", value=_fmt_cards(game.p1), inline=False)
        emb.add_field(name=("Player 2" if game.p2_id else "AI"), value=_fmt_cards(game.p2), inline=False)
    else:
        emb.add_field(name="Hands", value="🂠 🂠    vs    🂠 🂠", inline=False)
    # Board
    btxt = _fmt_cards(game.flop) if game.flop else "—"
    if game.turn: btxt += "  " + _fmt_cards(game.turn)
    if game.river: btxt += "  " + _fmt_cards(game.river)
    emb.add_field(name="Board", value=btxt, inline=False)
    emb.set_footer(text=f"Bet: {game.bet} per player")
    return emb

@tree.command(name="holdem", description="Heads-up Texas Hold'em vs AI or another user (fixed-stake, animated).")
@app_commands.describe(bet="Stake per player", opponent="Omit to face AI; mention a user for PvP")
async def holdem_cmd(inter: discord.Interaction, bet: app_commands.Range[int, 1, 1_000_000], opponent: Optional[discord.User] = None):
    # Balance checks
    if opponent:
        if opponent.bot or opponent.id == inter.user.id:
            return await inter.response.send_message("Pick a real opponent.", ephemeral=True)
        if store.get_balance(inter.user.id) < bet:
            return await inter.response.send_message("❌ You don't have enough credits for that bet.", ephemeral=True)
        if store.get_balance(opponent.id) < bet:
            return await inter.response.send_message(f"❌ {opponent.mention} doesn't have enough credits.", ephemeral=True)
        # Challenge flow
        challenge = PvPChallengeView(challenger_id=inter.user.id, challenged_id=opponent.id, timeout=PVP_TIMEOUT)
        await inter.response.send_message(f"♠️ {opponent.mention}, **{inter.user.display_name}** challenges you to **Texas Hold'em** for **{bet}** credits each. Accept?", view=challenge)
        await challenge.wait()
        if challenge.accepted is None:
            return await inter.followup.send("⌛ Challenge expired.")
        if not challenge.accepted:
            return await inter.followup.send("🚫 Challenge declined.")
        game = HoldemGame(bet=bet, p1_id=inter.user.id, p2_id=opponent.id)
        msg = await inter.followup.send(embed=_holdem_embed("Texas Hold'em — Dealing...", game, reveal_hands=False))
    else:
        if store.get_balance(inter.user.id) < bet:
            return await inter.response.send_message("❌ Not enough credits for that bet.", ephemeral=True)
        await inter.response.send_message(embed=discord.Embed(title="Texas Hold'em — Dealing..."))
        msg = await inter.original_response()
        game = HoldemGame(bet=bet, p1_id=inter.user.id, p2_id=None)

    # Animate reveal: preflop (hidden), flop, turn, river
    try:
        await asyncio.sleep(0.7)
        game.reveal_flop()
        await msg.edit(embed=_holdem_embed("Texas Hold'em — Flop", game, reveal_hands=False))
        await asyncio.sleep(0.9)
        game.reveal_turn()
        await msg.edit(embed=_holdem_embed("Texas Hold'em — Turn", game, reveal_hands=False))
        await asyncio.sleep(0.9)
        game.reveal_river()
        await msg.edit(embed=_holdem_embed("Texas Hold'em — River", game, reveal_hands=False))
        await asyncio.sleep(0.9)
    except discord.HTTPException:
        pass

    # Showdown
    winner, p1_eval, p2_eval = game.result()
    p1_name = inter.user.display_name
    p2_name = (opponent.display_name if opponent else "AI")
    title = "Texas Hold'em — Showdown"
    showdown = _holdem_embed(title, game, reveal_hands=True)

    # Human-readable hand names
    p1_cat, p2_cat = p1_eval[0], p2_eval[0]
    showdown.add_field(name=f"{p1_name} Hand", value=_hand_name(p1_cat), inline=True)
    showdown.add_field(name=f"{p2_name} Hand", value=_hand_name(p2_cat), inline=True)

    # Payouts
    if winner == 1:
        # player 1 wins the opponent's bet
        if opponent:
            store.add_balance(inter.user.id, bet)
            store.add_balance(opponent.id, -bet)
            store.add_result(inter.user.id, "win"); store.add_result(opponent.id, "loss")
            outcome = f"✅ **{p1_name}** wins **{bet}** credits from **{p2_name}**."
        else:
            store.add_balance(inter.user.id, bet)
            store.add_result(inter.user.id, "win")
            outcome = f"✅ **{p1_name}** beats the AI! +{bet} credits."
    elif winner == 2:
        if opponent:
            store.add_balance(inter.user.id, -bet)
            store.add_balance(opponent.id, bet)
            store.add_result(opponent.id, "win"); store.add_result(inter.user.id, "loss")
            outcome = f"✅ **{p2_name}** wins **{bet}** credits from **{p1_name}**."
        else:
            store.add_balance(inter.user.id, -bet)
            store.add_result(inter.user.id, "loss")
            outcome = f"❌ AI wins. **-{bet}** credits."
    else:
        # Push
        store.add_result(inter.user.id, "push")
        if opponent:
            store.add_result(opponent.id, "push")
        outcome = "🤝 It's a tie. Push."

    showdown.add_field(name="Outcome", value=outcome, inline=False)
    try:
        await msg.edit(embed=showdown)
    except discord.HTTPException:
        await inter.followup.send(embed=showdown)

if __name__ == "__main__":
    main()


# ====== Poll Overrides: single-vote + results on close ======

class PollView(discord.ui.View):
    def __init__(self, message_id: int, options: list[str], creator_id: int):
        super().__init__(timeout=None)
        self.message_id = int(message_id)
        self.creator_id = int(creator_id)
        for i, label in enumerate(options):
            self.add_item(self._make_vote_button(i, label))
        self.add_item(self._make_close_button())

    def _make_vote_button(self, idx: int, label: str):
        custom_id = f"poll_vote:{self.message_id}:{idx}"
        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, custom_id=custom_id)
        async def cb(inter: discord.Interaction):
            p = store.get_poll(self.message_id)
            if not p or not p.get("open", True):
                return await inter.response.send_message("Poll is closed.", ephemeral=True)

            voters = p.get("voters") or {}
            uid = str(inter.user.id)
            prev = voters.get(uid)

            if isinstance(prev, int) and 0 <= prev < len(p["options"]) and prev != idx:
                try:
                    p["options"][prev]["votes"] = max(0, int(p["options"][prev]["votes"]) - 1)
                except Exception:
                    pass

            if prev == idx:
                return await inter.response.send_message("You already chose that option.", ephemeral=True)

            voters[uid] = idx
            p["voters"] = voters
            p["options"][idx]["votes"] = int(p["options"][idx]["votes"]) + 1

            store.save_poll(self.message_id, p)
            await inter.response.defer()
            await update_poll_message(inter.channel, self.message_id, p)
        btn.callback = cb
        return btn

    def _make_close_button(self):
        custom_id = f"poll_close:{self.message_id}"
        btn = discord.ui.Button(label="Close Poll", style=discord.ButtonStyle.danger, custom_id=custom_id)
        async def cb(inter: discord.Interaction):
            p = store.get_poll(self.message_id)
            if not p:
                return
            is_mod = False
            try:
                if isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
                    is_mod = inter.channel.permissions_for(inter.user).manage_messages
            except Exception:
                pass
            if (inter.user.id != p.get("creator_id")) and not is_mod:
                return await inter.response.send_message("Only the creator or a mod can close this poll.", ephemeral=True)

            # Mark closed
            p["open"] = False
            # Compute results summary
            total = sum(int(o.get("votes", 0)) for o in p.get("options", []))
            lines = []
            for o in p.get("options", []):
                v = int(o.get("votes", 0))
                pct = 0 if total == 0 else int(round((v / total) * 100))
                lines.append(f"**{o.get('label','?')}** — {v} ({pct}%)")
            results_embed = discord.Embed(title="📊 Poll Results — " + str(p.get("question","Poll")), description="\n".join(lines))
            try:
                await inter.channel.send(embed=results_embed)
            except Exception:
                pass

            store.save_poll(self.message_id, p)  # this will delete it
            await inter.response.defer()
            await update_poll_message(inter.channel, self.message_id, p)
        btn.callback = cb
        return btn

async def update_poll_message(channel: discord.abc.Messageable, message_id: int, poll: dict):
    try:
        if hasattr(channel, "fetch_message"):
            msg = await channel.fetch_message(message_id)
        else:
            return
        total = sum(int(o.get("votes", 0)) for o in poll.get("options", []))
        bars = []
        for o in poll.get("options", []):
            v = int(o.get("votes", 0))
            pct = 0 if total == 0 else int(round((v / total) * 100))
            bars.append(f"**{o.get('label','?')}** — {v} ({pct}%)")
        emb = discord.Embed(title="📊 " + str(poll.get("question","Poll")), description="\n".join(bars))
        emb.set_footer(text=("Open" if poll.get("open", True) else "Closed"))
        view = None
        if poll.get("open", True):
            view = PollView(message_id=message_id, options=[o.get("label","?") for o in poll.get("options", [])], creator_id=poll.get("creator_id", 0))
        await msg.edit(embed=emb, view=view)
    except Exception:
        pass

