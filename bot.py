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

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

# ---------- Config ----------
DATA_PATH = os.environ.get("DATA_PATH", "/app/data/db.json")
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

def fmt_sun(dt_str: str, tz_name: str = DEFAULT_TZ_NAME):
    try:
        # dt_str like "2025-08-10T06:05"
        y, m, d = map(int, dt_str[:10].split("-"))
        hh, mm = map(int, dt_str[11:16].split(":"))
        tz = _chicago_tz_for(datetime(y, m, d))
        return datetime(y, m, d, hh, mm, tzinfo=timezone.utc).astimezone(tz).strftime("%I:%M %p")
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

# ---------- Simple JSON Store ----------
def _ensure_shape(data: dict) -> dict:
    # Base structures
    def ensure_dict(key):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    def ensure_list(key):
        if not isinstance(data.get(key), list):
            data[key] = []

    ensure_dict("wallets")
    ensure_dict("daily")
    ensure_dict("autodelete")
    ensure_dict("stats")
    ensure_dict("achievements")
    ensure_dict("work")
    ensure_dict("streaks")
    ensure_dict("trivia")

    # Weather subscriptions
    ensure_dict("weather")
    if not isinstance(data["weather"].get("subs"), dict):
        data["weather"]["subs"] = {}
    if not isinstance(data["weather"].get("zips"), dict):
        data["weather"]["zips"] = {}
    if not isinstance(data["weather"].get("seq"), int):
        data["weather"]["seq"] = 0

    ensure_dict("notes")
    ensure_dict("pins")

    ensure_dict("polls")

    ensure_dict("reminders")
    if not isinstance(data.get("reminder_seq"), int):
        data["reminder_seq"] = 0

    # Admin allowlist (new)
    ensure_list("admin_allowlist")

    # NEW: Inventory map
    ensure_dict("inventory")

    return data


