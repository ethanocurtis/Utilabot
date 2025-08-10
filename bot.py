
import os
import json
import random
import asyncio
from datetime import datetime, timedelta, timezone, date
from typing import Optional, Dict, List, Tuple

import discord
from discord.ext import tasks
from discord import app_commands

# Trivia deps
import aiohttp
import html
import urllib.parse

# Timezone for reminders default
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

# ---------- Config ----------
DATA_PATH = os.environ.get("DATA_PATH", "/app/data/db.json")
GUILD_IDS: List[int] = []  # Optional: test guild IDs for faster sync

DEFAULT_TZ = "America/Chicago"  # <- requested default

STARTING_DAILY = 250
PVP_TIMEOUT = 120  # seconds to accept/decline PvP challenge
WORK_COOLDOWN_MINUTES = 60
WORK_MIN_PAY = 80
WORK_MAX_PAY = 160

# Daily streak bonus settings
STREAK_MAX_BONUS = 500
STREAK_STEP = 50  # +50 per streak day up to max

# Trivia config
TRIVIA_REWARD = 120
TRIVIA_API = "https://opentdb.com/api.php"
TRIVIA_TOKEN_API = "https://opentdb.com/api_token.php"

intents = discord.Intents.default()
intents.message_content = False
intents.guilds = True
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


# ---------- Simple JSON Store ----------
def _ensure_shape(data: dict) -> dict:
    data.setdefault("wallets", {})
    data.setdefault("daily", {})
    data.setdefault("autodelete", {})
    data.setdefault("stats", {})         # user_id -> {"wins":0,"losses":0,"pushes":0}
    data.setdefault("achievements", {})  # user_id -> [names]
    data.setdefault("work", {})          # user_id -> last ISO timestamp
    data.setdefault("streaks", {})       # user_id -> {"count": int, "last_date": "YYYY-MM-DD"}
    data.setdefault("trivia", {})        # {"token": "..."}
    # Utilities
    data.setdefault("notes", {})         # user_id -> [str]
    data.setdefault("pins", {})          # channel_id -> str
    data.setdefault("reminders", [])     # list of dicts
    return data


class Store:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(_ensure_shape({}), f)

    def read(self):
        with open(self.path, "r", encoding="utf-8") as f:
            return _ensure_shape(json.load(f))

    def write(self, data):
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

    # Daily helpers
    def get_last_daily(self, user_id: int) -> Optional[str]:
        data = self.read()
        return data["daily"].get(str(user_id))

    def set_last_daily(self, user_id: int, iso_ts: str):
        data = self.read()
        data["daily"][str(user_id)] = iso_ts
        self.write(data)

    # Streak helpers
    def get_streak(self, user_id: int) -> Dict[str, object]:
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

    # Auto-delete config: {channel_id: seconds}
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
        if token is None:
            data["trivia"].pop("token", None)
        else:
            data["trivia"]["token"] = token
        self.write(data)

    # Notes, Pins, Reminders
    def add_note(self, user_id: int, text: str):
        data = self.read()
        arr = data["notes"].get(str(user_id), [])
        arr.append(text)
        data["notes"][str(user_id)] = arr
        self.write(data)

    def list_notes(self, user_id: int) -> List[str]:
        data = self.read()
        return data["notes"].get(str(user_id), [])

    def delete_note(self, user_id: int, index: int) -> bool:
        data = self.read()
        arr = data["notes"].get(str(user_id), [])
        if 0 <= index < len(arr):
            arr.pop(index)
            data["notes"][str(user_id)] = arr
            self.write(data)
            return True
        return False

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

    def add_reminder(self, reminder: dict) -> int:
        data = self.read()
        arr = data["reminders"]
        reminder["id"] = (max([r.get("id", 0) for r in arr]) + 1) if arr else 1
        arr.append(reminder)
        self.write(data)
        return reminder["id"]

    def list_reminders(self, user_id: Optional[int] = None) -> List[dict]:
        data = self.read()
        arr = data["reminders"]
        if user_id is None:
            return arr
        return [r for r in arr if r.get("user_id") == user_id]

    def remove_reminder(self, reminder_id: int) -> bool:
        data = self.read()
        arr = data["reminders"]
        new_arr = [r for r in arr if r.get("id") != reminder_id]
        if len(new_arr) != len(arr):
            data["reminders"] = new_arr
            self.write(data)
            return True
        return False

    def pop_due_reminders(self, now_iso: str) -> List[dict]:
        """Pop reminders whose due time <= now."""
        data = self.read()
        arr = data["reminders"]
        now_dt = datetime.fromisoformat(now_iso)
        due, future = [], []
        for r in arr:
            try:
                due_dt = datetime.fromisoformat(r.get("when_iso"))
            except Exception:
                continue
            if due_dt <= now_dt:
                due.append(r)
            else:
                future.append(r)
        if due:
            data["reminders"] = future
            self.write(data)
        return due


store = Store(DATA_PATH)


# ---------- Cards / Blackjack helpers ----------
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


def is_blackjack(cards: List[Tuple[str, str]]) -> bool:
    return len(cards) == 2 and hand_value(cards) == 21


# ---------- Utility ----------
def require_manage_messages():
    def predicate(inter: discord.Interaction):
        perms = inter.channel.permissions_for(inter.user) if isinstance(inter.channel, (discord.TextChannel, discord.Thread)) else None
        if not perms or not perms.manage_messages:
            raise app_commands.CheckFailure("You need the **Manage Messages** permission here.")
        return True
    return app_commands.check(predicate)


