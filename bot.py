import discord
from discord.ext import commands
import json
import os
from datetime import datetime
import git 

TOKEN = 'YOUR_TOKEN_HERE'
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

def sync_to_web():
    try:
        repo = git.Repo(os.getcwd())
        repo.git.add('players.json')
        repo.index.commit(f"Sentinel Auto-Update: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        origin = repo.remote(name='origin')
        origin.push()
        print("🌐 [Web Sync] Successfully pushed updates to GitHub.")
    except Exception as e:
        print(f"⚠️ [Web Sync] Failed to sync: {e}")

def load_data():
    if not os.path.exists('players.json'): 
        return {"players": {}, "history": []}
    with open('players.json', 'r') as f:
        content = json.load(f)
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
    target_tier = get_tier(elo)
    tier_names = list(TIER_THRESHOLDS.keys())
    roles_to_remove = [discord.utils.get(member.guild.roles, name=n) for n in tier_names]
    new_role = discord.utils.get(member.guild.roles, name=target_tier)
    current_tier_roles = [r for r in roles_to_remove if r and r in member.roles]
    if current_tier_roles:
        await member.remove_roles(*current_tier_roles)
    if new_role: 
        await member.add_roles(new_role)

@bot.event
async def on_ready():
    print(f'Sentinel is online. Monitoring Fraktu\'s Basement.')
    await bot.change_presence(activity=discord.Game(name="MC Tier Testing"))

@bot.command()
async def help(ctx):
    embed = discord.Embed(title="🛡️ Sentinel System Manual", color=discord.Color.dark_grey())
    player_cmds = "`!profile @user` - Stats & Tiers.\n`!recent` - Last 10 duels.\n`!leaderboard [mode]` - Rankings."
    embed.add_field(name="👤 Player Commands", value=player_cmds, inline=False)
    if ctx.author.guild_permissions.manage_messages:
        staff_cmds = "`!verify @user [IGN]` - Onboard player.\n`!report @win @loss [mode]` - Log match."
        embed.add_field(name="🛠️ Staff Operations", value=staff_cmds, inline=False)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def verify(ctx, member: discord.Member, ign: str):
    data = load_data()
    uid = str(member.id)
    if uid in data["players"]:
        return await ctx.send("Player is already verified!")
    
    # NEW: Store IGN specifically for the website
    data["players"][uid] = {
        "name": member.name, 
        "ign": ign, 
        "elo": {gm: INITIAL_ELO for gm in GAMEMODES}
    }
    save_data(data)

    unverified = discord.utils.get(ctx.guild.roles, name="Unverified")
    verified = discord.utils.get(ctx.guild.roles, name="Verified")
    if unverified: await member.remove_roles(unverified)
    if verified: await member.add_roles(verified)
    await update_tier_roles(member, INITIAL_ELO)
    
    sync_to_web()
    await ctx.send(f"✅ **{member.name}** verified as `{ign}`.")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def report(ctx, winner: discord.Member, loser: discord.Member, gamemode: str):
    mode = next((m for m in GAMEMODES if m.lower() == gamemode.lower()), None)
    if not mode: return await ctx.send(f"Invalid mode!")

    data = load_data()
    if str(winner.id) not in data["players"] or str(loser.id) not in data["players"]:
        return await ctx.send("Verify both players first!")

    w_elo = data["players"][str(winner.id)]['elo'][mode]
    l_elo = data["players"][str(loser.id)]['elo'][mode]
    gain = round(32 * (1 - (1 / (1 + 10 ** ((l_elo - w_elo) / 400)))))
    
    data["players"][str(winner.id)]['elo'][mode] += gain
    data["players"][str(loser.id)]['elo'][mode] -= gain
    
    # NEW: Use IGN in history for the web renders
    match_entry = {
        "winner": data["players"][str(winner.id)]['ign'], 
        "loser": data["players"][str(loser.id)]['ign'], 
        "mode": mode, "gain": gain, "time": datetime.now().strftime("%m/%d %H:%M")
    }
    data["history"].append(match_entry)
    save_data(data)

    if mode == "Vanilla":
        await update_tier_roles(winner, data["players"][str(winner.id)]['elo']["Vanilla"])
        await update_tier_roles(loser, data["players"][str(loser.id)]['elo']["Vanilla"])

    sync_to_web()
    await ctx.send(f"⚔️ Match Logged: {winner.name} beat {loser.name} in {mode}.")

# ... (Keep Profile, Recent, Leaderboard commands as they were)

bot.run(TOKEN)