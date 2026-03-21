import discord
from discord.ext import commands
import json
import os
from datetime import datetime
import git

# --- CONFIGURATION ---
TOKEN = 'REMOVED'
GAMEMODES = ["LTMs", "Vanilla", "UHC", "Pot", "NethOP", "SMP", "Sword", "Axe", "Mace"]
INITIAL_ELO = 1000

TIER_THRESHOLDS = {
    "HT1": 2500, "HT2": 2200, "HT3": 2000,
    "LT1": 1800, "LT2": 1600, "LT3": 1400, "LT4": 1200, "LT5": 0
}

intents = discord.Intents.default()
intents.members = True 
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# --- DATABASE HELPERS ---
def load_data():
    if not os.path.exists('players.json'): 
        return {"players": {}, "history": []}
    with open('players.json', 'r') as f:
        content = json.load(f)
        # Fix for transition: if the file is the old format, wrap it
        if "players" not in content:
            return {"players": content, "history": []}
        return content

def save_data(data):
    with open('players.json', 'w') as f:
        json.dump(data, f, indent=4)

def get_tier(elo):
    for tier, threshold in TIER_THRESHOLDS.items():
        if elo >= threshold: return tier
    return "LT5"

async def update_tier_roles(member, elo):
    """Calculates and assigns the correct LT/HT role based on Elo."""
    target_tier = get_tier(elo)
    tier_names = list(TIER_THRESHOLDS.keys())
    roles_to_remove = [discord.utils.get(member.guild.roles, name=n) for n in tier_names]
    new_role = discord.utils.get(member.guild.roles, name=target_tier)
    
    # Remove old tier roles
    current_tier_roles = [r for r in roles_to_remove if r and r in member.roles]
    if current_tier_roles:
        await member.remove_roles(*current_tier_roles)
    
    # Add new tier role
    if new_role: 
        await member.add_roles(new_role)

# --- EVENTS ---
@bot.event
async def on_ready():
    print(f'Sentinel is online. Monitoring Fraktu\'s Basement.')
    await bot.change_presence(activity=discord.Game(name="MC Tier Testing"))

@bot.event
async def on_member_join(member):
    """Auto-assign Unverified role when someone joins."""
    role = discord.utils.get(member.guild.roles, name="Unverified")
    if role:
        await member.add_roles(role)

# --- COMMANDS ---

@bot.command()
async def help(ctx):
    embed = discord.Embed(title="🛡️ Sentinel System Manual", color=discord.Color.dark_grey())
    embed.set_author(name="Sentinel Bot", icon_url=bot.user.avatar.url if bot.user.avatar else None)
    
    player_cmds = (
        "`!profile @user` - Full stats breakdown & current Tier.\n"
        "`!recent` - View the last 10 duels in the basement.\n"
        "`!leaderboard [mode]` - Top 10 rankings for any gamemode."
    )
    embed.add_field(name="👤 Player Commands", value=player_cmds, inline=False)
    
    if ctx.author.guild_permissions.manage_messages:
        staff_cmds = (
            "`!verify @user` - Onboard a new player (Sets LT5).\n"
            "`!report @win @loss [mode]` - Update Elo and Tiers."
        )
        embed.add_field(name="🛠️ Staff Operations", value=staff_cmds, inline=False)
    
    embed.set_footer(text="Fraktu's Basement • Secure Tier Testing")
    await ctx.send(embed=embed)