def _maybe_award_after_hand(user_id: int, bet_delta: int, player_cards: List[Tuple[str, str]], won: bool) -> List[str]:
    newly: List[str] = []
    # First win
    if won and store.get_stats(user_id).get("wins", 0) == 0:
        if store.award_achievement(user_id, "First Blood"):
            newly.append("First Blood")
    # Blackjack
    if is_blackjack(player_cards):
        if store.award_achievement(user_id, "Blackjack!"):
            newly.append("Blackjack!")
    # High Roller
    if won and bet_delta >= 1000:
        if store.award_achievement(user_id, "High Roller (1k+)"):
            newly.append("High Roller (1k+)")
    # Milestones
    wins = store.get_stats(user_id).get("wins", 0)
    for m in (5, 10, 25, 50, 100):
        name = f"Win Milestone {m}"
        if wins >= m and store.award_achievement(user_id, name):
            newly.append(name)
    return newly


# ---------- Views for PvP ----------
class PvPChallengeView(discord.ui.View):
    def __init__(self, challenger_id: int, challenged_id: int, timeout: float = PVP_TIMEOUT):
        super().__init__(timeout=timeout)
        self.challenger_id = challenger_id
        self.challenged_id = challenged_id
        self.accepted: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.challenged_id:
            await interaction.response.send_message("Only the challenged user can respond.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.accepted = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.accepted = False
        await interaction.response.defer()
        self.stop()


class PvPBlackjackView(discord.ui.View):
    """One shared view for spectators; only the active player can press buttons."""
    def __init__(self, current_player_id: int, timeout: float = 120):
        super().__init__(timeout=timeout)
        self.current_player_id = current_player_id
        self.choice: Optional[str] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.current_player_id:
            await interaction.response.send_message("It's not your turn.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "hit"
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "stand"
        await interaction.response.defer()
        self.stop()


# ---------- Commands ----------
@tree.command(name="balance", description="Check your balance.")
async def balance(inter: discord.Interaction, user: Optional[discord.User] = None):
    target = user or inter.user
    bal = store.get_balance(target.id)
    await inter.response.send_message(f"💰 **{target.display_name}** has **{bal}** credits.")


def _update_streak(user_id: int) -> int:
    """Returns new streak count after updating with today's claim logic."""
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
            return await inter.response.send_message(
                f"⏳ You already claimed. Try again in **{hrs}h {mins}m**.",
                ephemeral=True
            )

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
    emb.set_footer(text="Tip: /work every hour, /slots and /highlow to play, /trivia for quick cash.")
    await inter.response.send_message(embed=emb)


# ----- Blackjack (Dealer or PvP spectator-friendly) -----
@tree.command(name="blackjack", description="Play Blackjack vs dealer or challenge another user (spectator-friendly).")
@app_commands.describe(bet="Amount to wager", opponent="Optional: challenge another user for PvP")
async def blackjack(inter: discord.Interaction, bet: app_commands.Range[int, 1, 1_000_000], opponent: Optional[discord.User] = None):
    bettor_id = inter.user.id
    bal = store.get_balance(bettor_id)
    if bal < bet:
        return await inter.response.send_message("❌ Not enough credits for that bet.", ephemeral=True)

    # PvP spectator-friendly mode
    if opponent and opponent.id != inter.user.id and not opponent.bot:
        opp_bal = store.get_balance(opponent.id)
        if opp_bal < bet:
            return await inter.response.send_message(f"❌ {opponent.mention} doesn’t have enough credits to accept.", ephemeral=True)

        view_challenge = PvPChallengeView(challenger_id=inter.user.id, challenged_id=opponent.id)
        await inter.response.send_message(
            f"🎲 {opponent.mention}, **{inter.user.display_name}** challenges you to Blackjack for **{bet}** credits each. Accept?",
            view=view_challenge
        )
        await view_challenge.wait()
        if view_challenge.accepted is None:
            return await inter.followup.send("⌛ Challenge expired.")
        if not view_challenge.accepted:
            return await inter.followup.send("🚫 Challenge declined.")

        # Start game (single shared public message)
        deck = deal_deck()
        p1 = [deck.pop(), deck.pop()]
        p2 = [deck.pop(), deck.pop()]
        current_player_id = inter.user.id  # challenger goes first

        def embed_state(title_suffix: str = ""):
            title = f"♠ PvP Blackjack {title_suffix}".strip()
            emb = discord.Embed(title=title, description=f"Bet each: **{bet}**")
            emb.add_field(name=f"{inter.user.display_name}", value=fmt_hand(p1), inline=True)
            emb.add_field(name=f"{opponent.display_name}", value=fmt_hand(p2), inline=True)
            emb.add_field(
                name="Turn",
                value=f"▶️ **{inter.user.display_name if current_player_id==inter.user.id else opponent.display_name}**",
                inline=False
            )
            return emb

        view = PvPBlackjackView(current_player_id=current_player_id)
        msg = await inter.followup.send(embed=embed_state("— Game Start"), view=view)

        # Turn loop for each player
        async def play_turn(player_id: int, hand: List[Tuple[str, str]], name: str):
            nonlocal view, msg, current_player_id
            # swap current player in the view
            current_player_id = player_id
            view = PvPBlackjackView(current_player_id=current_player_id)
            try:
                await msg.edit(embed=embed_state(), view=view)
            except discord.HTTPException:
                pass

            # actions until stand or bust or timeout
            while hand_value(hand) < 21:
                await view.wait()
                choice = view.choice
                view.choice = None
                if choice == "hit":
                    hand.append(deck.pop())
                    try:
                        await msg.edit(embed=embed_state())
                    except discord.HTTPException:
                        pass
                    # refresh view (to reset button state)
                    view = PvPBlackjackView(current_player_id=current_player_id)
                    try:
                        await msg.edit(view=view)
                    except discord.HTTPException:
                        pass
                    continue
                else:
                    break  # stand or timeout

            # disable buttons for this turn
            for child in view.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
            try:
                await msg.edit(view=view)
            except discord.HTTPException:
                pass

        # Challenger then opponent
        await play_turn(inter.user.id, p1, inter.user.display_name)
        await play_turn(opponent.id, p2, opponent.display_name)

        v1 = hand_value(p1)
        v2 = hand_value(p2)

        outcome = "Tie! It’s a push."
        if v1 > 21 and v2 > 21:
            store.add_result(inter.user.id, "push")
            store.add_result(opponent.id, "push")
        elif v1 > 21:
            outcome = f"**{opponent.display_name}** wins!"
            store.add_balance(inter.user.id, -bet)
            store.add_balance(opponent.id, bet)
            store.add_result(inter.user.id, "loss")
            store.add_result(opponent.id, "win")
        elif v2 > 21:
            outcome = f"**{inter.user.display_name}** wins!"
            store.add_balance(opponent.id, -bet)
            store.add_balance(inter.user.id, bet)
            store.add_result(opponent.id, "loss")
            store.add_result(inter.user.id, "win")
        else:
            if v1 > v2:
                outcome = f"**{inter.user.display_name}** wins!"
                store.add_balance(opponent.id, -bet)
                store.add_balance(inter.user.id, bet)
                store.add_result(opponent.id, "loss")
                store.add_result(inter.user.id, "win")
            elif v2 > v1:
                outcome = f"**{opponent.display_name}** wins!"
                store.add_balance(inter.user.id, -bet)
                store.add_balance(opponent.id, bet)
                store.add_result(inter.user.id, "loss")
                store.add_result(opponent.id, "win")
            else:
                store.add_result(inter.user.id, "push")
                store.add_result(opponent.id, "push")

        # Achievements announce
        new1 = _maybe_award_after_hand(inter.user.id, bet, p1, "wins" in outcome and inter.user.display_name in outcome)
        new2 = _maybe_award_after_hand(opponent.id, bet, p2, "wins" in outcome and opponent.display_name in outcome)

        emb = discord.Embed(title="♠ PvP Blackjack — Result", description=f"Bet each: **{bet}**")
        emb.add_field(name=inter.user.display_name, value=fmt_hand(p1), inline=True)
        emb.add_field(name=opponent.display_name, value=fmt_hand(p2), inline=True)
        emb.add_field(name="Outcome", value=outcome, inline=False)
        if new1 or new2:
            awards_text = ""
            if new1:
                awards_text += f"🏆 {inter.user.display_name}: " + ", ".join(new1) + "\n"
            if new2:
                awards_text += f"🏆 {opponent.display_name}: " + ", ".join(new2)
            if awards_text:
                emb.add_field(name="New Achievements", value=awards_text, inline=False)
        try:
            await msg.edit(embed=emb, view=None)
        except discord.HTTPException:
            await inter.followup.send(embed=emb)
        return

    # Vs Dealer (interactive for the player; spectators see updates)
    deck = deal_deck()
    player = [deck.pop(), deck.pop()]
    dealer = [deck.pop(), deck.pop()]

    class DealerView(discord.ui.View):
        def __init__(self, uid: int, timeout: float = 120):
            super().__init__(timeout=timeout)
            self.uid = uid
            self.choice: Optional[str] = None

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.uid:
                await interaction.response.send_message("This isn’t your game.", ephemeral=True)
                return False
            return True

        @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
        async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.choice = "hit"
            await interaction.response.defer()
            self.stop()

        @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
        async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.choice = "stand"
            await interaction.response.defer()
            self.stop()

    def dealer_embed(title="♣ Blackjack vs Dealer"):
        emb = discord.Embed(title=title, description=f"Bet: **{bet}**")
        emb.add_field(name="Your Hand", value=fmt_hand(player), inline=True)
        # show only dealer upcard until stand/bust
        emb.add_field(name="Dealer Shows", value=f"{dealer[0][0]}{dealer[0][1]} ??", inline=True)
        return emb

    await inter.response.send_message(embed=dealer_embed(), view=DealerView(uid=inter.user.id))
    msg = await inter.original_response()

    view = DealerView(uid=inter.user.id)
    await msg.edit(view=view)

    while True:
        if hand_value(player) >= 21:
            break
        await view.wait()
        choice = view.choice
        view.choice = None
        if choice == "hit":
            player.append(deck.pop())
            try:
                await msg.edit(embed=dealer_embed(), view=view)
            except discord.HTTPException:
                pass
            view = DealerView(uid=inter.user.id)  # refresh buttons
            try:
                await msg.edit(view=view)
            except discord.HTTPException:
                pass
            continue
        else:
            break

    # Dealer plays
    while hand_value(dealer) < 17:
        dealer.append(deck.pop())

    pv = hand_value(player)
    dv = hand_value(dealer)

    won_flag = False
    if pv > 21:
        result = "You busted. Dealer wins."
        store.add_balance(inter.user.id, -bet)
        store.add_result(inter.user.id, "loss")
    elif dv > 21 or pv > dv:
        result = "You win!"
        store.add_balance(inter.user.id, bet)
        store.add_result(inter.user.id, "win")
        won_flag = True
    elif dv > pv:
        result = "Dealer wins."
        store.add_balance(inter.user.id, -bet)
        store.add_result(inter.user.id, "loss")
    else:
        result = "Push."
        store.add_result(inter.user.id, "push")

    newly = _maybe_award_after_hand(inter.user.id, bet if won_flag else 0, player, won_flag)

    final = discord.Embed(title="♣ Blackjack vs Dealer — Result", description=f"Bet: **{bet}**")
    final.add_field(name="Your Hand", value=fmt_hand(player), inline=True)
    final.add_field(name="Dealer Hand", value=fmt_hand(dealer), inline=True)
    final.add_field(name="Outcome", value=result, inline=False)
    if newly:
        final.add_field(name="New Achievements", value=", ".join(newly), inline=False)
    await msg.edit(embed=final, view=None)


# ----- High/Low -----
@tree.command(name="highlow", description="Simple High/Low card game. Guess if the next card is higher or lower.")
@app_commands.describe(bet="Amount to wager")
async def highlow(inter: discord.Interaction, bet: app_commands.Range[int, 1, 1_000_000]):
    if store.get_balance(inter.user.id) < bet:
        return await inter.response.send_message("❌ Not enough credits for that bet.", ephemeral=True)

    deck = deal_deck()
    first = deck.pop()

    def rank_value(r: str) -> int:
        order = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]
        return order.index(r)

    class HLView(discord.ui.View):
        def __init__(self, uid: int, timeout: float = 60):
            super().__init__(timeout=timeout)
            self.uid = uid
            self.choice: Optional[str] = None

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.uid:
                await interaction.response.send_message("This isn’t your round.", ephemeral=True)
                return False
            return True

        @discord.ui.button(label="Higher", style=discord.ButtonStyle.primary)
        async def higher(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.choice = "higher"
            await interaction.response.defer()
            self.stop()

        @discord.ui.button(label="Lower", style=discord.ButtonStyle.secondary)
        async def lower(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.choice = "lower"
            await interaction.response.defer()
            self.stop()

    def make_embed(title="♦ High/Low"):
        emb = discord.Embed(title=title, description=f"Bet: **{bet}**")
        emb.add_field(name="Current Card", value=f"{first[0]}{first[1]}", inline=True)
        emb.set_footer(text="Guess if the next card is higher or lower.")
        return emb

    view = HLView(uid=inter.user.id)
    await inter.response.send_message(embed=make_embed(), view=view)
    msg = await inter.original_response()
    await view.wait()

    second = deck.pop()
    first_v = rank_value(first[0])
    second_v = rank_value(second[0])

    outcome = "Push."
    result_tag = "push"
    if view.choice is None:
        pass
    elif second_v == first_v:
        outcome = "Equal rank! Push."
    else:
        is_higher = second_v > first_v
        if (is_higher and view.choice == "higher") or ((not is_higher) and view.choice == "lower"):
            outcome = "You win!"
            result_tag = "win"
            store.add_balance(inter.user.id, bet)
        else:
            outcome = "You lose."
            result_tag = "loss"
            store.add_balance(inter.user.id, -bet)

    store.add_result(inter.user.id, result_tag)
    newly = []
    if result_tag == "win":
        newly = _maybe_award_after_hand(inter.user.id, bet, [first, second], True)

    emb = discord.Embed(title="♦ High/Low — Result", description=f"Bet: **{bet}**")
    emb.add_field(name="First Card", value=f"{first[0]}{first[1]}", inline=True)
    emb.add_field(name="Second Card", value=f"{second[0]}{second[1]}", inline=True)
    emb.add_field(name="Outcome", value=outcome, inline=False)
    if newly:
        emb.add_field(name="New Achievements", value=", ".join(newly), inline=False)
    await msg.edit(embed=emb, view=None)


# ----- Enhanced Slot Machine (Animated, Wilds, Nudge, Spin Again) -----
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
NUDGE_UPGRADE_CHANCE = 0.10  # 10% chance to upgrade a two-of-a-kind to three-of-a-kind

def _best_triplet_with_wilds(reels: List[str]) -> Optional[Tuple[str, int]]:
    trip = tuple(reels)
    if trip in SLOT_PAYOUTS:
        mult = SLOT_PAYOUTS[trip]
        label = "Jackpot" if mult >= 20 else "Win"
        return (f"{label} x{mult}!", mult)
    for sym in ["7️⃣","🍀","⭐","🔔","🍋","🍒"]:
        match = sum(1 for r in reels if r == sym or r == WILD)
        if match == 3:
            mult = SLOT_PAYOUTS[(sym, sym, sym)]
            label = "Jackpot" if mult >= 20 else "Win"
            return (f"{label} x{mult}!", mult)
    for i in range(3):
        for j in range(i+1, 3):
            if reels[i] == reels[j] or WILD in (reels[i], reels[j]):
                return ("Nice! Two of a kind x2.", 2)
    return None

class SpinAgainView(discord.ui.View):
    def __init__(self, uid: int, bet: int, timeout: float = 30):
        super().__init__(timeout=timeout)
        self.uid = uid
        self.bet = bet

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.uid:
            await interaction.response.send_message("Only the original spinner can use this.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Spin Again", style=discord.ButtonStyle.primary, emoji="🔁")
    async def spin_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await slots_internal(interaction, self.bet)

async def slots_internal(inter_or_ctx: discord.Interaction, bet: int):
    if store.get_balance(inter_or_ctx.user.id) < bet:
        return await inter_or_ctx.followup.send("❌ Not enough credits for that bet.", ephemeral=True)

    # Choose final reels (before potential nudge)
    final_reels = [random.choice(SLOT_SYMBOLS) for _ in range(3)]

    # Animation start
    spinning = ["⬜", "⬜", "⬜"]
    emb = discord.Embed(title="🎰 Slot Machine")
    emb.add_field(name="Spin", value=" | ".join(spinning), inline=False)
    emb.add_field(name="Bet", value=str(bet), inline=True)
    emb.set_footer(text="Spinning...")
    try:
        msg = await inter_or_ctx.original_response()
        await msg.edit(embed=emb)
    except discord.NotFound:
        await inter_or_ctx.response.send_message(embed=emb)
        msg = await inter_or_ctx.original_response()

    # Animate reels
    reels = spinning[:]
    stops = [12, 16, 20]
    for t in range(max(stops)):
        for i in range(3):
            if t < stops[i] - 1:
                reels[i] = random.choice(SLOT_SYMBOLS)
            elif t == stops[i] - 1:
                reels[i] = final_reels[i]
        try:
            anim = discord.Embed(title="🎰 Slot Machine")
            anim.add_field(name="Spin", value=" | ".join(reels), inline=False)
            anim.add_field(name="Bet", value=str(bet), inline=True)
            anim.set_footer(text="Spinning..." if t < max(stops)-1 else "Result")
            await msg.edit(embed=anim)
        except discord.HTTPException:
            pass
        await asyncio.sleep(0.18)

    # Lucky nudge
    best = _best_triplet_with_wilds(final_reels)
    if best is None:
        pair = False
        for i in range(3):
            for j in range(i+1, 3):
                if final_reels[i] == final_reels[j] or WILD in (final_reels[i], final_reels[j]):
                    pair = True
        if pair and random.random() < NUDGE_UPGRADE_CHANCE:
            target_sym = "7️⃣"
            for sym in ["7️⃣","🍀","⭐","🔔","🍋","🍒"]:
                c = sum(1 for r in final_reels if r == sym or r == WILD)
                if c >= 2:
                    target_sym = sym
                    break
            for i in range(3):
                if final_reels[i] != target_sym and final_reels[i] != WILD:
                    final_reels[i] = target_sym
                    break
            best = _best_triplet_with_wilds(final_reels)

    if best is None:
        result_text, mult = ("You lose.", 0)
    else:
        result_text, mult = best

    # settle
    if mult == 0:
        store.add_balance(inter_or_ctx.user.id, -bet)
        net = -bet
    else:
        win_amount = bet * mult
        store.add_balance(inter_or_ctx.user.id, win_amount - bet)
        net = win_amount - bet

    final = discord.Embed(title="🎰 Slot Machine — Result")
    final.add_field(name="Spin", value=" | ".join(final_reels), inline=False)
    final.add_field(name="Bet", value=str(bet), inline=True)
    final.add_field(name="Result", value=result_text, inline=True)
    final.add_field(name="Net", value=(f"+{net}" if net >= 0 else str(net)), inline=True)
    view = SpinAgainView(uid=inter_or_ctx.user.id, bet=bet)
    try:
        await msg.edit(embed=final, view=view)
    except discord.HTTPException:
        await inter_or_ctx.followup.send(embed=final, view=view)

@tree.command(name="slots", description="Spin the slot machine. Matching symbols (with 🃏 wilds) pay big!")
@app_commands.describe(bet="Amount to wager")
async def slots(inter: discord.Interaction, bet: app_commands.Range[int, 1, 1_000_000]):
    await inter.response.defer()
    await slots_internal(inter, bet)


# ----- Earn: Work -----
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


# ----- Earn: Trivia via OpenTDB -----
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

async def fetch_trivia_question(session: aiohttp.ClientSession, difficulty: Optional[str] = None, category: Optional[int] = None):
    token = await _get_or_create_trivia_token(session)
    params = {
        "amount": 1,
        "type": "multiple",
        "encode": "url3986",
    }
    if difficulty in {"easy", "medium", "hard"}:
        params["difficulty"] = difficulty
    if isinstance(category, int):
        params["category"] = category
    if token:
        params["token"] = token

    async def _do_request():
        async with session.get(TRIVIA_API, params=params, timeout=15) as resp:
            return await resp.json()

    data = await _do_request()
    rc = data.get("response_code", 1)
    if rc == 4 and token:
        await _reset_trivia_token(session)
        data = await _do_request()
        rc = data.get("response_code", 1)

    if rc != 0 or not data.get("results"):
        return None

    item = data["results"][0]
    q = urllib.parse.unquote(item["question"])
    correct = urllib.parse.unquote(item["correct_answer"])
    incorrect = [urllib.parse.unquote(x) for x in item.get("incorrect_answers", [])]
    choices = incorrect + [correct]
    random.shuffle(choices)
    correct_idx = choices.index(correct)
    return q, choices, correct_idx


DIFF_CHOICES = [
    app_commands.Choice(name="Any", value=""),
    app_commands.Choice(name="Easy", value="easy"),
    app_commands.Choice(name="Medium", value="medium"),
    app_commands.Choice(name="Hard", value="hard"),
]

@tree.command(name="trivia", description="Answer a multiple-choice question for credits (powered by OpenTDB).")
@app_commands.describe(difficulty="Pick a difficulty (default Any).", category_id="Optional OpenTDB category id (e.g., 18 for Computers)")
@app_commands.choices(difficulty=DIFF_CHOICES)
async def trivia(inter: discord.Interaction, difficulty: Optional[app_commands.Choice[str]] = None, category_id: Optional[int] = None):
    diff_val = difficulty.value if difficulty else None
    async with aiohttp.ClientSession() as session:
        fetched = await fetch_trivia_question(session, diff_val or None, category_id)
    if not fetched:
        return await inter.response.send_message("⚠️ Couldn't fetch a trivia question right now. Try again in a bit.")

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
    await inter.response.send_message(embed=emb, view=view)
    msg = await inter.original_response()
    await view.wait()

    if view.choice is None:
        return await msg.edit(content="⌛ Time's up!", embed=None, view=None)

    if view.choice == correct_idx:
        store.add_balance(inter.user.id, TRIVIA_REWARD)
        return await msg.edit(content=f"✅ Correct! You earned **{TRIVIA_REWARD}** credits.", embed=None, view=None)
    else:
        return await msg.edit(content=f"❌ Nope. Correct answer was **{letters[correct_idx]}**.", embed=None, view=None)


# ----- NEW MULTIPLAYER GAME: Dice Duel -----
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
    if view.accepted is None:
        return await inter.followup.send("⌛ Challenge expired.")
    if not view.accepted:
        return await inter.followup.send("🚫 Challenge declined.")

    def roll2():
        return random.randint(1,6), random.randint(1,6)

    rerolls = 3
    history = []
    while True:
        a = roll2()
        b = roll2()
        sa, sb = sum(a), sum(b)
        history.append((a, sa, b, sb))
        if sa != sb or rerolls == 0:
            break
        rerolls -= 1

    # settle
    if sa > sb:
        outcome = f"**{inter.user.display_name}** wins! ({a[0]}+{a[1]}={sa} vs {b[0]}+{b[1]}={sb})"
        if bet:
            store.add_balance(inter.user.id, bet)
            store.add_balance(opponent.id, -bet)
        store.add_result(inter.user.id, "win")
        store.add_result(opponent.id, "loss")
    elif sb > sa:
        outcome = f"**{opponent.display_name}** wins! ({b[0]}+{b[1]}={sb} vs {a[0]}+{a[1]}={sa})"
        if bet:
            store.add_balance(inter.user.id, -bet)
            store.add_balance(opponent.id, bet)
        store.add_result(opponent.id, "win")
        store.add_result(inter.user.id, "loss")
    else:
        outcome = f"Tie after rerolls ({sa}={sb}). It's a push."
        store.add_result(opponent.id, "push")
        store.add_result(inter.user.id, "push")

    desc_lines = [f"Round {i+1}: 🎲 {h[0][0]}+{h[0][1]}={h[1]}  vs  🎲 {h[2][0]}+{h[2][1]}={h[3]}" for i, h in enumerate(history)]
    emb = discord.Embed(title="🎲 Dice Duel Results", description="\n".join(desc_lines))
    emb.add_field(name="Outcome", value=outcome, inline=False)
    await inter.followup.send(embed=emb)


# ----- Utility: Pay / Cooldowns / Stats -----
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
    # daily
    daily_left = "Ready ✅"
    last_iso = store.get_last_daily(inter.user.id)
    if last_iso:
        last = datetime.fromisoformat(last_iso)
        cd = timedelta(hours=20) - (now - last)
        if cd.total_seconds() > 0:
            h = int(cd.total_seconds() // 3600)
            m = int((cd.total_seconds() % 3600) // 60)
            daily_left = f"{h}h {m}m"
    # work
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


# ----- Notes -----
@tree.command(name="note_add", description="Save a personal note.")
@app_commands.describe(text="Your note text")
async def note_add(inter: discord.Interaction, text: str):
    if len(text) > 500:
        text = text[:500]
    store.add_note(inter.user.id, text)
    await inter.response.send_message("📝 Note saved.", ephemeral=True)

@tree.command(name="notes", description="List your notes or delete one.")
@app_commands.describe(delete_index="Optional: index to delete (starts at 1)")
async def notes(inter: discord.Interaction, delete_index: Optional[app_commands.Range[int, 1, 10_000]] = None):
    if delete_index is not None:
        ok = store.delete_note(inter.user.id, delete_index - 1)
        if ok:
            return await inter.response.send_message(f"🗑️ Deleted note #{delete_index}.", ephemeral=True)
        else:
            return await inter.response.send_message("Index out of range.", ephemeral=True)
    arr = store.list_notes(inter.user.id)
    if not arr:
        return await inter.response.send_message("No notes yet. Use `/note_add` to add one.", ephemeral=True)
    lines = [f"**{i+1}.** {t}" for i, t in enumerate(arr)]
    emb = discord.Embed(title="📝 Your Notes", description="\n".join(lines))
    await inter.response.send_message(embed=emb, ephemeral=True)


# ----- Channel Pin -----
@tree.command(name="pin_set", description="Set a sticky note for this channel.")
async def pin_set(inter: discord.Interaction, text: str):
    if not isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
        return await inter.response.send_message("Use this in a text channel.", ephemeral=True)
    store.set_pin(inter.channel.id, text[:1000])
    await inter.response.send_message("📌 Pin set for this channel.")

@tree.command(name="pin_show", description="Show this channel's sticky note.")
async def pin_show(inter: discord.Interaction):
    if not isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
        return await inter.response.send_message("Use this in a text channel.", ephemeral=True)
    txt = store.get_pin(inter.channel.id)
    if not txt:
        return await inter.response.send_message("No pin set. Use `/pin_set`.", ephemeral=True)
    await inter.response.send_message(f"📌 **Channel Pin:** {txt}")

@tree.command(name="pin_clear", description="Clear this channel's sticky note.")
async def pin_clear(inter: discord.Interaction):
    if not isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
        return await inter.response.send_message("Use this in a text channel.", ephemeral=True)
    store.clear_pin(inter.channel.id)
    await inter.response.send_message("🧹 Pin cleared.")


# ----- Polls -----
class PollView(discord.ui.View):
    def __init__(self, author_id: int, options: List[str], timeout: float = 1800):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.options = options
        self.votes: Dict[int, int] = {}  # user_id -> option index
        for i, opt in enumerate(options):
            self.add_item(self._make_button(i, opt))
        self.add_item(self._make_close())

    def tally(self) -> List[int]:
        counts = [0]*len(self.options)
        for idx in self.votes.values():
            counts[idx] += 1
        return counts

    def _make_button(self, idx: int, label: str):
        async def cb(interaction: discord.Interaction, idx=idx):
            self.votes[interaction.user.id] = idx
            await interaction.response.defer()
            await self._refresh_message(interaction)
        b = discord.ui.Button(label=label[:80], style=discord.ButtonStyle.secondary)
        b.callback = cb
        return b

    def _make_close(self):
        async def close_cb(interaction: discord.Interaction):
            if interaction.user.id != self.author_id:
                return await interaction.response.send_message("Only the poll creator can close it.", ephemeral=True)
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
            await self._refresh_message(interaction, closed=True)
            self.stop()
        btn = discord.ui.Button(label="Close", style=discord.ButtonStyle.danger)
        btn.callback = close_cb
        return btn

    async def _refresh_message(self, interaction: discord.Interaction, closed: bool=False):
        counts = self.tally()
        total = sum(counts)
        parts = []
        for i, opt in enumerate(self.options):
            c = counts[i]
            bar = "▮"*min(20, int((c/total)*20)) if total else ""
            parts.append(f"**{opt}** — {c} {bar}")
        desc = "\n".join(parts) or "No votes yet."
        title = "📊 Poll (closed)" if closed else "📊 Poll"
        emb = discord.Embed(title=title, description=desc)
        try:
            msg = await interaction.original_response()
            await msg.edit(embed=emb, view=(None if closed else self))
        except discord.NotFound:
            await interaction.followup.send(embed=emb, view=(None if closed else self))

@tree.command(name="poll", description="Create a quick poll with up to 5 options (separate by ;)")
@app_commands.describe(question="Your poll question", options="Options separated by ; (2-5 options)")
async def poll(inter: discord.Interaction, question: str, options: str):
    opts = [o.strip() for o in options.split(";") if o.strip()]
    if not (2 <= len(opts) <= 5):
        return await inter.response.send_message("Provide 2 to 5 options separated by `;`.", ephemeral=True)
    view = PollView(author_id=inter.user.id, options=opts)
    emb = discord.Embed(title="📊 Poll", description=question)
    await inter.response.send_message(embed=emb, view=view)


# ----- Helpers -----
@tree.command(name="choose", description="Pick one of the comma-separated choices.")
async def choose(inter: discord.Interaction, choices: str):
    opts = [c.strip() for c in choices.split(",") if c.strip()]
    if len(opts) < 2:
        return await inter.response.send_message("Give me at least two choices separated by commas.", ephemeral=True)
    pick = random.choice(opts)
    await inter.response.send_message(f"🎯 {pick}")

@tree.command(name="timer", description="Simple countdown timer that pings you when done.")
async def timer(inter: discord.Interaction, seconds: app_commands.Range[int, 1, 86400]):
    await inter.response.send_message(f"⏳ Timer set for {seconds} seconds.", ephemeral=True)
    await asyncio.sleep(seconds)
    try:
        await inter.followup.send(f"⏰ <@{inter.user.id}> time's up!")
    except discord.HTTPException:
        pass


# ----- Info -----
@tree.command(name="serverinfo", description="Show basic server info.")
async def serverinfo(inter: discord.Interaction):
    if not isinstance(inter.guild, discord.Guild):
        return await inter.response.send_message("Use this in a server.", ephemeral=True)
    g = inter.guild
    emb = discord.Embed(title=f"ℹ️ Server Info — {g.name}")
    emb.add_field(name="Members", value=str(g.member_count), inline=True)
    emb.add_field(name="Created", value=g.created_at.strftime("%Y-%m-%d"), inline=True)
    emb.add_field(name="Roles", value=str(len(g.roles)), inline=True)
    if g.icon:
        emb.set_thumbnail(url=g.icon.url)
    await inter.response.send_message(embed=emb)

@tree.command(name="whois", description="Info about a user.")
async def whois(inter: discord.Interaction, user: Optional[discord.User] = None):
    u = user or inter.user
    emb = discord.Embed(title=f"👤 {u.display_name}")
    emb.add_field(name="ID", value=str(u.id), inline=True)
    emb.add_field(name="Created", value=u.created_at.strftime("%Y-%m-%d"), inline=True)
    if isinstance(inter.guild, discord.Guild):
        m = inter.guild.get_member(u.id)
        if m and m.joined_at:
            emb.add_field(name="Joined", value=m.joined_at.strftime("%Y-%m-%d"), inline=True)
        if m and m.roles:
            emb.add_field(name="Roles", value=", ".join(r.name for r in m.roles if r.name != "@everyone") or "None", inline=False)
    if u.avatar:
        emb.set_thumbnail(url=u.avatar.url)
    await inter.response.send_message(embed=emb)


# ----- Reminders -----
def _parse_offset(offset_str: str) -> Optional[timezone]:
    """Parse strings like -05:00 or +02:30 into tzinfo with that fixed offset."""
    try:
        sign = 1
        s = offset_str.strip()
        if s[0] == "-":
            sign = -1
            s = s[1:]
        elif s[0] == "+":
            s = s[1:]
        hh, mm = s.split(":")
        delta = timedelta(hours=int(hh), minutes=int(mm))
        return timezone(sign*delta)
    except Exception:
        return None

def _tz_chicago():
    if ZoneInfo:
        try:
            return ZoneInfo(DEFAULT_TZ)
        except Exception:
            pass
    # fallback: US Central fixed offset (no DST awareness)
    return timezone(timedelta(hours=-6))

@tree.command(name="remind_in", description="Remind yourself in N minutes.")
@app_commands.describe(minutes="Minutes from now", message="What should I remind you?", dm="Send reminder via DM instead of channel")
async def remind_in(inter: discord.Interaction, minutes: app_commands.Range[int,1,7*24*60], message: str, dm: Optional[bool] = False):
    due = datetime.now(timezone.utc) + timedelta(minutes=int(minutes))
    rid = store.add_reminder({
        "user_id": inter.user.id,
        "channel_id": inter.channel.id if isinstance(inter.channel, (discord.TextChannel, discord.Thread)) else None,
        "message": message[:1000],
        "when_iso": due.isoformat(),
        "dm": bool(dm),
    })
    await inter.response.send_message(f"⏰ Reminder #{rid} set for **{minutes}** minutes from now.")

@tree.command(name="remind_at", description="Remind at an exact date & time (defaults to America/Chicago).")
@app_commands.describe(date_str="YYYY-MM-DD", time_str="HH:MM (24h)", message="Reminder text", tz_offset="Optional offset like -05:00 or +02:00", dm="Send as DM")
async def remind_at(inter: discord.Interaction, date_str: str, time_str: str, message: str, tz_offset: Optional[str] = None, dm: Optional[bool] = False):
    # Determine timezone
    tzinfo = None
    if tz_offset:
        tzinfo = _parse_offset(tz_offset)
    if tzinfo is None:
        tzinfo = _tz_chicago()

    try:
        naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        local_dt = naive.replace(tzinfo=tzinfo)
        due_utc = local_dt.astimezone(timezone.utc)
    except Exception:
        return await inter.response.send_message("Couldn't parse date/time. Use `YYYY-MM-DD` and `HH:MM` (24h). Example: `2025-08-10 14:30`", ephemeral=True)

    rid = store.add_reminder({
        "user_id": inter.user.id,
        "channel_id": inter.channel.id if isinstance(inter.channel, (discord.TextChannel, discord.Thread)) else None,
        "message": message[:1000],
        "when_iso": due_utc.isoformat(),
        "dm": bool(dm),
    })
    local_label = due_utc.astimezone(_tz_chicago()).strftime("%Y-%m-%d %H:%M")
    await inter.response.send_message(f"⏰ Reminder #{rid} set for **{local_label} America/Chicago**.")

@tree.command(name="reminders", description="List your pending reminders.")
async def reminders(inter: discord.Interaction):
    arr = store.list_reminders(inter.user.id)
    if not arr:
        return await inter.response.send_message("No pending reminders.", ephemeral=True)
    lines = []
    now_local = datetime.now(timezone.utc).astimezone(_tz_chicago())
    for r in sorted(arr, key=lambda x: x.get("when_iso")):
        when = datetime.fromisoformat(r["when_iso"]).astimezone(_tz_chicago())
        delta = when - now_local
        mins = int(delta.total_seconds() // 60)
        lines.append(f"**#{r['id']}** — {when.strftime('%Y-%m-%d %H:%M')} CT  ({mins}m)")
    emb = discord.Embed(title="⏰ Your Reminders", description="\n".join(lines))
    await inter.response.send_message(embed=emb, ephemeral=True)

@tree.command(name="remind_cancel", description="Cancel a reminder by id.")
@app_commands.describe(reminder_id="ID from /reminders")
async def remind_cancel(inter: discord.Interaction, reminder_id: int):
    # Allow cancel own; mods can cancel any
    own = any(r["id"] == reminder_id for r in store.list_reminders(inter.user.id))
    perms_ok = False
    if isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
        perms_ok = inter.channel.permissions_for(inter.user).manage_messages
    if not own and not perms_ok:
        return await inter.response.send_message("You can only cancel your own reminders (or need Manage Messages).", ephemeral=True)
    ok = store.remove_reminder(reminder_id)
    if ok:
        await inter.response.send_message(f"✅ Reminder #{reminder_id} canceled.")
    else:
        await inter.response.send_message("Not found.", ephemeral=True)


# ---------- Background Cleaner & Reminder Dispatcher ----------
@tasks.loop(minutes=2)
async def cleanup_loop():
    conf = store.get_autodelete()
    if not conf:
        return
    now = datetime.now(timezone.utc)
    for chan_id, secs in list(conf.items()):
        channel = bot.get_channel(int(chan_id))
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            continue

        cutoff = now - timedelta(seconds=int(secs))
        try:
            await channel.purge(
                limit=1000,
                check=lambda m: m.created_at < cutoff,
                bulk=True
            )
        except discord.Forbidden:
            continue
        except discord.HTTPException:
            continue

@tasks.loop(seconds=30)
async def reminder_loop():
    now_iso = datetime.now(timezone.utc).isoformat()
    due = store.pop_due_reminders(now_iso)
    for r in due:
        try:
            content = f"⏰ <@{r['user_id']}> Reminder: {r.get('message','')}"
            if r.get("dm"):
                user = await bot.fetch_user(int(r["user_id"]))
                await user.send(content)
            else:
                channel = bot.get_channel(int(r.get("channel_id"))) if r.get("channel_id") else None
                if isinstance(channel, (discord.TextChannel, discord.Thread)):
                    await channel.send(content)
        except Exception:
            continue

@cleanup_loop.before_loop
async def before_cleanup():
    await bot.wait_until_ready()

@reminder_loop.before_loop
async def before_reminders():
    await bot.wait_until_ready()


# ---------- Startup ----------
@bot.event
async def on_ready():
    if GUILD_IDS:
        for gid in GUILD_IDS:
            guild = discord.Object(id=gid)
            await tree.sync(guild=guild)
    else:
        await tree.sync()

    if not cleanup_loop.is_running():
        cleanup_loop.start()
    if not reminder_loop.is_running():
        reminder_loop.start()

    print(f"Logged in as {bot.user} ({bot.user.id})")


# ---------- Main ----------
def main():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN not set")
    bot.run(token)


if __name__ == "__main__":
    main()
