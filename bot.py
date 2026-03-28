import discord
from discord.ext import commands
import json
import os
from datetime import datetime
import git
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# ══════════════════════════════════════════════════════════════
#  SENTINEL — Fraktu's Basement Tier Bot
#  Full revamp with QoL, logging, streaks, and more
# ══════════════════════════════════════════════════════════════

load_dotenv()
TOKEN             = os.getenv('DISCORD_TOKEN')
MATCH_LOG_CHANNEL = os.getenv('MATCH_LOG_CHANNEL', '🥊-match-reporting')  # channel name to auto-post results
BOT_VERSION       = "2.0.0"

# ── Config ─────────────────────────────────────────────────────
GAMEMODES   = ["LTMs", "Vanilla", "UHC", "Pot", "NethOP", "SMP", "Sword", "Axe", "Mace"]
INITIAL_ELO = 1000
ELO_MIN     = 0
ELO_MAX     = 9999

TIER_THRESHOLDS = {
    "HT1": 2400, "LT1": 2200,
    "HT2": 2000, "LT2": 1800,
    "HT3": 1600, "LT3": 1400,
    "HT4": 1300, "LT4": 1200,
    "HT5": 1100, "LT5": 0,
}

TIER_COLORS = {
    "HT1": 0xff2a2a, "LT1": 0xff5555,
    "HT2": 0xff7f00, "LT2": 0xffa040,
    "HT3": 0xffcc00, "LT3": 0xffe066,
    "HT4": 0x4caf50, "LT4": 0x81c784,
    "HT5": 0x5865f2, "LT5": 0x8ea1e1,
}

TIER_FULL = {
    "HT1": "High Tier 1", "LT1": "Low Tier 1",
    "HT2": "High Tier 2", "LT2": "Low Tier 2",
    "HT3": "High Tier 3", "LT3": "Low Tier 3",
    "HT4": "High Tier 4", "LT4": "Low Tier 4",
    "HT5": "High Tier 5", "LT5": "Low Tier 5",
}

# ── Bot Setup ───────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members         = True
intents.message_content = True
bot      = commands.Bot(command_prefix='!', intents=intents, help_command=None)
executor = ThreadPoolExecutor(max_workers=2)

# ══════════════════════════════════════════════════════════════
#  UTILITY HELPERS
# ══════════════════════════════════════════════════════════════

def get_tier(elo: int) -> str:
    for tier, threshold in TIER_THRESHOLDS.items():
        if elo >= threshold:
            return tier
    return "LT5"

def get_best_tier(elo_dict: dict) -> str:
    return get_tier(max(elo_dict.values()))

def tier_color(tier: str) -> int:
    return TIER_COLORS.get(tier, 0x2f3136)

def avg_elo(p: dict) -> int:
    vals = list(p['elo'].values())
    return round(sum(vals) / len(vals))

def load_data() -> dict:
    if not os.path.exists('players.json'):
        return {"players": {}, "history": []}
    with open('players.json', 'r') as f:
        return json.load(f)

def save_data(data: dict):
    if len(data.get("history", [])) > 500:
        data["history"] = data["history"][-500:]
    with open('players.json', 'w') as f:
        json.dump(data, f, indent=4)

def get_rank(data: dict, uid: str, mode: str) -> int:
    sorted_p = sorted(
        data["players"].items(),
        key=lambda x: x[1]['elo'].get(mode, 0),
        reverse=True
    )
    for i, (k, _) in enumerate(sorted_p):
        if k == uid:
            return i + 1
    return -1

def find_player_by_ign(data: dict, query: str) -> list:
    q = query.lower()
    return [
        (uid, p) for uid, p in data["players"].items()
        if q in (p.get('ign') or p.get('name', '')).lower()
    ]

def update_streak(player: dict, won: bool):
    streak = player.get('streak', {'type': None, 'count': 0})
    if won:
        streak = {'type': 'W', 'count': streak['count'] + 1} if streak['type'] == 'W' else {'type': 'W', 'count': 1}
    else:
        streak = {'type': 'L', 'count': streak['count'] + 1} if streak['type'] == 'L' else {'type': 'L', 'count': 1}
    player['streak'] = streak

def streak_text(player: dict) -> str:
    s = player.get('streak', {'type': None, 'count': 0})
    if not s['type']:
        return "No streak yet"
    emoji = "🔥" if s['type'] == 'W' else "❄️"
    label = "Win" if s['type'] == 'W' else "Loss"
    return f"{emoji} {s['count']} {label} streak"