class Store:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(_ensure_shape({}), f)

    def read(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        return _ensure_shape(data)

    def write(self, data: dict):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_ensure_shape(data), f, indent=2)
        os.replace(tmp, self.path)

    # Wallet helpers
    def get_balance(self, user_id: int) -> int:
        data = self.read()
        return int(data["wallets"].get(str(user_id), 0))

    def add_balance(self, user_id: int, amount: int):
        data = self.read()
        key = str(user_id)
        data["wallets"][key] = int(data["wallets"].get(key, 0)) + int(amount)
        self.write(data)

    def set_balance(self, user_id: int, amount: int):
        data = self.read()
        data["wallets"][str(user_id)] = int(amount)
        self.write(data)

    # NEW: Inventory helpers
    def get_inventory(self, user_id: int) -> Dict[str, int]:
        data = self.read()
        return {k: int(v) for k, v in data["inventory"].get(str(user_id), {}).items()}

    def add_item(self, user_id: int, item_name: str, qty: int = 1):
        if qty == 0:
            return
        data = self.read()
        inv = data["inventory"].get(str(user_id), {})
        inv[item_name] = int(inv.get(item_name, 0)) + int(qty)
        if inv[item_name] <= 0:
            inv.pop(item_name, None)
        data["inventory"][str(user_id)] = inv
        self.write(data)

    def remove_item(self, user_id: int, item_name: str, qty: int = 1) -> bool:
        data = self.read()
        inv = data["inventory"].get(str(user_id), {})
        have = int(inv.get(item_name, 0))
        if have < qty:
            return False
        inv[item_name] = have - qty
        if inv[item_name] <= 0:
            inv.pop(item_name, None)
        data["inventory"][str(user_id)] = inv
        self.write(data)
        return True

    def has_item(self, user_id: int, item_name: str, qty: int = 1) -> bool:
        inv = self.get_inventory(user_id)
        return inv.get(item_name, 0) >= qty

    # Daily helpers
    def get_last_daily(self, user_id: int) -> Optional[str]:
        data = self.read()
        return data["daily"].get(str(user_id))

    def set_last_daily(self, user_id: int, iso_ts: str):
        data = self.read()
        data["daily"][str(user_id)] = iso_ts
        self.write(data)

    # Streak helpers
    def get_streak(self, user_id: int) -> Dict[str, Any]:
        data = self.read()
        return data["streaks"].get(str(user_id), {"count": 0, "last_date": None})

    def set_streak(self, user_id: int, count: int, last_date: Optional[str]):
        data = self.read()
        data["streaks"][str(user_id)] = {"count": int(count), "last_date": last_date}
        self.write(data)

    # Work cooldown
    def get_last_work(self, user_id: int) -> Optional[str]:
        data = self.read()
        return data["work"].get(str(user_id))

    def set_last_work(self, user_id: int, iso_ts: str):
        data = self.read()
        data["work"][str(user_id)] = iso_ts
        self.write(data)

    # Auto-delete config
    def get_autodelete(self) -> dict:
        return self.read()["autodelete"]

    def set_autodelete(self, channel_id: int, seconds: int):
        data = self.read()
        data["autodelete"][str(channel_id)] = int(seconds)
        self.write(data)

    def remove_autodelete(self, channel_id: int):
        data = self.read()
        data["autodelete"].pop(str(channel_id), None)
        self.write(data)

    # Stats & achievements
    def add_result(self, user_id: int, result: str):
        data = self.read()
        s = data["stats"].get(str(user_id), {"wins": 0, "losses": 0, "pushes": 0})
        if result == "win":
            s["wins"] += 1
        elif result == "loss":
            s["losses"] += 1
        else:
            s["pushes"] += 1
        data["stats"][str(user_id)] = s
        self.write(data)

    def get_stats(self, user_id: int) -> dict:
        data = self.read()
        return data["stats"].get(str(user_id), {"wins": 0, "losses": 0, "pushes": 0})

    def list_top(self, key: str, limit: int = 10):
        data = self.read()
        if key == "balance":
            items = [(int(uid), int(bal)) for uid, bal in data["wallets"].items()]
            items.sort(key=lambda x: x[1], reverse=True)
            return items[:limit]
        elif key == "wins":
            stats = data["stats"]
            items = [(int(uid), s.get("wins", 0)) for uid, s in stats.items()]
            items.sort(key=lambda x: x[1], reverse=True)
            return items[:limit]
        return []

    def get_achievements(self, user_id: int) -> List[str]:
        data = self.read()
        return data["achievements"].get(str(user_id), [])

    def award_achievement(self, user_id: int, name: str) -> bool:
        data = self.read()
        arr = data["achievements"].get(str(user_id), [])
        if name not in arr:
            arr.append(name)
            data["achievements"][str(user_id)] = arr
            self.write(data)
            return True
        return False

    # Trivia token helpers
    def get_trivia_token(self) -> Optional[str]:
        data = self.read()
        return data["trivia"].get("token")

    def set_trivia_token(self, token: Optional[str]):
        data = self.read()
        data['trivia']['token'] = token
        self.write(data)
    # Weather defaults & subscriptions
    def set_user_zip(self, user_id: int, zip_code: str):
        data = self.read()
        data["weather"]["zips"][str(user_id)] = str(zip_code)
        self.write(data)

    def get_user_zip(self, user_id: int) -> Optional[str]:
        data = self.read()
        return data["weather"]["zips"].get(str(user_id))

    def add_weather_sub(self, sub: dict) -> int:
        data = self.read()
        sid = int(data["weather"].get("seq", 0)) + 1
        data["weather"]["seq"] = sid
        data["weather"]["subs"][str(sid)] = sub
        self.write(data)
        return sid

    def list_weather_subs(self, user_id: Optional[int] = None) -> list:
        data = self.read()
        items = []
        for sid, s in data["weather"]["subs"].items():
            s2 = dict(s); s2["id"] = int(sid)
            if user_id is None or int(s2.get("user_id", 0)) == int(user_id):
                items.append(s2)
        items.sort(key=lambda x: x.get("next_run_utc", ""))
        return items

    def remove_weather_sub(self, sid: int, requester_id: int) -> bool:
        data = self.read()
        s = data["weather"]["subs"].get(str(sid))
        if not s:
            return False
        if int(s.get("user_id", 0)) != int(requester_id):
            return False
        data["weather"]["subs"].pop(str(sid), None)
        self.write(data)
        return True

    def update_weather_sub(self, sid: int, **updates):
        data = self.read()
        key = str(sid)
        if key not in data["weather"]["subs"]:
            return False
        s = data["weather"]["subs"][key]
        s.update(updates)
        data["weather"]["subs"][key] = s
        self.write(data)
        return True
        data = self.read()
        if token is None:
            data["trivia"].pop("token", None)
        else:
            data["trivia"]["token"] = token
        self.write(data)

    # Notes
    def add_note(self, user_id: int, text: str):
        data = self.read()
        arr = data["notes"].get(str(user_id), [])
        arr.append(text)
        data["notes"][str(user_id)] = arr
        self.write(data)

    def list_notes(self, user_id: int) -> List[str]:
        data = self.read()
        return data["notes"].get(str(user_id), [])

    def delete_note(self, user_id: int, idx: int) -> bool:
        data = self.read()
        arr = data["notes"].get(str(user_id), [])
        if 0 <= idx < len(arr):
            arr.pop(idx)
            data["notes"][str(user_id)] = arr
            self.write(data)
            return True
        return False

    # Pins
    def set_pin(self, channel_id: int, text: str):
        data = self.read()
        data["pins"][str(channel_id)] = text
        self.write(data)

    def get_pin(self, channel_id: int) -> Optional[str]:
        data = self.read()
        return data["pins"].get(str(channel_id))

    def clear_pin(self, channel_id: int):
        data = self.read()
        data["pins"].pop(str(channel_id), None)
        self.write(data)

    # Polls
    def save_poll(self, message_id: int, poll: dict):
        data = self.read()
        data["polls"][str(message_id)] = poll
        self.write(data)

    def get_poll(self, message_id: int) -> Optional[dict]:
        data = self.read()
        return data["polls"].get(str(message_id))

    def delete_poll(self, message_id: int):
        data = self.read()
        data["polls"].pop(str(message_id), None)
        self.write(data)

    def list_open_polls(self) -> List[Tuple[int, dict]]:
        data = self.read()
        out = []
        for mid, p in data["polls"].items():
            if p.get("open", True):
                out.append((int(mid), p))
        return out

    # Reminders
    def add_reminder(self, rem: dict) -> int:
        data = self.read()
        rid = int(data.get("reminder_seq", 0)) + 1
        data["reminder_seq"] = rid
        data["reminders"][str(rid)] = rem
        self.write(data)
        return rid

    def list_reminders(self, user_id: Optional[int] = None) -> List[dict]:
        data = self.read()
        arr = []
        for rid, r in data["reminders"].items():
            r2 = dict(r)
            r2["id"] = int(rid)
            if (user_id is None) or (int(r2.get("user_id", 0)) == int(user_id)):
                arr.append(r2)
        arr.sort(key=lambda x: x.get("due_utc", ""))
        return arr

    def cancel_reminder(self, rid: int, requester_id: int, is_mod: bool) -> bool:
        data = self.read()
        r = data["reminders"].get(str(rid))
        if not r:
            return False
        if (not is_mod) and int(r.get("user_id", 0)) != int(requester_id):
            return False
        data["reminders"].pop(str(rid), None)
        self.write(data)
        return True

    # ---- Admin allowlist helpers (new) ----
    def is_allowlisted(self, user_id: int) -> bool:
        data = self.read()
        return str(user_id) in {str(x) for x in data["admin_allowlist"]}

    def add_allowlisted(self, user_id: int) -> bool:
        data = self.read()
        s = {str(x) for x in data["admin_allowlist"]}
        if str(user_id) in s:
            return False
        s.add(str(user_id))
        data["admin_allowlist"] = sorted(map(int, s))
        self.write(data)
        return True

    def remove_allowlisted(self, user_id: int) -> bool:
        data = self.read()
        s = {str(x) for x in data["admin_allowlist"]}
        if str(user_id) not in s:
            return False
        s.remove(str(user_id))
        data["admin_allowlist"] = sorted(map(int, s))
        self.write(data)
        return True

    def list_allowlisted(self) -> List[int]:
        data = self.read()
        return list(map(int, data["admin_allowlist"]))


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

