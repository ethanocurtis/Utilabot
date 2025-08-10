import os
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Tuple

import discord
from discord.ext import tasks
from discord import app_commands

# ---------- Config ----------
DATA_PATH = os.environ.get("DATA_PATH", "/app/data/db.json")
GUILD_IDS: List[int] = []  # Optional: test guild IDs for faster sync, e.g. [123456789012345678]

STARTING_DAILY = 250
PVP_TIMEOUT = 120  # seconds to accept/decline PvP challenge

intents = discord.Intents.default()
intents.message_content = False
intents.guilds = True
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


# ---------- Simple JSON Store ----------

def _ensure_shape(data: dict) -> dict:
    # Coerce to dict if file was corrupted/old
    if not isinstance(data, dict):
        data = {}
    def need_dict(key):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    # Core economy & meta
    need_dict("wallets")
    need_dict("daily")
    need_dict("autodelete")
    need_dict("stats")
    need_dict("achievements")
    need_dict("work")
    need_dict("streaks")
    need_dict("trivia")
    # Utilities
    need_dict("notes")
    need_dict("pins")
    # Polls
    need_dict("polls")
    # Reminders
    need_dict("reminders")
    if not isinstance(data.get("reminder_seq"), int):
        data["reminder_seq"] = int(data.get("reminder_seq") or 0)
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


# ---------- Views for PvP Blackjack ----------
class PvPBlackjackView(discord.ui.View):
    """One shared view for spectators; only the active player can press buttons."""
    def __init__(self, current_player_id: int, timeout: float = 120):
        super().__init__(timeout=timeout)
        self.current_player_id = current_player_id
        self.choice: Optional[str] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only current player may act
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


# ---------- Commands ----------
@tree.command(name="balance", description="Check your balance.")
async def balance(inter: discord.Interaction, user: Optional[discord.User] = None):
    target = user or inter.user
    bal = store.get_balance(target.id)
    await inter.response.send_message(f"💰 **{target.display_name}** has **{bal}** credits.")