# ── Git Sync (non-blocking) ─────────────────────────────────────
def _git_push_blocking():
    repo = git.Repo(os.getcwd())
    repo.git.add('players.json')
    repo.index.commit(f"Sentinel Auto-Update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    repo.remote(name='origin').push()

async def sync_to_web():
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(executor, _git_push_blocking)
        print("🌐 [Web Sync] Pushed to GitHub.")
    except Exception as e:
        print(f"⚠️ [Web Sync] Failed: {e}")

# ── Role helpers ────────────────────────────────────────────────
async def update_tier_roles(member: discord.Member, elo: int):
    target_tier = get_tier(elo)
    tier_names  = list(TIER_THRESHOLDS.keys())
    to_remove   = [
        discord.utils.get(member.guild.roles, name=n)
        for n in tier_names
        if discord.utils.get(member.guild.roles, name=n) in member.roles
    ]
    new_role = discord.utils.get(member.guild.roles, name=target_tier)
    if to_remove:
        await member.remove_roles(*to_remove)
    if new_role:
        await member.add_roles(new_role)

async def log_match(guild: discord.Guild, embed: discord.Embed):
    ch = discord.utils.get(guild.text_channels, name=MATCH_LOG_CHANNEL)
    if ch:
        await ch.send(embed=embed)

# ══════════════════════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"✅ Sentinel v{BOT_VERSION} online — Fraktu's Basement")

@bot.event
async def on_member_join(member: discord.Member):
    role = discord.utils.get(member.guild.roles, name="Unverified")
    if role:
        await member.add_roles(role)
        print(f"[Sentinel] Unverified → {member.name}")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("⛔ You don't have permission to use that command.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("⚠️ Couldn't find that member. Try mentioning them with @.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Missing argument: `{error.param.name}`. Use `!help` to see usage.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("⚠️ Invalid argument. Check your command and try again.")
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Slow down! Try again in `{error.retry_after:.1f}s`.")
    else:
        print(f"[Error] {ctx.command}: {error}")

# ══════════════════════════════════════════════════════════════
#  INFO COMMANDS
# ══════════════════════════════════════════════════════════════

@bot.command()
async def ping(ctx):
    latency = round(bot.latency * 1000)
    color   = 0x2ecc71 if latency < 100 else 0xf39c12 if latency < 200 else 0xe74c3c
    embed   = discord.Embed(title="🏓 Pong!", color=color)
    embed.add_field(name="Latency", value=f"`{latency}ms`")
    await ctx.send(embed=embed)


@bot.command()
async def about(ctx):
    embed = discord.Embed(
        title="🛡️ Sentinel",
        description="The official tier-tracking bot for **Fraktu's Basement**.",
        color=0xff7f00
    )
    data = load_data()
    embed.add_field(name="Version",  value=f"`v{BOT_VERSION}`",      inline=True)
    embed.add_field(name="Prefix",   value="`!`",                     inline=True)
    embed.add_field(name="Modes",    value=str(len(GAMEMODES)),        inline=True)
    embed.add_field(name="Players",  value=str(len(data["players"])),  inline=True)
    embed.add_field(name="Matches",  value=str(len(data["history"])),  inline=True)
    embed.set_footer(text="Use !help for a full command list.")
    await ctx.send(embed=embed)


@bot.command()
async def help(ctx):
    embed = discord.Embed(title="🛡️ Sentinel System Manual", color=discord.Color.dark_grey())

    player_cmds = (
        "`!profile [@user]` — Full stats & tier breakdown\n"
        "`!stats [@user] [mode]` — W/L history, optionally per mode\n"
        "`!streak [@user]` — Current win or loss streak\n"
        "`!compare @a @b [mode]` — Side-by-side comparison\n"
        "`!rank [@user] [mode]` — Leaderboard position\n"
        "`!search [name]` — Find a player by partial IGN\n"
        "`!recent` — Last 10 duels\n"
        "`!leaderboard [mode]` — Top 10 (default: Vanilla)\n"
        "`!ping` — Bot latency\n"
        "`!about` — Bot info & stats"
    )
    embed.add_field(name="👤 Player Commands", value=player_cmds, inline=False)

    if ctx.author.guild_permissions.manage_messages:
        staff_cmds = (
            "`!verify @user [IGN]` — Onboard a player\n"
            "`!unverify @user` — Remove a player from the system\n"
            "`!report @winner @loser [mode]` — Log a match\n"
            "`!undo` — Reverse the last logged match\n"
            "`!rename @user [newIGN]` — Update a player's IGN\n"
            "`!wipe @user` — Reset ALL ELO to 1000\n"
            "`!resetelo @user [mode]` — Reset one mode to 1000\n"
            "`!setelo @user [mode] [elo]` — Force-set ELO (Admin / Tier Verifier)"
        )
        embed.add_field(name="🛠️ Staff Operations", value=staff_cmds, inline=False)

    modes_list = ", ".join(f"`{m}`" for m in GAMEMODES)
    embed.add_field(name="🎮 Available Modes", value=modes_list, inline=False)
    await ctx.send(embed=embed)

# ══════════════════════════════════════════════════════════════
#  PLAYER COMMANDS
# ══════════════════════════════════════════════════════════════

@bot.command()
async def profile(ctx, member: discord.Member = None):
    member = member or ctx.author
    data   = load_data()
    uid    = str(member.id)
    if uid not in data["players"]:
        return await ctx.send("⚠️ This player is not verified!")

    p    = data["players"][uid]
    best = get_best_tier(p['elo'])

    embed = discord.Embed(title=f"👤 {p['ign']}", color=tier_color(best))
    embed.set_thumbnail(url=f"https://mc-heads.net/body/{p['ign']}")
    embed.add_field(name="Best Tier", value=f"**{TIER_FULL[best]}**", inline=True)
    embed.add_field(name="Avg ELO",   value=f"**{avg_elo(p)}**",      inline=True)
    embed.add_field(name="Streak",    value=streak_text(p),            inline=True)

    stats = "\n".join(
        [f"**{m}:** {p['elo'][m]} ({get_tier(p['elo'][m])})" for m in GAMEMODES]
    )
    embed.add_field(name="ELO Breakdown", value=stats, inline=False)
    embed.set_footer(text=f"Discord: {member.name}")
    await ctx.send(embed=embed)


@bot.command()
async def stats(ctx, member: discord.Member = None, mode: str = None):
    member = member or ctx.author
    data   = load_data()
    uid    = str(member.id)
    if uid not in data["players"]:
        return await ctx.send("⚠️ This player is not verified!")

    ign     = data["players"][uid]['ign']
    matches = [m for m in data["history"] if m['winner'] == ign or m['loser'] == ign]

    gm_label = None
    if mode:
        gm_label = next((m for m in GAMEMODES if m.lower() == mode.lower()), None)
        if gm_label:
            matches = [m for m in matches if m['mode'] == gm_label]

    if not matches:
        suffix = f" in **{gm_label}**" if gm_label else ""
        return await ctx.send(f"No match history found for **{ign}**{suffix}.")

    wins    = sum(1 for m in matches if m['winner'] == ign)
    losses  = len(matches) - wins
    winrate = round((wins / len(matches)) * 100)
    recent  = matches[-10:][::-1]

    log = "\n".join([
        f"{'✅' if m['winner'] == ign else '❌'} vs "
        f"**{m['loser'] if m['winner'] == ign else m['winner']}** "
        f"in {m['mode']} ({'+'if m['winner']==ign else '-'}{m['gain']}) — {m['time']}"
        for m in recent
    ])

    best  = get_best_tier(data["players"][uid]['elo'])
    embed = discord.Embed(
        title=f"📊 {ign}{f' — {gm_label}' if gm_label else ''}",
        color=tier_color(best)
    )
    embed.add_field(name="Record",   value=f"**{wins}W / {losses}L**", inline=True)
    embed.add_field(name="Win Rate", value=f"**{winrate}%**",           inline=True)
    embed.add_field(name="Streak",   value=streak_text(data["players"][uid]), inline=True)
    embed.add_field(name="Last 10 Matches", value=log or "None", inline=False)
    await ctx.send(embed=embed)


@bot.command()
async def streak(ctx, member: discord.Member = None):
    member = member or ctx.author
    data   = load_data()
    uid    = str(member.id)
    if uid not in data["players"]:
        return await ctx.send("⚠️ This player is not verified!")

    p     = data["players"][uid]
    best  = get_best_tier(p['elo'])
    embed = discord.Embed(
        title=f"🔥 {p['ign']}'s Streak",
        description=streak_text(p),
        color=tier_color(best)
    )
    await ctx.send(embed=embed)


@bot.command()
async def compare(ctx, member1: discord.Member, member2: discord.Member, mode: str = "Vanilla"):
    data = load_data()
    uid1 = str(member1.id)
    uid2 = str(member2.id)

    if uid1 not in data["players"] or uid2 not in data["players"]:
        return await ctx.send("⚠️ Both players must be verified!")

    gm   = next((m for m in GAMEMODES if m.lower() == mode.lower()), "Vanilla")
    p1   = data["players"][uid1]
    p2   = data["players"][uid2]
    elo1 = p1['elo'][gm]
    elo2 = p2['elo'][gm]
    t1   = get_tier(elo1)
    t2   = get_tier(elo2)

    embed = discord.Embed(title=f"⚔️ {p1['ign']} vs {p2['ign']} — {gm}", color=0x9b59b6)
    embed.add_field(
        name=f"🔵 {p1['ign']}",
        value=f"**{elo1} ELO**\n{TIER_FULL[t1]}\nAvg: {avg_elo(p1)}\n{streak_text(p1)}",
        inline=True
    )
    embed.add_field(name="\u200b", value="**VS**", inline=True)
    embed.add_field(
        name=f"🔴 {p2['ign']}",
        value=f"**{elo2} ELO**\n{TIER_FULL[t2]}\nAvg: {avg_elo(p2)}\n{streak_text(p2)}",
        inline=True
    )
    leader = p1['ign'] if elo1 >= elo2 else p2['ign']
    embed.set_footer(text=f"{leader} leads by {abs(elo1-elo2)} ELO in {gm}")
    await ctx.send(embed=embed)


@bot.command()
async def rank(ctx, member: discord.Member = None, mode: str = "Vanilla"):
    member = member or ctx.author
    data   = load_data()
    uid    = str(member.id)
    if uid not in data["players"]:
        return await ctx.send("⚠️ This player is not verified!")

    gm       = next((m for m in GAMEMODES if m.lower() == mode.lower()), "Vanilla")
    position = get_rank(data, uid, gm)
    total    = len(data["players"])
    p        = data["players"][uid]
    elo      = p['elo'][gm]
    tier     = get_tier(elo)

    embed = discord.Embed(title=f"🏅 {p['ign']} — {gm} Rank", color=tier_color(tier))
    embed.add_field(name="Position", value=f"**#{position}** of {total}", inline=True)
    embed.add_field(name="ELO",      value=f"**{elo}**",                  inline=True)
    embed.add_field(name="Tier",     value=TIER_FULL[tier],               inline=True)
    await ctx.send(embed=embed)


@bot.command()
async def search(ctx, *, query: str):
    data    = load_data()
    results = find_player_by_ign(data, query)

    if not results:
        return await ctx.send(f"🔍 No players found matching `{query}`.")

    embed = discord.Embed(title=f"🔍 Search: \"{query}\"", color=0x3498db)
    for uid, p in results[:10]:
        best = get_best_tier(p['elo'])
        embed.add_field(
            name=p['ign'],
            value=f"Best: **{TIER_FULL[best]}** | Avg ELO: **{avg_elo(p)}** | {streak_text(p)}",
            inline=False
        )
    if len(results) > 10:
        embed.set_footer(text=f"Showing 10 of {len(results)} results.")
    await ctx.send(embed=embed)


@bot.command()
async def recent(ctx):
    data = load_data()
    if not data["history"]:
        return await ctx.send("No matches logged yet!")
    history = data["history"][-10:][::-1]
    log = "\n".join([
        f"**{m['winner']}** beat **{m['loser']}** in {m['mode']} (+{m['gain']}) — {m['time']}"
        for m in history
    ])
    await ctx.send(embed=discord.Embed(title="📜 Recent Duels", description=log, color=0x3498db))


@bot.command()
@commands.cooldown(1, 5, commands.BucketType.channel)
async def leaderboard(ctx, mode: str = "Vanilla"):
    gm       = next((m for m in GAMEMODES if m.lower() == mode.lower()), "Vanilla")
    data     = load_data()
    sorted_p = sorted(data["players"].items(), key=lambda x: x[1]['elo'][gm], reverse=True)[:10]
    medals   = ["🥇", "🥈", "🥉"]

    lb = "\n".join([
        f"{medals[i] if i < 3 else f'`{i+1}.`'} **{v['ign']}** — "
        f"{v['elo'][gm]} ELO ({get_tier(v['elo'][gm])})"
        for i, (k, v) in enumerate(sorted_p)
    ])
    embed = discord.Embed(title=f"🏆 {gm} Leaderboard", description=lb or "No players yet.", color=0xf1c40f)
    embed.set_footer(text=f"{len(data['players'])} total players")
    await ctx.send(embed=embed)

# ══════════════════════════════════════════════════════════════
#  STAFF COMMANDS
# ══════════════════════════════════════════════════════════════

@bot.command()
@commands.has_permissions(administrator=True)
async def verify(ctx, member: discord.Member, ign: str):
    data = load_data()
    uid  = str(member.id)
    data["players"][uid] = {
        "name":   member.name,
        "ign":    ign,
        "elo":    {gm: INITIAL_ELO for gm in GAMEMODES},
        "streak": {"type": None, "count": 0}
    }
    save_data(data)

    unverified = discord.utils.get(member.guild.roles, name="Unverified")
    verified   = discord.utils.get(member.guild.roles, name="Verified")
    if unverified and unverified in member.roles:
        await member.remove_roles(unverified)
    if verified:
        await member.add_roles(verified)

    await update_tier_roles(member, INITIAL_ELO)
    await sync_to_web()
    await ctx.send(f"✅ **{member.name}** verified as `{ign}`. Welcome to the Basement!")


@bot.command()
@commands.has_permissions(administrator=True)
async def unverify(ctx, member: discord.Member):
    data = load_data()
    uid  = str(member.id)
    if uid not in data["players"]:
        return await ctx.send("⚠️ This player isn't in the system.")

    ign = data["players"][uid]['ign']
    del data["players"][uid]
    save_data(data)

    verified   = discord.utils.get(member.guild.roles, name="Verified")
    unverified = discord.utils.get(member.guild.roles, name="Unverified")
    tier_roles = [discord.utils.get(member.guild.roles, name=n) for n in TIER_THRESHOLDS.keys()]
    to_strip   = [r for r in [verified] + tier_roles if r and r in member.roles]
    if to_strip:
        await member.remove_roles(*to_strip)
    if unverified:
        await member.add_roles(unverified)

    await sync_to_web()
    await ctx.send(f"🗑️ **{ign}** has been removed from the system.")


@bot.command()
@commands.has_permissions(manage_messages=True)
async def rename(ctx, member: discord.Member, new_ign: str):
    data = load_data()
    uid  = str(member.id)
    if uid not in data["players"]:
        return await ctx.send("⚠️ This player isn't verified!")

    old_ign = data["players"][uid]['ign']
    data["players"][uid]['ign'] = new_ign
    save_data(data)
    await sync_to_web()
    await ctx.send(f"✏️ Renamed **{old_ign}** → **{new_ign}**.")


@bot.command()
@commands.has_permissions(manage_messages=True)
async def report(ctx, winner: discord.Member, loser: discord.Member, gamemode: str):
    mode = next((m for m in GAMEMODES if m.lower() == gamemode.lower()), None)
    if not mode:
        return await ctx.send(f"⚠️ Invalid mode! Choose from: {', '.join(GAMEMODES)}")

    data  = load_data()
    w_uid = str(winner.id)
    l_uid = str(loser.id)

    if w_uid not in data["players"] or l_uid not in data["players"]:
        return await ctx.send("⚠️ Both players must be verified first!")

    w_elo = data["players"][w_uid]['elo'][mode]
    l_elo = data["players"][l_uid]['elo'][mode]
    gain  = round(32 * (1 - (1 / (1 + 10 ** ((l_elo - w_elo) / 400)))))

    new_w_elo = max(ELO_MIN, min(ELO_MAX, w_elo + gain))
    new_l_elo = max(ELO_MIN, min(ELO_MAX, l_elo - gain))

    data["players"][w_uid]['elo'][mode] = new_w_elo
    data["players"][l_uid]['elo'][mode] = new_l_elo

    update_streak(data["players"][w_uid], won=True)
    update_streak(data["players"][l_uid], won=False)

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

    await update_tier_roles(winner, new_w_elo)
    await update_tier_roles(loser,  new_l_elo)
    await sync_to_web()

    w_tier = get_tier(new_w_elo)
    l_tier = get_tier(new_l_elo)

    embed = discord.Embed(title=f"⚔️ Match Result — {mode}", color=tier_color(w_tier))
    embed.add_field(
        name=f"🏆 {data['players'][w_uid]['ign']}",
        value=f"{w_elo} → **{new_w_elo}** (+{gain})\n{TIER_FULL[w_tier]}\n{streak_text(data['players'][w_uid])}",
        inline=True
    )
    embed.add_field(name="\u200b", value="beats", inline=True)
    embed.add_field(
        name=f"💀 {data['players'][l_uid]['ign']}",
        value=f"{l_elo} → **{new_l_elo}** (-{gain})\n{TIER_FULL[l_tier]}\n{streak_text(data['players'][l_uid])}",
        inline=True
    )
    embed.set_footer(text=f"Reported by {ctx.author.name}")

    await ctx.send(embed=embed)
    await log_match(ctx.guild, embed)


@bot.command()
@commands.has_permissions(manage_messages=True)
async def undo(ctx):
    data = load_data()
    if not data["history"]:
        return await ctx.send("⚠️ No matches to undo!")

    last = data["history"][-1]
    if "w_elo_before" not in last or "winner_id" not in last:
        return await ctx.send("⚠️ This match predates undo support. Use `!setelo` to fix manually.")

    w_uid = last["winner_id"]
    l_uid = last["loser_id"]
    if w_uid not in data["players"] or l_uid not in data["players"]:
        return await ctx.send("⚠️ One or both players no longer exist.")

    data["players"][w_uid]['elo'][last['mode']] = last['w_elo_before']
    data["players"][l_uid]['elo'][last['mode']] = last['l_elo_before']

    # Reverse streaks
    update_streak(data["players"][w_uid], won=False)
    update_streak(data["players"][l_uid], won=True)

    data["history"].pop()
    save_data(data)

    w_member = ctx.guild.get_member(int(w_uid))
    l_member = ctx.guild.get_member(int(l_uid))
    if w_member: await update_tier_roles(w_member, last['w_elo_before'])
    if l_member: await update_tier_roles(l_member, last['l_elo_before'])

    await sync_to_web()
    await ctx.send(
        f"↩️ Undone: **{last['winner']}** vs **{last['loser']}** in {last['mode']} "
        f"(+{last['gain']} reversed)."
    )


@bot.command()
@commands.has_permissions(administrator=True)
async def wipe(ctx, member: discord.Member):
    data = load_data()
    uid  = str(member.id)
    if uid not in data["players"]:
        return await ctx.send("⚠️ This player isn't verified!")

    ign = data["players"][uid]['ign']
    data["players"][uid]['elo']    = {gm: INITIAL_ELO for gm in GAMEMODES}
    data["players"][uid]['streak'] = {"type": None, "count": 0}
    save_data(data)

    await update_tier_roles(member, INITIAL_ELO)
    await sync_to_web()
    await ctx.send(f"🧹 **{ign}**'s ELO wiped to {INITIAL_ELO} across all modes.")


@bot.command()
@commands.has_permissions(manage_messages=True)
async def resetelo(ctx, member: discord.Member, gamemode: str):
    mode = next((m for m in GAMEMODES if m.lower() == gamemode.lower()), None)
    if not mode:
        return await ctx.send(f"⚠️ Invalid mode! Choose from: {', '.join(GAMEMODES)}")

    data = load_data()
    uid  = str(member.id)
    if uid not in data["players"]:
        return await ctx.send("⚠️ This player isn't verified!")

    data["players"][uid]['elo'][mode] = INITIAL_ELO
    save_data(data)
    await update_tier_roles(member, INITIAL_ELO)
    await sync_to_web()
    await ctx.send(f"🔄 **{data['players'][uid]['ign']}**'s {mode} ELO reset to {INITIAL_ELO}.")


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

    elo  = max(ELO_MIN, min(ELO_MAX, elo))
    data = load_data()
    uid  = str(member.id)
    if uid not in data["players"]:
        return await ctx.send("⚠️ This player isn't verified!")

    data["players"][uid]['elo'][mode] = elo
    save_data(data)
    await update_tier_roles(member, elo)
    await sync_to_web()

    tier = get_tier(elo)
    await ctx.send(f"✅ Set **{data['players'][uid]['ign']}** to **{elo} ELO** ({TIER_FULL[tier]}) in {mode}.")

# ══════════════════════════════════════════════════════════════
bot.run(TOKEN)