@bot.command()
async def profile(ctx, member: discord.Member = None):
    member = member or ctx.author
    data = load_data()
    uid = str(member.id)

    if uid not in data["players"]:
        return await ctx.send("❌ Player not verified.")

    stats = data["players"][uid]['elo']
    
    # Logic for Peak Mode
    sorted_modes = sorted(stats.items(), key=lambda x: x[1], reverse=True)
    best_mode, best_elo = sorted_modes[0]
    
    # Calculate Overall Average
    avg_elo = round(sum(stats.values()) / len(stats))

    embed = discord.Embed(title=f"👤 {member.name}'s Profile", color=discord.Color.green())
    embed.set_thumbnail(url=member.avatar.url if member.avatar else None)
    
    embed.add_field(name="Overall Average", value=f"`{avg_elo} Elo`", inline=True)
    embed.add_field(name="Peak Performance", value=f"**{get_tier(best_elo)}** in **{best_mode}** ({best_elo} Elo)", inline=False)
    
    # Breakdown of all modes
    breakdown = ""
    for mode in GAMEMODES:
        elo = stats.get(mode, 1000)
        breakdown += f"**{mode}:** {get_tier(elo)} ({elo})\n"
    
    embed.add_field(name="All Gamemodes", value=breakdown, inline=False)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def verify(ctx, member: discord.Member):
    data = load_data()
    uid = str(member.id)
    if uid in data["players"]:
        return await ctx.send("Player is already verified!")

    data["players"][uid] = {"name": member.name, "elo": {gm: INITIAL_ELO for gm in GAMEMODES}}
    save_data(data)

    # Role Management
    unverified = discord.utils.get(ctx.guild.roles, name="Unverified")
    verified = discord.utils.get(ctx.guild.roles, name="Verified")
    
    if unverified: await member.remove_roles(unverified)
    if verified: await member.add_roles(verified)
    await update_tier_roles(member, INITIAL_ELO)
    
    await ctx.send(f"✅ **{member.name}** has been cleared. Welcome to the Basement.")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def report(ctx, winner: discord.Member, loser: discord.Member, gamemode: str):
    mode = next((m for m in GAMEMODES if m.lower() == gamemode.lower()), None)
    if not mode: return await ctx.send(f"Invalid mode! Use: {', '.join(GAMEMODES)}")

    data = load_data()
    if str(winner.id) not in data["players"] or str(loser.id) not in data["players"]:
        return await ctx.send("Verify both players first!")

    w_elo = data["players"][str(winner.id)]['elo'][mode]
    l_elo = data["players"][str(loser.id)]['elo'][mode]

    # Elo Math
    gain = round(32 * (1 - (1 / (1 + 10 ** ((l_elo - w_elo) / 400)))))
    
    data["players"][str(winner.id)]['elo'][mode] += gain
    data["players"][str(loser.id)]['elo'][mode] -= gain
    
    # Log History
    match_entry = {
        "winner": winner.name, "loser": loser.name, 
        "mode": mode, "gain": gain, "time": datetime.now().strftime("%m/%d %H:%M")
    }
    data["history"].append(match_entry)
    save_data(data)

    # Only Vanilla Elo changes the actual Discord Role (Standard)
    if mode == "Vanilla":
        await update_tier_roles(winner, data["players"][str(winner.id)]['elo']["Vanilla"])
        await update_tier_roles(loser, data["players"][str(loser.id)]['elo']["Vanilla"])

    await ctx.send(f"⚔️ **Match Logged:** {winner.name} (+{gain}) vs {loser.name} (-{gain}) in {mode}.")

@bot.command()
async def recent(ctx):
    data = load_data()
    history = data.get("history", [])[-10:]
    if not history: return await ctx.send("No matches recorded yet!")

    embed = discord.Embed(title="⚔️ Recent Matches", color=discord.Color.blue())
    desc = ""
    for m in reversed(history):
        desc += f"• **{m['winner']}** beat **{m['loser']}** in `{m['mode']}` *(+{m['gain']} Elo)*\n"
    embed.description = desc
    await ctx.send(embed=embed)

@bot.command()
async def leaderboard(ctx, gamemode: str = "Vanilla"):
    mode = next((m for m in GAMEMODES if m.lower() == gamemode.lower()), None)
    if not mode: return await ctx.send("Invalid gamemode.")

    data = load_data()
    leader = sorted(data["players"].items(), key=lambda x: x[1]['elo'].get(mode, 1000), reverse=True)

    embed = discord.Embed(title=f"🏆 {mode} Top 10", color=discord.Color.gold())
    description = ""
    for i, (uid, info) in enumerate(leader[:10], 1):
        description += f"`{i}.` **{info['name']}** — {info['elo'][mode]} Elo\n"
    
    embed.description = description or "The basement is empty... for now."
    await ctx.send(embed=embed)

bot.run(TOKEN)