@tree.command(name="daily", description="Claim your daily free credits.")
async def daily(inter: discord.Interaction):
    now = datetime.now(timezone.utc)
    last_iso = store.get_last_daily(inter.user.id)
    if last_iso:
        last = datetime.fromisoformat(last_iso)
        if now - last < timedelta(hours=20):  # generous window
            remaining = timedelta(hours=20) - (now - last)
            hrs = int(remaining.total_seconds() // 3600)
            mins = int((remaining.total_seconds() % 3600) // 60)
            return await inter.response.send_message(
                f"⏳ You already claimed. Try again in **{hrs}h {mins}m**.",
                ephemeral=True
            )
    store.add_balance(inter.user.id, STARTING_DAILY)
    store.set_last_daily(inter.user.id, now.isoformat())
    await inter.response.send_message(f"✅ You claimed **{STARTING_DAILY}** credits. Use `/blackjack` or `/highlow` to play!")


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


# ----- New Mini‑Game: High/Low -----
# Reveal one card, you guess whether the next card will be Higher or Lower.
# Payout: 1:1 (equal rank = push)
@tree.command(name="highlow", description="Simple High/Low card game. Guess if the next card is higher or lower.")
@app_commands.describe(bet="Amount to wager")
async def highlow(inter: discord.Interaction, bet: app_commands.Range[int, 1, 1_000_000]):
    if store.get_balance(inter.user.id) < bet:
        return await inter.response.send_message("❌ Not enough credits for that bet.", ephemeral=True)

    deck = deal_deck()
    first = deck.pop()

    def rank_value(r: str) -> int:
        # Treat A as high
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

    # Draw second card and settle
    second = deck.pop()
    first_v = rank_value(first[0])
    second_v = rank_value(second[0])

    outcome = "Push."
    result_tag = "push"
    if view.choice is None:
        # timeout counts as reveal only
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


# ----- Moderation: Purge -----
@tree.command(name="purge", description="Bulk delete recent messages (max 1000).")
@app_commands.describe(limit="Number of recent messages to scan (1-1000)", user="Only delete messages by this user")
@require_manage_messages()
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


# ----- Auto-Delete: set/disable/status -----
@tree.command(name="autodelete_set", description="Enable auto-delete for this channel after N minutes.")
@app_commands.describe(minutes="Delete messages older than this many minutes (min 1, max 1440)")
@require_manage_messages()
async def autodelete_set(inter: discord.Interaction, minutes: app_commands.Range[int, 1, 1440]):
    if not isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
        return await inter.response.send_message("Use this in a text channel.", ephemeral=True)
    seconds = minutes * 60
    store.set_autodelete(inter.channel.id, seconds)
    await inter.response.send_message(f"🗑️ Auto-delete enabled: messages older than **{minutes}** minutes will be cleaned up periodically.", ephemeral=True)


@tree.command(name="autodelete_disable", description="Disable auto-delete for this channel.")
@require_manage_messages()
async def autodelete_disable(inter: discord.Interaction):
    if not isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
        return await inter.response.send_message("Use this in a text channel.", ephemeral=True)
    store.remove_autodelete(inter.channel.id)
    await inter.response.send_message("🛑 Auto-delete disabled for this channel.", ephemeral=True)


@tree.command(name="autodelete_status", description="Show auto-delete settings for this channel.")
async def autodelete_status(inter: discord.Interaction):
    if not isinstance(inter.channel, (discord.TextChannel, discord.Thread)):
        return await inter.response.send_message("Use this in a text channel.", ephemeral=True)
    conf = store.get_autodelete()
    secs = conf.get(str(inter.channel.id))
    if secs:
        mins = secs // 60
        await inter.response.send_message(f"✅ Auto-delete is **ON**: older than **{mins}** minutes.", ephemeral=True)
    else:
        await inter.response.send_message("❌ Auto-delete is **OFF** for this channel.", ephemeral=True)


# ----- Leaderboard & Achievements -----
@tree.command(name="leaderboard", description="Show the top players.")
@app_commands.describe(category="Choose 'balance' or 'wins'")
@app_commands.choices(category=[
    app_commands.Choice(name="balance", value="balance"),
    app_commands.Choice(name="wins", value="wins")
])
async def leaderboard(inter: discord.Interaction, category: app_commands.Choice[str]):
    top = store.list_top(category.value, 10)
    if not top:
        return await inter.response.send_message("No data yet.")
    lines = []
    for i, (uid, val) in enumerate(top, start=1):
        try:
            user = await bot.fetch_user(uid)
            uname = user.display_name
        except Exception:
            uname = f"User {uid}"
        lines.append(f"**{i}. {uname}** — {val} {'credits' if category.value=='balance' else 'wins'}")
    emb = discord.Embed(title=f"🏆 Leaderboard — {category.value.capitalize()}", description="\n".join(lines))
    await inter.response.send_message(embed=emb)


@tree.command(name="achievements", description="Show your achievements (or another user's).")
async def achievements(inter: discord.Interaction, user: Optional[discord.User] = None):
    target = user or inter.user
    ach = store.get_achievements(target.id)
    emb = discord.Embed(title=f"🏆 Achievements — {target.display_name}")
    if not ach:
        emb.description = "None yet — go win some games!"
    else:
        emb.description = ", ".join(sorted(ach))
    stats = store.get_stats(target.id)
    emb.add_field(name="Record", value=f"{stats.get('wins',0)}W / {stats.get('losses',0)}L / {stats.get('pushes',0)}P", inline=False)
    await inter.response.send_message(embed=emb)


# ---------- Background Cleaner ----------
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


@cleanup_loop.before_loop
async def before_cleanup():
    await bot.wait_until_ready()


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

    print(f"Logged in as {bot.user} ({bot.user.id})")


# ---------- Main ----------
def main():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN not set")
    bot.run(token)


if __name__ == "__main__":
    main()
