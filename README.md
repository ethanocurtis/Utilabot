# Discord Utility + Blackjack Bot

- Runs in Docker
- Currency system with `/daily`, `/balance`
- Blackjack vs Dealer **and** interactive PvP with wagers
- Leaderboard (`/leaderboard balance|wins`) and achievements (`/achievements`)
- Bulk delete (`/purge`) and per-channel auto-delete (`/autodelete_set`)

## Quick Start

```bash
cp .env.example .env   # put your token
docker compose up -d --build
```

Invite with scopes `bot` and `applications.commands`. Grant bot permissions: **Manage Messages**, **Read Message History**, **Send Messages**, **Embed Links**.

## Commands

- `/daily` — claim daily credits
- `/balance [user]`
- `/blackjack bet:<int> [opponent:@user]` — play Dealer or PvP (interactive hit/stand)
- `/leaderboard category:<balance|wins>`
- `/achievements [user]`
- `/purge limit:<1-1000> [user:@user]`
- `/autodelete_set minutes:<1-1440>` / `/autodelete_disable` / `/autodelete_status`

> Note: Discord bulk delete cannot remove messages older than 14 days.
