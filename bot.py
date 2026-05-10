import discord
from discord.ext import commands
import logging
import time
import json
import os
import asyncio
import requests
import io
import re
from collections import defaultdict
from keep_alive import keep_alive

logging.getLogger("discord").setLevel(logging.CRITICAL)

# ── Env Vars ──────────────────────────────────────────────────────────────────
TICKET_BOT_TOKEN = os.getenv("TICKET_BOT_TOKEN")
KEYS_API_URL     = os.getenv("KEYS_API_URL", "")
# Example: https://oathmanager.onrender.com/keys?secret=mysecretkey123

# ── Bot ───────────────────────────────────────────────────────────────────────
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None, max_messages=1000)
user_cooldowns = defaultdict(lambda: 0)
COOLDOWN_SECONDS = 2

# ── Files ─────────────────────────────────────────────────────────────────────
CONFIG_FILE  = "ticket_config.json"
TICKETS_FILE = "tickets.json"
SERVERS_FILE = "authorized_servers.json"
KEYS_FILE    = "keys.json"

# ── JSON Helpers ──────────────────────────────────────────────────────────────
def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def get_config():
    return load_json(CONFIG_FILE, {
        "sendmsg_roles": [],
        "ticket_support_roles": [],
        "ticket_free_roles": [],
        "ticket_options": [],
        "ticket_panel_channel": None,
        "ticket_category": None,
        "ticket_log_channel": None,
        "cooked_rich_role": None,
        "ticket_panel_title": "Support Tickets",
        "ticket_panel_description": "Click below to open a support ticket.",
    })

def save_config(cfg):    save_json(CONFIG_FILE, cfg)
def get_tickets():       return load_json(TICKETS_FILE, {})
def save_tickets(t):     save_json(TICKETS_FILE, t)
def get_servers():       return load_json(SERVERS_FILE, {})
def save_servers(s):     save_json(SERVERS_FILE, s)

# ── Key System ────────────────────────────────────────────────────────────────
def load_keys():
    """Fetch keys from Auth Bot API"""
    if KEYS_API_URL:
        try:
            r = requests.get(KEYS_API_URL, timeout=5)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
    return load_json(KEYS_FILE, {})

def verify_key(key: str) -> dict:
    """Verify key — checks if revoked or expired"""
    keys = load_keys()

    # Not found = revoked
    if key not in keys:
        return {
            "valid": False,
            "reason": "revoked",
            "message": "❌ This key has been revoked."
        }

    v = keys[key]

    # Expired
    if v["expiry"] != 0 and time.time() > v["expiry"]:
        return {
            "valid": False,
            "reason": "expired",
            "message": "⌛ This key has expired."
        }

    return {"valid": True, "data": v}

def mark_key_used(key: str, guild_id: str):
    keys = load_json(KEYS_FILE, {})
    if key in keys:
        keys[key]["used"] = True
        keys[key]["used_by_server"] = guild_id
        save_json(KEYS_FILE, keys)

def format_expiry(expiry: float):
    return "Lifetime ♾️" if expiry == 0 else time.strftime("%Y-%m-%d", time.localtime(expiry))

# ── Auth Check — re-verifies key every command ────────────────────────────────
def is_server_authorized(guild_id: int) -> bool:
    servers = get_servers()
    sid     = str(guild_id)

    if sid not in servers:
        return False

    entry = servers[sid]
    key   = entry.get("key")

    if key:
        result = verify_key(key)
        if not result["valid"]:
            # Auto lock — remove server
            del servers[sid]
            save_servers(servers)
            return False

    if entry.get("lifetime"):
        return True

    expiry = entry.get("expiry", 0)
    if expiry != 0 and time.time() > expiry:
        del servers[sid]
        save_servers(servers)
        return False

    return True

def auth_check(ctx):
    return is_server_authorized(ctx.guild.id)

# ── General Helpers ───────────────────────────────────────────────────────────
def has_any_role(member, role_ids):
    if member.guild_permissions.administrator:
        return True
    if not role_ids:
        return False
    guild_roles      = {r.id: r for r in member.guild.roles}
    target_positions = [guild_roles[rid].position for rid in role_ids if rid in guild_roles]
    if not target_positions:
        return False
    highest_target = max(target_positions)
    for r in member.roles:
        if r.id in role_ids or r.position > highest_target:
            return True
    return False

def emb(title, description, color=0x5865F2, footer=None):
    e = discord.Embed(title=title, description=description, color=color)
    if footer:
        e.set_footer(text=footer)
    return e

