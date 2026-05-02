# 🎫 Ticket Bot

A powerful Discord ticket bot with KeyAuth license protection.

## Setup on Render

1. Fork or clone this repo
2. Go to [render.com](https://render.com) and create a new **Web Service**
3. Connect this GitHub repo
4. Set the following environment variables in Render:

| Variable | Description |
|----------|-------------|
| `TICKET_BOT_TOKEN` | Your Discord bot token |
| `KEYAUTH_APP_NAME` | Your KeyAuth app name |
| `KEYAUTH_OWNER_ID` | Your KeyAuth owner ID |

5. Build Command: `pip install -r requirements.txt`
6. Start Command: `python bot.py`

## Activation

Once the bot is in your server, run:
```
/activate <your_license_key>
```

## Commands

| Command | Description |
|---------|-------------|
| `/activate <key>` | Activate the bot with your license key |
| `!ticketpanel` | Send the ticket panel |
| `!setsupportrole <id>` | Set support role |
| `!setlogchannel <id>` | Set log channel |
| `!claim` | Claim a ticket |
| `!close` | Close a ticket |
| `!help` | Show all commands |
