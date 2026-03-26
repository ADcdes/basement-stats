import discord
from discord.ext import commands
import json
import os
from datetime import datetime
import git
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# ── Load .env ──────────────────────────────────────────────────────────────────
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# ── Config ─────────────────────────────────────────────────────────────────────
GAMEMODES = ["LTMs", "Vanilla", "UHC", "Pot", "NethOP", "SMP", "Sword", "Axe", "Mace"]
INITIAL_ELO = 1000
ELO_MIN = 0
ELO_MAX = 9999

TIER_THRESHOLDS = {
    "HT1": 2400,
    "LT1": 2200,
    "HT2": 2000,
    "LT2": 1800,
    "HT3": 1600,
    "LT3": 1400,
    "HT4": 1300,
    "LT4": 1200,
    "HT5": 1100,
    "LT5": 0
}

# ── Bot Setup ──────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Thread pool for blocking git operations so the bot doesn't freeze
executor = ThreadPoolExecutor(max_workers=2)

# ── Git Sync (Non-blocking) ────────────────────────────────────────────────────
def _git_push_blocking():
    """Runs in a separate thread so it doesn't freeze the bot."""
    repo = git.Repo(os.getcwd())
    repo.git.add('players.json')
    repo.index.commit(f"Sentinel Auto-Update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    origin = repo.remote(name='origin')
    origin.push()

async def sync_to_web():
    """Async wrapper — pushes players.json to GitHub in background."""
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(executor, _git_push_blocking)
        print("🌐 [Web Sync] Pushed updates to GitHub.")
    except Exception as e:
        print(f"⚠️ [Web Sync] Failed: {e}")

# ── Data Helpers ───────────────────────────────────────────────────────────────
def load_data():
    if not os.path.exists('players.json'):
        return {"players": {}, "history": []}
    with open('players.json', 'r') as f:
        return json.load(f)

def save_data(data):
    # Cap history at 500 so the file never bloats
    if len(data.get("history", [])) > 500:
        data["history"] = data["history"][-500:]
    with open('players.json', 'w') as f:
        json.dump(data, f, indent=4)

# ── Tier Logic ─────────────────────────────────────────────────────────────────
def get_tier(elo: int) -> str:
    for tier, threshold in TIER_THRESHOLDS.items():
        if elo >= threshold:
            return tier
    return "LT5"

def get_best_tier(elo_dict: dict) -> str:
    """Returns the best (highest) tier across all gamemodes."""
    return get_tier(max(elo_dict.values()))

async def update_tier_roles(member: discord.Member, elo: int):
    """Strips all tier roles and applies the correct one for this ELO."""
    target_tier = get_tier(elo)
    tier_names = list(TIER_THRESHOLDS.keys())

    roles_to_remove = [
        discord.utils.get(member.guild.roles, name=n)
        for n in tier_names
        if discord.utils.get(member.guild.roles, name=n) in member.roles
    ]
    new_role = discord.utils.get(member.guild.roles, name=target_tier)

    if roles_to_remove:
        await member.remove_roles(*roles_to_remove)
    if new_role:
        await member.add_roles(new_role)

# ── Events ─────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Sentinel is online. Monitoring Fraktu's Basement.")

@bot.event
async def on_member_join(member: discord.Member):
    """Auto-assign 'Unverified' to anyone who joins."""
    unverified = discord.utils.get(member.guild.roles, name="Unverified")
    if unverified:
        await member.add_roles(unverified)
        print(f"[Sentinel] Assigned Unverified → {member.name}")

# ── Commands ───────────────────────────────────────────────────────────────────

@bot.command()
async def help(ctx):
    embed = discord.Embed(title="🛡️ Sentinel System Manual", color=discord.Color.dark_grey())
    player_cmds = (
        "`!profile [@user]` — Stats & tiers for a player\n"
        "`!stats [@user] [mode]` — Match history (optionally filtered by mode)\n"
        "`!recent` — Last 10 duels across all modes\n"
        "`!leaderboard [mode]` — Top 10 rankings (default: Vanilla)"
    )
    embed.add_field(name="👤 Player Commands", value=player_cmds, inline=False)

    if ctx.author.guild_permissions.manage_messages:
        staff_cmds = (
            "`!verify @user [IGN]` — Onboard a player\n"
            "`!report @winner @loser [mode]` — Log a match result\n"
            "`!undo` — Reverse the last logged match\n"
            "`!setelo @user [mode] [elo]` — Force-set ELO (Admin / Tier Verifier only)"
        )
        embed.add_field(name="🛠️ Staff Operations", value=staff_cmds, inline=False)

    modes_list = ", ".join(f"`{m}`" for m in GAMEMODES)
    embed.add_field(name="🎮 Available Modes", value=modes_list, inline=False)
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def verify(ctx, member: discord.Member, ign: str):
    data = load_data()
    uid = str(member.id)
    data["players"][uid] = {
        "name": member.name,
        "ign": ign,
        "elo": {gm: INITIAL_ELO for gm in GAMEMODES}
    }
    save_data(data)

    # Role swap: Unverified → Verified
    unverified = discord.utils.get(member.guild.roles, name="Unverified")
    verified   = discord.utils.get(member.guild.roles, name="Verified")
    if unverified and unverified in member.roles:
        await member.remove_roles(unverified)
    if verified:
        await member.add_roles(verified)

    # Give starting tier role (LT5 at 1000 ELO)
    await update_tier_roles(member, INITIAL_ELO)
    await sync_to_web()
    await ctx.send(f"✅ **{member.name}** verified as `{ign}`. Welcome to the Basement!")


@bot.command()
@commands.has_permissions(manage_messages=True)
async def report(ctx, winner: discord.Member, loser: discord.Member, gamemode: str):
    mode = next((m for m in GAMEMODES if m.lower() == gamemode.lower()), None)
    if not mode:
        return await ctx.send(f"⚠️ Invalid mode! Choose from: {', '.join(GAMEMODES)}")

    data = load_data()
    w_uid, l_uid = str(winner.id), str(loser.id)

    if w_uid not in data["players"] or l_uid not in data["players"]:
        return await ctx.send("⚠️ Both players must be verified first!")

    w_elo = data["players"][w_uid]['elo'][mode]
    l_elo = data["players"][l_uid]['elo'][mode]

    # Standard Elo (K=32)
    gain = round(32 * (1 - (1 / (1 + 10 ** ((l_elo - w_elo) / 400)))))

    # Clamp results to valid range
    new_w_elo = max(ELO_MIN, min(ELO_MAX, w_elo + gain))
    new_l_elo = max(ELO_MIN, min(ELO_MAX, l_elo - gain))

    data["players"][w_uid]['elo'][mode] = new_w_elo
    data["players"][l_uid]['elo'][mode] = new_l_elo

    # Store pre-match ELOs and player IDs so !undo can reverse this
    data["history"].append({
        "winner_id":    w_uid,
        "loser_id":     l_uid,
        "winner":       data["players"][w_uid]['ign'],
        "loser":        data["players"][l_uid]['ign'],
        "mode":         mode,
        "gain":         gain,
        "w_elo_before": w_elo,
        "l_elo_before": l_elo,
        "time":         datetime.now().strftime("%m/%d %H:%M")
    })
    save_data(data)

    # Update tier roles for BOTH players in this mode
    await update_tier_roles(winner, new_w_elo)
    await update_tier_roles(loser,  new_l_elo)
    await sync_to_web()

    w_tier = get_tier(new_w_elo)
    l_tier = get_tier(new_l_elo)
    await ctx.send(
        f"⚔️ **{data['players'][w_uid]['ign']}** beat **{data['players'][l_uid]['ign']}** "
        f"in {mode} (+{gain} ELO)\n"
        f"📊 {data['players'][w_uid]['ign']}: {new_w_elo} ({w_tier}) | "
        f"{data['players'][l_uid]['ign']}: {new_l_elo} ({l_tier})"
    )


@bot.command()
async def profile(ctx, member: discord.Member = None):
    member = member or ctx.author
    data = load_data()
    uid = str(member.id)
    if uid not in data["players"]:
        return await ctx.send("⚠️ This player is not verified!")

    p = data["players"][uid]
    best = get_best_tier(p['elo'])

    embed = discord.Embed(title=f"👤 {p['ign']}'s Profile", color=0x2ecc71)
    embed.set_thumbnail(url=f"https://mc-heads.net/body/{p['ign']}")
    embed.set_footer(text=f"Best Tier: {best}")

    stats = "\n".join(
        [f"**{m}:** {p['elo'][m]} ({get_tier(p['elo'][m])})" for m in GAMEMODES]
    )
    embed.add_field(name="Elo Breakdown", value=stats, inline=False)
    await ctx.send(embed=embed)


@bot.command()
async def recent(ctx):
    data = load_data()
    if not data["history"]:
        return await ctx.send("No matches logged yet!")
    history = data["history"][-10:][::-1]
    log = "\n".join(
        [f"**{m['winner']}** beat **{m['loser']}** in {m['mode']} (+{m['gain']})" for m in history]
    )
    await ctx.send(embed=discord.Embed(title="📜 Recent Duels", description=log, color=0x3498db))


@bot.command()
async def leaderboard(ctx, mode: str = "Vanilla"):
    gm = next((m for m in GAMEMODES if m.lower() == mode.lower()), "Vanilla")
    data = load_data()
    sorted_p = sorted(data["players"].items(), key=lambda x: x[1]['elo'][gm], reverse=True)[:10]
    lb = "\n".join(
        [f"{i+1}. **{v['ign']}** — {v['elo'][gm]} ELO ({get_tier(v['elo'][gm])})"
         for i, (k, v) in enumerate(sorted_p)]
    )
    await ctx.send(embed=discord.Embed(title=f"🏆 {gm} Leaderboard", description=lb, color=0xf1c40f))


@bot.command()
async def stats(ctx, member: discord.Member = None, mode: str = None):
    """Show match history for a player, optionally filtered by mode."""
    member = member or ctx.author
    data = load_data()
    uid = str(member.id)
    if uid not in data["players"]:
        return await ctx.send("⚠️ This player is not verified!")

    ign = data["players"][uid]['ign']
    matches = [m for m in data["history"] if m['winner'] == ign or m['loser'] == ign]

    gm_label = None
    if mode:
        gm_label = next((m for m in GAMEMODES if m.lower() == mode.lower()), None)
        if gm_label:
            matches = [m for m in matches if m['mode'] == gm_label]

    if not matches:
        suffix = f" in **{gm_label}**" if gm_label else ""
        return await ctx.send(f"No match history found for **{ign}**{suffix}.")

    wins   = sum(1 for m in matches if m['winner'] == ign)
    losses = len(matches) - wins
    recent = matches[-10:][::-1]

    log = "\n".join([
        f"{'✅' if m['winner'] == ign else '❌'} vs **{m['loser'] if m['winner'] == ign else m['winner']}** "
        f"in {m['mode']} ({'+'if m['winner']==ign else '-'}{m['gain']}) — {m['time']}"
        for m in recent
    ])

    embed = discord.Embed(
        title=f"📊 {ign}'s History{f' — {gm_label}' if gm_label else ''}",
        color=0x9b59b6
    )
    embed.add_field(name="Overall Record", value=f"**{wins}W / {losses}L**", inline=True)
    winrate = round((wins / len(matches)) * 100) if matches else 0
    embed.add_field(name="Win Rate", value=f"**{winrate}%**", inline=True)
    embed.add_field(name="Last 10 Matches", value=log, inline=False)
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(manage_messages=True)
async def undo(ctx):
    """Reverse the last logged match entirely."""
    data = load_data()
    if not data["history"]:
        return await ctx.send("⚠️ No matches to undo!")

    last = data["history"][-1]

    # Old matches (before this update) won't have the necessary fields
    if "w_elo_before" not in last or "winner_id" not in last:
        return await ctx.send(
            "⚠️ This match was logged before undo support. "
            "Reverse it manually with `!setelo`."
        )

    w_uid = last["winner_id"]
    l_uid = last["loser_id"]

    if w_uid not in data["players"] or l_uid not in data["players"]:
        return await ctx.send("⚠️ One or both players no longer exist in the system.")

    # Restore pre-match ELOs
    data["players"][w_uid]['elo'][last['mode']] = last['w_elo_before']
    data["players"][l_uid]['elo'][last['mode']] = last['l_elo_before']
    data["history"].pop()
    save_data(data)

    # Update tier roles back
    w_member = ctx.guild.get_member(int(w_uid))
    l_member = ctx.guild.get_member(int(l_uid))
    if w_member:
        await update_tier_roles(w_member, last['w_elo_before'])
    if l_member:
        await update_tier_roles(l_member, last['l_elo_before'])

    await sync_to_web()
    await ctx.send(
        f"↩️ Undone: **{last['winner']}** vs **{last['loser']}** in {last['mode']} "
        f"(reversed +{last['gain']} ELO)."
    )


@bot.command()
async def setelo(ctx, member: discord.Member, gamemode: str, elo: int):
    is_admin    = ctx.author.guild_permissions.administrator
    is_owner    = ctx.author.id == ctx.guild.owner_id
    is_verifier = discord.utils.get(ctx.author.roles, name="Tier Verifier") is not None

    if not (is_admin or is_owner or is_verifier):
        return await ctx.send("⛔ You need the 'Tier Verifier' role or Admin permissions.")

    mode = next((m for m in GAMEMODES if m.lower() == gamemode.lower()), None)
    if not mode:
        return await ctx.send(f"⚠️ Invalid mode! Choose from: {', '.join(GAMEMODES)}")

    # Clamp ELO to valid range
    elo = max(ELO_MIN, min(ELO_MAX, elo))

    data = load_data()
    uid = str(member.id)
    if uid not in data["players"]:
        return await ctx.send("⚠️ This player is not verified yet!")

    data["players"][uid]['elo'][mode] = elo
    save_data(data)

    # Always update tier roles regardless of mode
    await update_tier_roles(member, elo)
    await sync_to_web()

    tier = get_tier(elo)
    await ctx.send(
        f"✅ Set **{data['players'][uid]['ign']}** to **{elo} ELO** ({tier}) in {mode}."
    )

# ── Run ────────────────────────────────────────────────────────────────────────
bot.run(TOKEN)