async def send_not_authorized(target):
    await target.send(embed=discord.Embed(
        title="🔒 Bot Not Activated",
        description=(
            "This bot requires a valid license key.\n\n"
            "**How to activate:**\n"
            "> 1. Purchase a license key\n"
            "> 2. Run `/activate <your_key>` in this server\n\n"
            "Contact the bot owner to get a key."
        ),
        color=0xED4245
    ))

# ── Log Helper ────────────────────────────────────────────────────────────────
async def send_ticket_log(guild, cfg, ticket_data: dict, channel, title: str, color: int):
    log_ch_id = cfg.get("ticket_log_channel")
    if not log_ch_id:
        return
    log_ch = guild.get_channel(int(log_ch_id))
    if not log_ch:
        return
    log_embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
    log_embed.add_field(name="📋 Category",   value=ticket_data.get("option", "N/A"), inline=True)
    log_embed.add_field(name="🎫 Channel",    value=channel.mention if channel else "Deleted", inline=True)
    log_embed.add_field(name="🙋 Opened by",  value=f"<@{ticket_data['creator_id']}> (`{ticket_data.get('creator_name','')}`)", inline=False)
    log_embed.add_field(name="📦 Issue",      value=ticket_data.get("issue", "N/A"), inline=False)
    log_embed.add_field(name="👤 Other User", value=ticket_data.get("other_user_display", "N/A"), inline=True)
    log_embed.add_field(name="📝 Extra Info", value=ticket_data.get("extra_info", "N/A"), inline=True)
    log_embed.add_field(name="🕐 Opened at",  value=ticket_data.get("opened_at", "N/A"), inline=True)
    if ticket_data.get("claimed_by"):
        log_embed.add_field(name="✋ Claimed by", value=f"<@{ticket_data['claimed_by']}> (`{ticket_data.get('claimed_by_name','')}`)", inline=True)
        log_embed.add_field(name="🕑 Claimed at", value=ticket_data.get("claimed_at", "N/A"), inline=True)
    if ticket_data.get("closed_by"):
        log_embed.add_field(name="🔒 Closed by", value=f"<@{ticket_data['closed_by']}> (`{ticket_data.get('closed_by_name','')}`)", inline=True)
        log_embed.add_field(name="🕒 Closed at", value=ticket_data.get("closed_at", "N/A"), inline=True)
    await log_ch.send(embed=log_embed)

async def close_ticket_channel(channel, closer):
    tickets = get_tickets()
    tid     = str(channel.id)
    cfg     = get_config()
    if tid in tickets:
        tickets[tid]["closed_by"]      = str(closer.id)
        tickets[tid]["closed_by_name"] = str(closer)
        tickets[tid]["closed_at"]      = time.strftime("%Y-%m-%d %H:%M:%S")
        save_tickets(tickets)
        await send_ticket_log(channel.guild, cfg, tickets[tid], channel, "🔴 Ticket Closed", 0xED4245)
        del tickets[tid]
        save_tickets(tickets)
    await channel.send(embed=discord.Embed(
        title="🔒 Ticket Closing",
        description=f"Closed by {closer.mention}. Channel deletes in 5 seconds.",
        color=0xED4245
    ))
    await asyncio.sleep(5)
    await channel.delete()

# ── SLASH: /activate ──────────────────────────────────────────────────────────
@bot.tree.command(name="activate", description="Activate the ticket bot with your license key")
async def activate(interaction: discord.Interaction, key: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Only administrators can activate the bot.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=False)

    result = verify_key(key)

    if not result["valid"]:
        reason = result.get("reason", "invalid")
        if reason == "expired":
            await interaction.followup.send(
                embed=discord.Embed(
                    title="⌛ Key Expired",
                    description=f"The key `{key}` has **expired**.\n\nContact the bot owner to get a new key.",
                    color=0xFEE75C
                )
            )
        else:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Invalid Key",
                    description=f"The key `{key}` is **invalid or has been revoked**.\n\nContact the bot owner to get a valid key.",
                    color=0xED4245
                )
            )
        return

    key_data   = result["data"]
    expiry     = key_data["expiry"]
    duration   = key_data["duration"]
    expiry_str = format_expiry(expiry)

    servers = get_servers()
    servers[str(interaction.guild.id)] = {
        "key":          key,
        "activated_by": str(interaction.user.id),
        "activated_at": time.time(),
        "duration":     duration,
        "lifetime":     expiry == 0,
        "expiry":       expiry
    }
    save_servers(servers)
    mark_key_used(key, str(interaction.guild.id))

    await interaction.followup.send(
        embed=discord.Embed(
            title="🔓 Bot Unlocked!",
            description=(
                f"**Trady** has been successfully activated!\n\n"
                f"**Duration:** {duration}\n"
                f"**Expires:** {expiry_str}\n"
                f"**Activated by:** {interaction.user.mention}\n\n"
                f"Run `!help` to see all commands."
            ),
            color=0x57F287
        )
    )