@tree.command(name="weather", description="Current weather by US ZIP code (no API key).")
async def weather_cmd(inter: discord.Interaction, zip: app_commands.Range[str, 5, 10]):
    await inter.response.defer()
    z = re.sub(r"[^0-9]", "", zip)
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


async def _fetch_outlook(session: aiohttp.ClientSession, lat: float, lon: float, days: int, tz_name: str = DEFAULT_TZ_NAME):
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
                            outlook = await _fetch_outlook(session, lat, lon, days=2, tz_name=DEFAULT_TZ_NAME)
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
                            store.update_weather_sub(s["id"], next_run_utc=next_local.astimezone(timezone.utc).isoformat())
                        else:
                            days = int(s.get("weekly_days", 7))
                            days = 10 if days > 10 else (3 if days < 3 else days)
                            outlook = await _fetch_outlook(session, lat, lon, days=days, tz_name=DEFAULT_TZ_NAME)
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
                            store.update_weather_sub(s["id"], next_run_utc=next_local.astimezone(timezone.utc).isoformat())
                    except Exception:
                        fallback = now_utc + timedelta(minutes=5)
                        store.update_weather_sub(s["id"], next_run_utc=fallback.isoformat())
    except Exception:
        pass

@weather_scheduler.before_loop
async def before_weather():
    await bot.wait_until_ready()


