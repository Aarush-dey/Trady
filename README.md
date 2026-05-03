# 🎫 Ticket Bot

A powerful Discord ticket bot with self-hosted license key protection.

## Setup on Render

1. Push this repo to GitHub (keep it **private**)
2. Go to [render.com](https://render.com) → New Web Service
3. Connect this GitHub repo
4. Set these environment variables in Render:

| Variable | Description |
|----------|-------------|
| `TICKET_BOT_TOKEN` | Your Discord bot token |
| `KEYS_API_URL` | (Optional) URL to fetch keys from auth bot |

5. Build Command: `pip install -r requirements.txt`
6. Start Command: `python bot.py`

## Activation

Once the bot is in a server, the admin runs:
```
/activate <license_key>
```

## Commands

| Command | Description |
|---------|-------------|
| `/activate <key>` | Activate bot with license key |
| `!ticketpanel` | Send the ticket panel |
| `!setsupportrole <id>` | Set support role |
| `!setlogchannel <id>` | Set log channel |
| `!claim` | Claim a ticket |
| `!close` | Close a ticket |
| `!help` | Show all commands |