# ── VIEWS ─────────────────────────────────────────────────────────────────────
class TicketPanelView(discord.ui.View):
    def __init__(self, options):
        super().__init__(timeout=None)
        for opt in options:
            self.add_item(TicketOptionButton(opt["label"], opt.get("emoji")))

class TicketOptionButton(discord.ui.Button):
    def __init__(self, label, emoji=None):
        super().__init__(label=label, emoji=emoji or None, style=discord.ButtonStyle.primary, custom_id=f"ticket_opt_{label}")

    async def callback(self, interaction: discord.Interaction):
        if not is_server_authorized(interaction.guild.id):
            await send_not_authorized(interaction.channel); return
        await interaction.response.send_modal(TicketFormModal(self.label))


class TicketFormModal(discord.ui.Modal, title="🎫 Create a Ticket"):
    trade   = discord.ui.TextInput(label="What is your issue / request?", style=discord.TextStyle.paragraph, placeholder="Describe your issue here...", required=True)
    user_id = discord.ui.TextInput(label="@user or User ID (optional)", placeholder="@username or 123456789012345678", required=False)
    extra   = discord.ui.TextInput(label="Any additional info?", placeholder="Extra details...", required=False)

    def __init__(self, option_label):
        super().__init__()
        self.option_label = option_label

    async def on_submit(self, interaction: discord.Interaction):
        cfg    = get_config()
        guild  = interaction.guild
        member = interaction.user
        other_member       = None
        user_not_in_server = False
        raw = self.user_id.value.strip() if self.user_id.value else ""

        if raw:
            cleaned = raw.lstrip("<@!>").rstrip(">").replace("<@","").replace("!","").replace(">","").strip()
            try:
                uid = int(cleaned)
                other_member = guild.get_member(uid) or await guild.fetch_member(uid)
                if other_member is None: user_not_in_server = True
            except ValueError: pass
            except discord.NotFound: user_not_in_server = True
            except Exception: pass

        category = None
        if cfg.get("ticket_category"):
            category = guild.get_channel(int(cfg["ticket_category"]))
        if category:
            try: await category.set_permissions(guild.default_role, read_messages=False)
            except Exception: pass

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        if other_member:
            overwrites[other_member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        for rid in cfg.get("ticket_free_roles", []):
            role = guild.get_role(int(rid))
            if role: overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, use_application_commands=True)
        for rid in cfg.get("ticket_support_roles", []):
            role = guild.get_role(int(rid))
            if role and role not in overwrites:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=False, use_application_commands=False)

        channel = await guild.create_text_channel(name=f"ticket-{member.name}", category=category, overwrites=overwrites)

        if other_member: other_display = other_member.mention
        elif user_not_in_server and raw: other_display = f"⚠️ Not found (`{raw}`)"
        elif raw: other_display = raw
        else: other_display = "N/A"

        tickets = get_tickets()
        tickets[str(channel.id)] = {
            "creator_id": str(member.id), "creator_name": str(member),
            "other_user_id": str(other_member.id) if other_member else None,
            "other_user_display": other_display, "option": self.option_label,
            "issue": self.trade.value, "extra_info": self.extra.value or "N/A",
            "claimed_by": None, "claimed_by_name": None,
            "closed_by": None, "closed_by_name": None,
            "opened_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_tickets(tickets)

        ticket_embed = discord.Embed(title=f"🎫 Ticket — {self.option_label}", color=0x5865F2)
        ticket_embed.add_field(name="📦 Issue", value=self.trade.value, inline=False)
        ticket_embed.add_field(name="👤 Other User", value=other_display, inline=True)
        ticket_embed.add_field(name="📝 Extra Info", value=self.extra.value or "N/A", inline=True)
        ticket_embed.add_field(name="🙋 Opened by", value=member.mention, inline=False)
        ticket_embed.set_footer(text="!claim to claim | !close to close")

        pings = " ".join(guild.get_role(int(rid)).mention for rid in cfg.get("ticket_support_roles", []) if guild.get_role(int(rid)))
        await channel.send(content=pings if pings else None, embed=ticket_embed, view=TicketActionsView())
        await interaction.response.send_message(embed=emb("✅ Ticket Created", f"Your ticket: {channel.mention}", color=0x57F287), ephemeral=True)
        await send_ticket_log(guild, cfg, tickets[str(channel.id)], channel, "🟢 Ticket Opened", 0x57F287)


class TicketActionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.success, emoji="✋", custom_id="claim_ticket")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_config()
        if not has_any_role(interaction.user, [int(r) for r in cfg.get("ticket_support_roles", [])]):
            await interaction.response.send_message(embed=emb("❌ No Permission", "Only support roles can claim.", color=0xED4245), ephemeral=True); return
        tickets = get_tickets(); tid = str(interaction.channel.id)
        if tid not in tickets:
            await interaction.response.send_message(embed=emb("❌ Error", "Ticket data not found.", color=0xED4245), ephemeral=True); return
        if tickets[tid]["claimed_by"]:
            claimer = interaction.guild.get_member(int(tickets[tid]["claimed_by"]))
            await interaction.response.send_message(embed=emb("⚠️ Already Claimed", f"By {claimer.mention if claimer else 'someone'}.", color=0xFEE75C), ephemeral=True); return
        tickets[tid]["claimed_by"] = str(interaction.user.id)
        tickets[tid]["claimed_by_name"] = str(interaction.user)
        tickets[tid]["claimed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_tickets(tickets)
        await interaction.channel.set_permissions(interaction.user, read_messages=True, send_messages=True, use_application_commands=True)
        button.disabled = True; button.label = f"Claimed by {interaction.user.display_name}"
        await interaction.message.edit(view=self)
        await interaction.response.send_message(embed=emb("✅ Claimed", f"{interaction.user.mention} claimed this ticket!", color=0x57F287))
        await send_ticket_log(interaction.guild, cfg, tickets[tid], interaction.channel, "🟡 Ticket Claimed", 0xFEE75C)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="close_ticket_btn")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_config()
        if not has_any_role(interaction.user, [int(r) for r in cfg.get("ticket_support_roles", [])]):
            await interaction.response.send_message(embed=emb("❌ No Permission", "Only support roles can close.", color=0xED4245), ephemeral=True); return
        await interaction.response.defer()
        await close_ticket_channel(interaction.channel, interaction.user)


class ConfirmTradeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success, custom_id="confirm_trade")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        tickets = get_tickets(); tid = str(interaction.channel.id)
        if tid not in tickets:
            await interaction.response.send_message(embed=emb("❌ Error", "Ticket not found.", color=0xED4245), ephemeral=True); return
        uid = str(interaction.user.id)
        if uid in tickets[tid].get("confirm_users", []):
            await interaction.response.send_message(embed=emb("⚠️ Already Confirmed", "You already confirmed.", color=0xFEE75C), ephemeral=True); return
        tickets[tid].setdefault("confirm_users", []).append(uid)
        save_tickets(tickets)
        await interaction.response.send_message(f"✅ {interaction.user.mention} **confirmed!**")


class CookedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="💰 I Wanna Be Rich", style=discord.ButtonStyle.success, custom_id="cooked_rich")
    async def rich_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_config(); rich_role_id = cfg.get("cooked_rich_role")
        if rich_role_id:
            role = interaction.guild.get_role(int(rich_role_id))
            if role:
                try: await interaction.user.add_roles(role)
                except Exception: pass
        await interaction.response.send_message(f"{interaction.user.mention} choose to be rich 💰", allowed_mentions=discord.AllowedMentions(users=True))

    @discord.ui.button(label="😴 I Wanna Stay Pooron", style=discord.ButtonStyle.danger, custom_id="cooked_poor")
    async def poor_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"{interaction.user.mention} choose to stay pooron 😴", allowed_mentions=discord.AllowedMentions(users=True))

# ── COMMANDS ──────────────────────────────────────────────────────────────────

