# IPSW Signing Monitor Bot

A Telegram bot that monitors Apple firmware signing status on [ipsw.me](https://ipsw.me) and [ipsw.dev](https://ipsw.dev) and notifies you of any changes. Bot was deployed and available as [@ipswsignedbot](https://t.me/ipswsignedbot).

## How it works

1. The bot asks for your device type (iPhone, iPad, Mac, etc.)
2. You select a lineup (iPhone 16, iPhone 15, …) and then a specific model
3. The bot shows currently signed firmwares (Release + Beta/Dev)
4. Press **🔔 Notify me on changes** to subscribe to updates

Every 30 minutes (configurable) the bot checks ipsw.me and ipsw.dev, compares against the previous state, and sends a notification if:
- A new firmware version becomes signed ✅
- A firmware version stops being signed ❌

## Setup

```bash
# 1. Clone or download the files

# 2. Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure the bot token
cp .env.example .env
# Open .env and paste your BOT_TOKEN from @BotFather

# 5. Run
python bot.py
```

## Docker

```bash
cp .env.example .env
# paste your BOT_TOKEN into .env

docker compose up -d
docker compose logs -f
```

The database is stored in a Docker volume and survives container restarts.

## Configuration (.env)

| Variable | Default | Description |
|---|---|---|
| `BOT_TOKEN` | — | Token from @BotFather (required) |
| `CHECK_INTERVAL_SECONDS` | `1800` | Check interval in seconds |
| `DB_PATH` | `ipsw_bot.db` | Path to the SQLite database |

## Bot commands

| Command | Description |
|---|---|
| `/start` | Select a device and view signed firmwares |
| `/check` | Same as /start |
| `/list` | Show your active subscriptions |

## Project structure

```
bot.py          — entry point, Telegram handlers
database.py     — SQLite: subscriptions and firmware snapshots
ipsw_api.py     — ipsw.me API client and ipsw.dev scraper
checker.py      — background job: diff detection and notifications
Dockerfile      — container image
docker-compose.yml
```

## Data sources

- **Release**: `https://api.ipsw.me/v4/device/{identifier}?type=ipsw`
- **Beta/Dev**: `https://ipsw.dev/signing/?identifier={identifier}` (HTML scraping)