@weather_scheduler.before_loop
async def before_weather():
    await bot.wait_until_ready()

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

@tree.command(name="shop", description="Browse items available for purchase.")
async def shop_cmd(inter: discord.Interaction):
    lines = []
    for name, meta in SHOP_CATALOG.items():
        price = meta["price"]
        price_str = f"{price} cr" if isinstance(price, int) else "–"
        lines.append(f"**{name}** — Buy: {price_str} - Sell: {meta['sell'] if meta['sell'] is not None else 0} - _{meta['desc']}_")
    emb = discord.Embed(title="🛒 Shop")
    emb.description = "\n".join(lines)
    await inter.response.send_message(embed=emb)

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
    uid = inter.user.id
    # Checks
    if not store.has_item(uid, "Fishing Pole", 1):
        return await inter.response.send_message("You need a **Fishing Pole**. Buy one with `/buy Fishing Pole`.", ephemeral=True)
    bait_type = None
    if store.has_item(uid, "Premium Bait", 1):
        bait_type = "Premium Bait"
        table = FISH_TABLE_PREMIUM
    elif store.has_item(uid, "Basic Bait", 1):
        bait_type = "Basic Bait"
        table = FISH_TABLE_BASIC
    else:
        return await inter.response.send_message("You need **Basic Bait** or **Premium Bait**.", ephemeral=True)
    # Consume bait
    store.remove_item(uid, bait_type, 1)
    catch = weighted_choice(table)
    store.add_item(uid, catch, 1)
    sell_val = SHOP_CATALOG.get(catch, {}).get("sell", 0) or 0
    flair = "🎣"
    if "Rare" in catch: flair = "💎"
    if "Epic" in catch: flair = "🌟"
    await inter.response.send_message(f"{flair} You cast your line using **{bait_type}** and caught **{catch}**! (Sell value: **{sell_val}** cr)")

# ---------- Notes ----------
@tree.command(name="note_add", description="Save a personal note.")
async def note_add(inter: discord.Interaction, text: str):
    store.add_note(inter.user.id, text)
    await inter.response.send_message("📝 Saved.", ephemeral=True)

@tree.command(name="notes", description="List or delete your notes.")
async def notes(inter: discord.Interaction, delete_index: Optional[app_commands.Range[int, 1, 999]] = None):
    if delete_index:
        ok = store.delete_note(inter.user.id, delete_index - 1)
        if ok:
            return await inter.response.send_message("🗑️ Deleted.", ephemeral=True)
        else:
            return await inter.response.send_message("Index out of range.", ephemeral=True)
    arr = store.list_notes(inter.user.id)
    if not arr:
        return await inter.response.send_message("No notes.", ephemeral=True)
    lines = [f"{i+1}. {t}" for i,t in enumerate(arr)]
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
        return (user is None) or (m.author.id == user.id)
    await inter.response.defer(ephemeral=True)
    try:
        deleted = await inter.channel.purge(limit=limit, check=check, bulk=True)
        await inter.followup.send(f"🧹 Deleted **{len(deleted)}** messages.", ephemeral=True)
    except discord.Forbidden:
        await inter.followup.send("I need the **Manage Messages** and **Read Message History** permissions.", ephemeral=True)
    except discord.HTTPException as e:
        await inter.followup.send(f"Error while deleting: {e}", ephemeral=True)

@tree.command(name="autodelete_set", description="Enable auto-delete for this channel after N minutes.")
@app_commands.describe(minutes="Delete messages older than this many minutes (min 1, max 1440)")
@require_admin_or_allowlisted()
async def autodelete_set(inter: discord.Interaction, minutes: app_commands.Range[int, 1, 1440]):
    if not isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
        return await inter.response.send_message("Use this in a text channel.", ephemeral=True)
    seconds = minutes * 60; store.set_autodelete(inter.channel.id, seconds)
    await inter.response.send_message(f"🗑️ Auto-delete enabled: older than **{minutes}** minutes.", ephemeral=True)

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
        mins = secs // 60; await inter.response.send_message(f"✅ Auto-delete is **ON**: older than **{mins}** minutes.", ephemeral=True)
    else:
        await inter.response.send_message("❌ Auto-delete is **OFF** for this channel.", ephemeral=True)

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
        cutoff = now - timedelta(seconds=int(secs))
        try:
            await channel.purge(limit=1000, check=lambda m: m.created_at < cutoff, bulk=True)
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
                store.cancel_reminder(int(r.get("id", 0)), requester_id=0, is_mod=True)
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

if __name__ == "__main__":
    main()