@bot.command(name="ticketpanel")
@commands.has_permissions(administrator=True)
async def ticketpanel(ctx, image_url: str = None):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    cfg = get_config(); options = cfg.get("ticket_options", [])
    if not options:
        await ctx.send(embed=emb("❌ No Options", "Add options with `!addticketoption <label> [emoji]`.", color=0xED4245)); return
    target_channel = ctx.channel
    panel_ch_id = cfg.get("ticket_panel_channel")
    if panel_ch_id:
        found = ctx.guild.get_channel(int(panel_ch_id))
        if found: target_channel = found
    panel_embed = discord.Embed(title=cfg.get("ticket_panel_title", "Support Tickets"), description=cfg.get("ticket_panel_description", "Click below to open a ticket."), color=0x5865F2)
    panel_embed.set_footer(text="One ticket per issue please.")
    file_to_send = None
    if ctx.message.attachments:
        att = ctx.message.attachments[0]
        if any(att.filename.lower().endswith(ext) for ext in [".png",".jpg",".jpeg",".gif",".webp"]):
            file_bytes = await att.read()
            file_to_send = discord.File(io.BytesIO(file_bytes), filename=att.filename)
            panel_embed.set_image(url=f"attachment://{att.filename}")
    elif image_url: panel_embed.set_image(url=image_url)
    elif cfg.get("ticket_panel_image_url"): panel_embed.set_image(url=cfg["ticket_panel_image_url"])
    view = TicketPanelView(options)
    if file_to_send:
        sent = await target_channel.send(embed=panel_embed, view=view, file=file_to_send)
        if sent.embeds and sent.embeds[0].image:
            cfg["ticket_panel_image_url"] = sent.embeds[0].image.url; save_config(cfg)
    else: await target_channel.send(embed=panel_embed, view=view)
    if target_channel != ctx.channel:
        await ctx.send(embed=emb("✅ Panel Sent", f"Ticket panel sent to {target_channel.mention}.", color=0x57F287))


@bot.command(name="setlogchannel")
@commands.has_permissions(administrator=True)
async def setlogchannel(ctx, channel_id: str):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    ch = ctx.guild.get_channel(int(channel_id))
    if not ch: await ctx.send(embed=emb("❌ Error", "Channel not found.", color=0xED4245)); return
    cfg = get_config(); cfg["ticket_log_channel"] = channel_id; save_config(cfg)
    await ctx.send(embed=emb("✅ Log Channel Set", f"Ticket logs → {ch.mention}.", color=0x57F287))


@bot.command(name="setsupportrole")
@commands.has_permissions(administrator=True)
async def setsupportrole(ctx, role_id: str):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    cfg = get_config()
    if role_id not in cfg["ticket_support_roles"]: cfg["ticket_support_roles"].append(role_id); save_config(cfg)
    await ctx.send(embed=emb("✅ Updated", f"Role `{role_id}` is now a support role.", color=0x57F287))


@bot.command(name="setfreerole")
@commands.has_permissions(administrator=True)
async def setfreerole(ctx, role_id: str):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    cfg = get_config()
    if role_id not in cfg["ticket_free_roles"]: cfg["ticket_free_roles"].append(role_id); save_config(cfg)
    await ctx.send(embed=emb("✅ Updated", f"Role `{role_id}` can msg without claiming.", color=0x57F287))


@bot.command(name="setsendrole")
@commands.has_permissions(administrator=True)
async def setsendrole(ctx, role_id: str):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    cfg = get_config(); cfg.setdefault("sendmsg_roles", [])
    if role_id not in cfg["sendmsg_roles"]: cfg["sendmsg_roles"].append(role_id); save_config(cfg)
    await ctx.send(embed=emb("✅ Updated", f"Role `{role_id}` can use `!sendmsg`.", color=0x57F287))


@bot.command(name="setticketcategory")
@commands.has_permissions(administrator=True)
async def setticketcategory(ctx, category_id: str):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    cfg = get_config(); cfg["ticket_category"] = category_id; save_config(cfg)
    category = ctx.guild.get_channel(int(category_id))
    if category:
        try:
            await category.set_permissions(ctx.guild.default_role, read_messages=False)
            await ctx.send(embed=emb("✅ Updated", "Ticket category set and hidden.", color=0x57F287))
        except Exception as e: await ctx.send(embed=emb("✅ Updated", f"Set but couldn't hide: `{e}`", color=0xFEE75C))
    else: await ctx.send(embed=emb("✅ Updated", "Ticket category set.", color=0x57F287))


@bot.command(name="setpanelchannel")
@commands.has_permissions(administrator=True)
async def setpanelchannel(ctx, channel_id: str):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    ch = ctx.guild.get_channel(int(channel_id))
    if not ch: await ctx.send(embed=emb("❌ Error", "Channel not found.", color=0xED4245)); return
    cfg = get_config(); cfg["ticket_panel_channel"] = channel_id; save_config(cfg)
    await ctx.send(embed=emb("✅ Panel Channel Set", f"Panel → {ch.mention}.", color=0x57F287))


