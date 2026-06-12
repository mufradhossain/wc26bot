# WC26 Bot - FIFA World Cup 2026 Discord Prediction Bot

A Discord bot that lets server members predict World Cup 2026 match winners by reacting with country flag emojis.

## Features

- Posts match prediction cards 30 minutes before kickoff
- Users vote by reacting with home/away flag emojis (supports voting for both)
- Automatically posts results with per-user prediction breakdown when match ends
- Works across multiple Discord servers independently
- Smart API polling — sleeps during match, checks at +115 min, polls only if extra time
- Uses free World Cup 2026 API (no rate limits, no key needed)

## Bot Commands

| Command | Description | Permission |
|---|---|---|
| `/setchannel` | Set the channel for match predictions | Server Admin |

## Setup (Server Admin)

1. Click the bot invite link provided by the developer
2. Select your server and authorize
3. Run `/setchannel` in the channel you want predictions posted to
4. Done — the bot handles everything automatically

## What Gets Posted

**Prediction card** (30 min before kickoff):
```
⚽ MATCH PREDICTION
━━━━━━━━━━━━━━━━━━
🇧🇷 Brazil  vs  🇦🇷 Argentina
📅 June 11, 2026 at 8:00 PM UTC
🏟️ Group A - Matchday 1

React with 🇧🇷 or 🇦🇷 to vote!
```

**Results table** (when match ends):
```
🏁 MATCH RESULT
━━━━━━━━━━━━━━━━━━
🇧🇷 Brazil  2 - 1  Argentina 🇦🇷

📊 Votes: 3 total
🇧🇷 Brazil: 2 (67%)
🇦🇷 Argentina: 1 (33%)

🏆 Predictions:
✅ john — picked 🇧🇷 Brazil (45m before kickoff)
❌ mike — picked 🇦🇷 Argentina (30m before kickoff)
🏳️ sara — voted for BOTH 🇧🇷 and 🇦🇷 (12m before kickoff)
```

## Self-Hosting

### Prerequisites

- Python 3.11+
- Discord bot token + client ID ([Discord Developer Portal](https://discord.com/developers/applications))
- Enable Privileged Intents: Message Content, Server Members

### Local

```bash
pip install -r requirements.txt
```

Create `.env`:
```
DISCORD_TOKEN=your_bot_token
CLIENT_ID=your_client_id
```

```bash
python bot.py
```

### Railway

1. Push to GitHub
2. Create Railway project, connect repo
3. Add persistent volume at `/app/data`
4. Set environment variables:
   - `DISCORD_TOKEN`
   - `CLIENT_ID`
   - `RAILWAY_VOLUME_MOUNT_PATH=/app/data`
5. Railway auto-deploys from Dockerfile

## Architecture

```
bot.py      — Discord bot, commands, reaction handling, match lifecycle
db.py       — SQLite schema (multi-guild: guild_messages, per-guild votes)
api.py      — Free WC2026 API wrapper (worldcup26.ir)
teams.py    — 48 country → flag emoji mappings
Dockerfile  — Production deployment
```

## Match Lifecycle (Smart Polling)

1. Discovery loop checks API every 6 hours for upcoming matches
2. Schedules card to post exactly 30 min before kickoff
3. Sleeps until kickoff + 115 minutes
4. Checks if match finished → posts results
5. If still live (extra time/penalties) → polls every 5 min until done

Typical match: 1 API call. Extra time + penalties: ~12 calls. No rate limit concerns.
