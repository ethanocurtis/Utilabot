# Discord Utility & Games Bot

A multi-purpose Discord bot with economy, games, reminders, notes, polls, moderation tools, price tracking, and a fully interactive reaction-based shop.  
Now includes an **admin allowlist** so you can give extra users access to admin commands.

---

## Table of Contents
1. [Features](#features)
   - [Economy](#economy)
   - [Games](#games)
   - [Shop](#shop)
   - [Reminders](#reminders)
   - [Notes](#notes)
   - [Polls](#polls)
   - [Price Tracker](#price-tracker)
   - [Moderation Tools](#moderation-tools)
   - [Admin Allowlist](#admin-allowlist)
2. [Installation](#installation)
3. [Update Guide](#update-guide)
4. [Usage](#usage)
5. [Permissions](#permissions)
6. [Known Limitations](#known-limitations)

---

## Features

### Economy
- `/daily` — claim daily credits  
- `/balance [user]` — check your own or another user’s balance  
- `/work` — earn random credits with cooldown  
- `/pay user:@user amount:<int>` — send credits to another user  
- `/cooldowns` — see your economy cooldowns  
- `/stats [user]` — show a user’s stats  
- `/leaderboard category:<balance|wins>` — top balances or most game wins  
- `/achievements [user]` — view achievements  

### Games
- `/blackjack bet:<int> [opponent:@user]` — play vs Dealer or another player (interactive hit/stand)  
- `/highlow bet:<int>` — guess if the next number is higher or lower  
- `/diceduel bet:<int> [opponent:@user]` — dice battle against a player or bot  
- `/slots bet:<int>` — animated slot machine  
- `/trivia` — answer random trivia questions for rewards  

### Shop
- `/shop` — **Interactive reaction-based shop**:
  - Displays your current balance at the top  
  - 1️⃣–9️⃣ — Select item  
  - ◀️ ▶️ — Switch pages  
  - 🛒 — Switch to Buy mode  
  - 💵 — Switch to Sell mode  
  - ➕ / ➖ — Adjust quantity  
  - ✅ — Confirm purchase/sale  
  - ❌ — Close shop  
- Items have buy/sell prices and descriptions  
- Supports buying multiple quantities and selling owned items 

### Reminders
- `/remind_in duration:<1m/h/d> text:<str>` — set a timer reminder  
- `/remind_at date:<MM-DD-YYYY> time:<HH:MM> text:<str>` — set a reminder for a specific date/time  
- `/reminders` — list your active reminders  
- `/remind_cancel id:<int>` — cancel a reminder  

### Notes
- `/note_add text:<str>` — save a personal note  
- `/notes` — list your saved notes  

### Polls
- `/poll_create question:<str> options:<str,str,...>` — create a multiple-choice poll  
- Interactive voting buttons (fixed button bug from previous version)  
- `/poll_vote id:<int> option:<str>` — vote on a poll  
- `/poll_status id:<int>` — view poll results  

### Price Tracker
- `/price_add <item|link>` — add item with auto-fetched or mock price  
- `/price_list` — list tracked items with clickable links  
- `/price_remove <item>` — remove from tracker  

### Moderation Tools
(Admins or allowlisted users only)
- `/purge limit:<1-1000> [user:@user]` — bulk delete recent messages  
- `/autodelete_set minutes:<1-1440>` — auto-delete messages in a channel after X minutes  
- `/autodelete_disable` — disable auto-delete in the channel  
- `/autodelete_status` — check current auto-delete settings  

> Discord bulk delete cannot remove messages older than 14 days.

### Admin Allowlist
- `/admin_allow @user` — give a user access to admin commands without making them a Discord admin  
- `/admin_revoke @user` — remove a user from the allowlist  
- `/admin_list` — list all allowlisted users  

---

## Installation

### 1. Clone and Prepare
```bash
git clone https://github.com/yourusername/yourbotrepo.git
cd yourbotrepo
cp .env.example .env
```
Edit `.env`:
```
DISCORD_TOKEN=your_discord_bot_token
```

### 2. Build and Run with Docker
```bash
docker compose up -d --build
```

### 3. Invite the Bot
Generate an invite link with:
- Scopes: `bot`, `applications.commands`
- Permissions:  
  - **Manage Messages**  
  - **Read Message History**  
  - **Send Messages**  
  - **Embed Links**  
  - **Add Reactions**

---

## Update Guide

### Pull Latest Changes
```bash
cd yourbotrepo
git pull
```

### Rebuild and Restart
```bash
docker compose down
docker compose up -d --build
```

---

## Usage
Once the bot is in your server, type `/` in the chat to see available commands.  
Only admins or allowlisted users can use moderation commands.

---

## Permissions
- **Public**: Economy, games, shop, reminders, notes, polls, price tracker, `/autodelete_status`  
- **Admin/Allowlist**: `/purge`, `/autodelete_set`, `/autodelete_disable`, admin management commands

---

## Known Limitations
- Discord’s bulk delete won’t remove messages older than 14 days  
- Auto-delete works only for messages sent after it’s enabled  
- Shop reaction menu expires after 2 minutes of inactivity