@bot.command(name="setpanelmsg")
@commands.has_permissions(administrator=True)
async def setpanelmsg(ctx, *, content: str = None):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    if not content or "|" not in content:
        await ctx.send(embed=emb("❌ Usage", "`!setpanelmsg <title> | <description>`", color=0xED4245)); return
    parts = content.split("|", 1); cfg = get_config()
    cfg["ticket_panel_title"] = parts[0].strip(); cfg["ticket_panel_description"] = parts[1].strip(); save_config(cfg)
    await ctx.send(embed=emb("✅ Updated", "Panel title & description updated.", color=0x57F287))


@bot.command(name="addticketoption")
@commands.has_permissions(administrator=True)
async def addticketoption(ctx, label: str, emoji: str = None):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    cfg = get_config(); cfg["ticket_options"].append({"label": label, "emoji": emoji}); save_config(cfg)
    await ctx.send(embed=emb("✅ Option Added", f"Button `{emoji or ''} {label}` added.", color=0x57F287))


@bot.command(name="clearticketoptions")
@commands.has_permissions(administrator=True)
async def clearticketoptions(ctx):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    cfg = get_config(); cfg["ticket_options"] = []; save_config(cfg)
    await ctx.send(embed=emb("✅ Cleared", "All ticket options removed.", color=0x57F287))


@bot.command(name="claim")
async def claim(ctx):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    cfg = get_config()
    if not has_any_role(ctx.author, [int(r) for r in cfg.get("ticket_support_roles", [])]):
        await ctx.send(embed=emb("❌ No Permission", "Only support roles can claim.", color=0xED4245)); return
    tickets = get_tickets(); tid = str(ctx.channel.id)
    if tid not in tickets: await ctx.send(embed=emb("❌ Error", "Not a ticket channel.", color=0xED4245)); return
    if tickets[tid]["claimed_by"]:
        claimer = ctx.guild.get_member(int(tickets[tid]["claimed_by"]))
        await ctx.send(embed=emb("⚠️ Already Claimed", f"By {claimer.mention if claimer else 'someone'}.", color=0xFEE75C)); return
    tickets[tid]["claimed_by"] = str(ctx.author.id); tickets[tid]["claimed_by_name"] = str(ctx.author)
    tickets[tid]["claimed_at"] = time.strftime("%Y-%m-%d %H:%M:%S"); save_tickets(tickets)
    await ctx.channel.set_permissions(ctx.author, read_messages=True, send_messages=True, use_application_commands=True)
    await ctx.send(embed=emb("✅ Claimed", f"{ctx.author.mention} claimed this ticket!", color=0x57F287))
    await send_ticket_log(ctx.guild, cfg, tickets[tid], ctx.channel, "🟡 Ticket Claimed", 0xFEE75C)


@bot.command(name="unclaim")
async def unclaim(ctx):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    cfg = get_config()
    if not has_any_role(ctx.author, [int(r) for r in cfg.get("ticket_support_roles", [])]):
        await ctx.send(embed=emb("❌ No Permission", "Only support roles can unclaim.", color=0xED4245)); return
    tickets = get_tickets(); tid = str(ctx.channel.id)
    if tid not in tickets: await ctx.send(embed=emb("❌ Error", "Not a ticket channel.", color=0xED4245)); return
    if not tickets[tid]["claimed_by"]: await ctx.send(embed=emb("⚠️ Not Claimed", "Not claimed yet.", color=0xFEE75C)); return
    tickets[tid]["claimed_by"] = None; tickets[tid]["claimed_by_name"] = None; save_tickets(tickets)
    await ctx.channel.set_permissions(ctx.author, read_messages=True, send_messages=False, use_application_commands=False)
    pings = " ".join(ctx.guild.get_role(int(rid)).mention for rid in cfg.get("ticket_support_roles", []) if ctx.guild.get_role(int(rid)))
    await ctx.send(content=pings if pings else None, embed=discord.Embed(title="🔓 Ticket Unclaimed", description=f"{ctx.author.mention} unclaimed. Needs new support!", color=0xFEE75C))


@bot.command(name="close")
async def close(ctx):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    cfg = get_config()
    if not has_any_role(ctx.author, [int(r) for r in cfg.get("ticket_support_roles", [])]):
        await ctx.send(embed=emb("❌ No Permission", "Only support roles can close.", color=0xED4245)); return
    tickets = get_tickets()
    if str(ctx.channel.id) not in tickets: await ctx.send(embed=emb("❌ Error", "Not a ticket channel.", color=0xED4245)); return
    await close_ticket_channel(ctx.channel, ctx.author)


@bot.command(name="adduser")
async def adduser(ctx, *, user_input: str):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("ticket_support_roles", [])] + [int(r) for r in cfg.get("ticket_free_roles", [])]
    if not has_any_role(ctx.author, allowed): await ctx.send(embed=emb("❌ No Permission", "You don't have permission.", color=0xED4245)); return
    tickets = get_tickets()
    if str(ctx.channel.id) not in tickets: await ctx.send(embed=emb("❌ Error", "Use in a ticket channel.", color=0xED4245)); return
    member = None
    cleaned = user_input.strip().replace("<@","").replace("!","").replace(">","").strip()
    try:
        uid = int(cleaned); member = ctx.guild.get_member(uid) or await ctx.guild.fetch_member(uid)
    except ValueError:
        search = user_input.strip().lower().lstrip("@")
        member = discord.utils.find(lambda m: m.name.lower() == search or m.display_name.lower() == search, ctx.guild.members)
        if not member: member = discord.utils.find(lambda m: search in m.name.lower() or search in m.display_name.lower(), ctx.guild.members)
    except discord.NotFound: pass
    if not member: await ctx.send(embed=emb("❌ Not Found", f"Could not find `{user_input}`.", color=0xED4245)); return
    await ctx.channel.set_permissions(member, read_messages=True, send_messages=True)
    await ctx.send(embed=emb("✅ User Added", f"{member.mention} added.", color=0x57F287))


@bot.command(name="confirmtrade")
async def confirmtrade(ctx):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("ticket_support_roles", [])] + [int(r) for r in cfg.get("ticket_free_roles", [])]
    if not has_any_role(ctx.author, allowed): await ctx.send(embed=emb("❌ No Permission", "You don't have permission.", color=0xED4245)); return
    tickets = get_tickets()
    if str(ctx.channel.id) not in tickets: await ctx.send(embed=emb("❌ Error", "Use in a ticket channel.", color=0xED4245)); return
    await ctx.send(embed=discord.Embed(title="🤝 Confirm?", description="Click **Confirm** below.", color=0xFEE75C), view=ConfirmTradeView())


@bot.command(name="cooked")
async def cooked(ctx):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    cfg = get_config()
    if not has_any_role(ctx.author, [int(r) for r in cfg.get("ticket_support_roles", [])]):
        await ctx.send(embed=emb("❌ No Permission", "Only support roles.", color=0xED4245)); return
    cooked_embed = discord.Embed(title="⚠️ YOU ARE SCAMMED ⚠️", color=0xED4245)
    cooked_embed.add_field(name="1️⃣ Join us to recover", value="Use this server to get back what you lost.", inline=False)
    cooked_embed.add_field(name="2️⃣ Open a ticket", value="Open a support ticket.", inline=False)
    cooked_embed.add_field(name="3️⃣ We handle it", value="We split **50-50**.", inline=False)
    cooked_embed.add_field(name="🚀 TAP BELOW", value='**"I WANNA BE RICH"**', inline=False)
    await ctx.send(embed=cooked_embed, view=CookedView())


@bot.command(name="sendmsg")
async def sendmsg(ctx, channel_id: str = None, *, message: str = None):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    cfg = get_config()
    if not has_any_role(ctx.author, [int(r) for r in cfg.get("sendmsg_roles", [])]):
        await ctx.send(embed=emb("❌ No Permission", "You don't have permission.", color=0xED4245)); return
    if not channel_id or not message:
        await ctx.send(embed=emb("❌ Usage", "`!sendmsg <channel_id> <message>`", color=0xED4245)); return
    try:
        ch = ctx.guild.get_channel(int(channel_id))
        if not ch: await ctx.send(embed=emb("❌ Error", "Channel not found.", color=0xED4245)); return
        links = re.findall(r'https?://\S+', message)
        msg_embed = discord.Embed(description=message, color=0x5865F2)
        file_to_send = None
        if ctx.message.attachments:
            att = ctx.message.attachments[0]
            if any(att.filename.lower().endswith(ext) for ext in [".png",".jpg",".jpeg",".gif",".webp"]):
                file_bytes = await att.read()
                file_to_send = discord.File(io.BytesIO(file_bytes), filename=att.filename)
                msg_embed.set_image(url=f"attachment://{att.filename}")
        if file_to_send: await ch.send(embed=msg_embed, file=file_to_send)
        else: await ch.send(embed=msg_embed)
        for link in links: await ch.send(f"|| {link} ||")
        await ctx.send(embed=emb("✅ Sent", f"Message sent to {ch.mention}.", color=0x57F287))
    except Exception as e: await ctx.send(embed=emb("❌ Error", str(e), color=0xED4245))


@bot.command(name="setcookedrole")
@commands.has_permissions(administrator=True)
async def setcookedrole(ctx, role_id: str):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    cfg = get_config(); role = ctx.guild.get_role(int(role_id))
    if not role: await ctx.send(embed=emb("❌ Error", "Role not found.", color=0xED4245)); return
    cfg["cooked_rich_role"] = role_id; save_config(cfg)
    await ctx.send(embed=emb("✅ Updated", f"{role.mention} → 'I Wanna Be Rich' clickers.", color=0x57F287))


@bot.command(name="mminfoeng")
async def mminfoeng(ctx):
    if not auth_check(ctx): await send_not_authorized(ctx); return
    cfg = get_config()
    allowed = [int(r) for r in cfg.get("ticket_support_roles",[])] + [int(r) for r in cfg.get("ticket_free_roles",[])]
    if not has_any_role(ctx.author, allowed): await ctx.send(embed=emb("❌ No Permission", "You don't have permission.", color=0xED4245)); return
    info_embed = discord.Embed(title="🛡️ How This MM Deal Works", color=0x5865F2)
    info_embed.add_field(name="1️⃣ Item Secured",   value="Seller gives item to MM. MM confirms receipt.", inline=False)
    info_embed.add_field(name="2️⃣ Direct Payment", value="Buyer sends PayPal to Seller (Friends & Family).", inline=False)
    info_embed.add_field(name="3️⃣ Proof",          value="Buyer sends screenshot. Seller confirms.", inline=False)
    info_embed.add_field(name="4️⃣ Item Release",   value="Seller confirms received → MM gives item to Buyer.", inline=False)
    info_embed.add_field(name="5️⃣ Done",           value="MM leaves. Trade complete.", inline=False)
    await ctx.send(embed=info_embed)


@bot.command(name="help")
async def help_cmd(ctx):
    h = discord.Embed(title="📖 Ticket Bot Commands", description="Prefix: `!`", color=0x5865F2)
    h.add_field(name="🔐 Activation", value="`/activate <key>` — Activate bot with license key", inline=False)
    h.add_field(name="🔧 Setup (Admin)", value="""
`!setsupportrole <id>` — Set support role
`!setfreerole <id>` — Role that can msg without claiming
`!setsendrole <id>` — Allow role to use `!sendmsg`
`!setticketcategory <id>` — Set ticket category
`!setpanelchannel <id>` — Set panel channel
`!setlogchannel <id>` — Set log channel
`!setpanelmsg <title> | <desc>` — Set panel text
`!addticketoption <label> [emoji]` — Add ticket button
`!clearticketoptions` — Remove all buttons
`!ticketpanel [image_url]` — Send ticket panel
`!setcookedrole <id>` — Role for "I Wanna Be Rich"
""", inline=False)
    h.add_field(name="🎫 Ticket Commands (Support)", value="""
`!claim` — Claim a ticket
`!unclaim` — Unclaim ticket
`!close` — Close & delete ticket
`!adduser <@user or ID>` — Add user to ticket
`!confirmtrade` — Confirm trade buttons
`!cooked` — Scam recovery embed
`!mminfoeng` — MM info (English)
`!sendmsg <ch_id> <msg>` — Send message to channel
""", inline=False)
    h.set_footer(text="Requires /activate <key> to unlock | Revoked key = auto locked")
    await ctx.send(embed=h)

# ── EVENTS ────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
     try:
        with open("avatar.gif", "rb") as f:
            await bot.user.edit(avatar=f.read())
        print("✅ Avatar set!")
    except Exception as e:
        print(f"Avatar error: {e}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Sync error: {e}")
    bot.add_view(TicketActionsView())
    bot.add_view(ConfirmTradeView())
    bot.add_view(CookedView())
    cfg = get_config()
    if cfg.get("ticket_options"):
        bot.add_view(TicketPanelView(cfg["ticket_options"]))
    print(f"✅ Ticket Bot logged in as {bot.user}")
    if KEYS_API_URL:
        print(f"✅ Keys API connected: {KEYS_API_URL}")
    else:
        print("⚠️ KEYS_API_URL not set — using local keys.json")


@bot.event
async def on_message(message):
    if message.author.bot: return
    uid = message.author.id
    now = time.time()
    if now - user_cooldowns[uid] < COOLDOWN_SECONDS: return
    user_cooldowns[uid] = now
    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound): return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(embed=emb("❌ No Permission", "You need Administrator permission.", color=0xED4245)); return
    print(f"Error: {error}")

# ── RUN ───────────────────────────────────────────────────────────────────────
keep_alive()
bot.run(TICKET_BOT_TOKEN)
