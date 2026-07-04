import discord
from discord import app_commands
from discord.ext import commands
import anthropic
import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

RR_CONTEXT = """
You are Penta-Bot, the official assistant for the Pentathletes Discord server.
Pentathletes is a Rocket League esports org competing primarily in the Titans division of Rocket Rivals.
You can answer ANY question members ask — general knowledge, Rocket League gameplay, tech, math, whatever. Be genuinely helpful on any topic.
For Rocket Rivals league rules, eligibility, rosters, and tryouts specifically, answer ONLY from the reference info below — never invent league rules. If the reference doesn't cover a league question, say you're not sure and suggest asking a league admin.
Be concise, direct, and professional. Use Discord markdown formatting where appropriate.

=== ROCKET RIVALS OVERVIEW ===
Rocket Rivals is a structured Rocket League league with 3 divisions:
- Challengers: C1–C3
- Legends: GC1–GC2
- Titans: GC3–2K SSL

=== GENERAL RULES ===
- Must be 15+ to compete
- Discord name must match in-game name exactly at all times
- No toxicity: slurs, hate speech, harassment, NSFW content are all prohibited
- Strike system: 1st offense = 24hr timeout, 2nd = 1 week timeout, 3rd = permanent ban
- Zero tolerance for cheating: smurfing, account sharing, game mods, alt accounts — all result in indefinite removal
- Epic account bans carry over into Rocket Rivals (no alt accounts allowed)
- Inappropriate Discord bio, name, or Epic name = denied entry
- Sensitive topic discussion (race, religion, politics, gender) = timeout or further punishment

=== ELIGIBILITY ===
- Must be 15 years or older
- Peak rank from the last calendar year (1s/2s/3s — whichever is highest)
- 1s rank inflates your league rank by 1 full rank (e.g. GC1 in 1s = GC2 in league)
- If RL tracker shows lower rank but peak MMR shows higher, the higher rank applies
- 400 ranked games required (1s/2s/3s combined, last 2 seasons)
- Can be signed to a roster before 400 games but CANNOT play until threshold is met
- Must be officially signed in #signings and verified by the bot before any match
- Can only play in ONE division (can coach/manage in others)
- No second accounts to play on other teams in any division

=== TEAM STRUCTURE (ALL DIVISIONS) ===
- 5 rostered players (includes captain)
- 1 required Team Captain
- 1 optional Coach (non-playing, permanently)
- 1 optional Manager (non-playing, permanently)
- Only owners/managers/coaches/captains can add/remove players in #signings
- Team owners have final say on all roster changes and cannot be removed
- Max 2 teams per org per division (must be on separate days: 1 Saturday, 1 Sunday)

=== CHALLENGERS ROSTER SLOTS (C1–C3) ===
- Player 1: GC1 peak or lower
- Player 2: GC1 peak or lower
- Player 3: C3 peak or lower
- Player 4: C3 peak or lower
- Player 5: C3 peak or lower

=== LEGENDS ROSTER SLOTS (GC1–GC2) ===
- Player 1: GC3 peak or lower
- Player 2: GC3 peak or lower
- Player 3: GC2 peak or lower
- Player 4: GC2 peak or lower
- Player 5: GC2 peak or lower

=== TITANS ROSTER SLOTS (GC3–2K SSL) ===
- Player 1: SSL 2K MMR max or lower
- Player 2: GC3 peak or lower
- Player 3: GC3 peak or lower
- Player 4: GC3 peak or lower
- Player 5: GC3 peak or lower

=== FREE AGENCY & ROSTER CHANGES ===
- 1 free agent window per season (after Major 1, before Split 2)
- Abandoning your team = locked out until next season
- If you rank out of your division mid-season, you have 5 days to join a new team
- Replacement player must be of equal or lesser rank to the player who left
- Name changes after season starts = invalid player status

=== MATCH RULES ===
- US East servers only for all official games
- All playing members must be in team VC during matches
- Lobby name must use team acronyms (e.g. NCR vs BVO)
- No unapproved spectators — results in game forfeits
- Server reset allowed within first minute if requested properly ("server reset" not just "server")
- Ballchasing link required after every series
- Score report required after every non-streamed series (winner posts)
- Delays: after 10 min = Game 1 FF, after 15 min = full series FF
- Approved maps list only (Aquadome, Beckwith Park, DFH Stadium, etc.)

=== DISCORD SERVERS ===
Hub (required for all): https://discord.gg/UMkFkr7r
Challengers: https://discord.gg/J6Yn4RbdPh
Legends: https://discord.gg/MVm3wTZnHY
Titans: https://discord.gg/SNJhsp8jXZ

=== HOW TO JOIN ===
1. Join the Hub server: https://discord.gg/UMkFkr7r
2. Join your eligible division server
3. Set Discord display name to match your in-game name exactly
4. Go to #rank-selection-and-roles and select your peak rank
5. Grab the Free Agent tag
6. Post in #free-agency to find a team
7. Once signed by a team, get verified by the bot
8. You're eligible once signed + verified + 400 games met
"""

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # required for on_member_remove (farewell messages)

bot = commands.Bot(command_prefix="!", intents=intents)
ai = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    print(f"Penta-Bot online as {bot.user} (ID: {bot.user.id})")


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if bot.user.mentioned_in(message) and not message.mention_everyone:
        question = message.content.replace(f"<@{bot.user.id}>", "").strip()
        if not question:
            await message.reply("What do you need? Ask me anything — league rules or any other question.")
            return
        async with message.channel.typing():
            response = await query_claude(question)
        for chunk in split_message(response):
            await message.channel.send(chunk)
    await bot.process_commands(message)


async def query_claude(question: str) -> str:
    try:
        msg = ai.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=RR_CONTEXT,
            messages=[{"role": "user", "content": question}]
        )
        return msg.content[0].text
    except Exception as e:
        return f"Something went wrong reaching the AI: {e}"


def split_message(text: str, limit: int = 1900) -> list:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


# ── /ask ──────────────────────────────────────────────────────────────────────

@bot.tree.command(name="ask", description="Ask Penta-Bot anything — league rules or any other question")
@app_commands.describe(question="Your question — anything goes")
async def ask_command(interaction: discord.Interaction, question: str):
    await interaction.response.defer()
    response = await query_claude(question)
    chunks = split_message(response)
    await interaction.followup.send(chunks[0])
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk)


# ── /tryouts ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="tryouts", description="Tryout info for all Rocket Rivals divisions")
async def tryouts(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🚀 ROCKET RIVALS — TRYOUTS ARE OPEN",
        description="Rocket Rivals is accepting Free Agents across all three divisions.\nFind a team, get signed, and compete.",
        color=0x5865F2
    )
    embed.add_field(name="🔵 Challengers — C1 to C3", value="[Join Server](https://discord.gg/J6Yn4RbdPh)", inline=True)
    embed.add_field(name="🟣 Legends — GC1 to GC2", value="[Join Server](https://discord.gg/MVm3wTZnHY)", inline=True)
    embed.add_field(name="🔴 Titans — GC3 to 2K SSL", value="[Join Server](https://discord.gg/SNJhsp8jXZ)", inline=True)
    embed.add_field(
        name="✅ Requirements",
        value="• 15+ years old\n• 400 ranked games (1s/2s/3s, last 2 seasons)\n• Discord name must match in-game name\n• Verified by bot + officially signed to a team",
        inline=False
    )
    embed.add_field(
        name="📋 How to Sign Up",
        value="1. Join the Hub server\n2. Join your division server\n3. Select peak rank in **#rank-selection-and-roles**\n4. Grab the Free Agent tag\n5. Post in **#free-agency** to find a team",
        inline=False
    )
    embed.add_field(name="🌐 Rocket Rivals Hub", value="[Join Hub](https://discord.gg/UMkFkr7r) — Required for all players", inline=False)
    embed.set_footer(text="Pentathletes • Rocket Rivals")
    await interaction.response.send_message(embed=embed)


# ── /divisions ────────────────────────────────────────────────────────────────

@bot.tree.command(name="divisions", description="Division breakdown and server links")
async def divisions(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🏆 ROCKET RIVALS — DIVISIONS",
        description=(
            "There are 3 divisions based on your **peak rank from the last calendar year** (1s/2s/3s — highest counts).\n"
            "⚠️ Your 1s rank inflates your league rank by **1 full rank**."
        ),
        color=0x5865F2
    )
    embed.add_field(name="🔵 Challengers", value="**Ranks:** C1 – C3\n[Join](https://discord.gg/J6Yn4RbdPh)", inline=True)
    embed.add_field(name="🟣 Legends", value="**Ranks:** GC1 – GC2\n[Join](https://discord.gg/MVm3wTZnHY)", inline=True)
    embed.add_field(name="🔴 Titans", value="**Ranks:** GC3 – 2K SSL\n[Join](https://discord.gg/SNJhsp8jXZ)", inline=True)
    embed.add_field(name="🌐 Hub Server", value="Required for all players regardless of division.\n[Join Hub](https://discord.gg/UMkFkr7r)", inline=False)
    embed.set_footer(text="Pentathletes • Rocket Rivals")
    await interaction.response.send_message(embed=embed)


# ── /roster ───────────────────────────────────────────────────────────────────

@bot.tree.command(name="roster", description="Team roster structure and rank slot limits per division")
async def roster(interaction: discord.Interaction):
    embed = discord.Embed(
        title="👥 TEAM ROSTER RULES — ALL DIVISIONS",
        description=(
            "Every team has the same base structure:\n"
            "• **5 rostered players** (includes captain)\n"
            "• **1 Team Captain** — required\n"
            "• **1 Coach** — optional, non-playing\n"
            "• **1 Manager** — optional, non-playing"
        ),
        color=0x5865F2
    )
    embed.add_field(
        name="🔵 Challengers Slots",
        value="• 2 slots: GC1 peak or lower\n• 3 slots: C3 peak or lower",
        inline=True
    )
    embed.add_field(
        name="🟣 Legends Slots",
        value="• 2 slots: GC3 peak or lower\n• 3 slots: GC2 peak or lower",
        inline=True
    )
    embed.add_field(
        name="🔴 Titans Slots",
        value="• 1 slot: SSL (2K MMR max)\n• 4 slots: GC3 peak",
        inline=True
    )
    embed.add_field(
        name="📌 Notes",
        value=(
            "• Coaches & Managers can **never** play in matches\n"
            "• Max **2 teams per org** per division (must be Sat/Sun split)\n"
            "• Team Owner has final say on all roster changes and cannot be removed"
        ),
        inline=False
    )
    embed.set_footer(text="Pentathletes • Rocket Rivals")
    await interaction.response.send_message(embed=embed)


# ── /checklist ────────────────────────────────────────────────────────────────

@bot.tree.command(name="checklist", description="Full player eligibility checklist to compete in Rocket Rivals")
async def checklist(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 PLAYER ELIGIBILITY CHECKLIST",
        description="Everything you need before you can compete in Rocket Rivals.",
        color=0x5865F2
    )
    embed.add_field(
        name="Personal",
        value="✅ 15 years or older\n✅ Clean Epic account (bans carry over — no alts)\n✅ Appropriate Discord name, Epic name, and bio",
        inline=False
    )
    embed.add_field(
        name="Rank & Division",
        value=(
            "✅ Know your peak rank (last calendar year, 1s/2s/3s)\n"
            "✅ Remember: 1s rank inflates league rank by 1 full rank\n"
            "✅ Join the correct division server"
        ),
        inline=False
    )
    embed.add_field(
        name="Games Played",
        value="✅ 400 ranked games (1s/2s/3s combined, last 2 seasons)\n✅ Must be on the account you'll use in league",
        inline=False
    )
    embed.add_field(
        name="Signing & Verification",
        value=(
            "✅ Officially posted in **#signings** by your team\n"
            "✅ Verified by the league bot\n"
            "✅ Discord name matches in-game name at all times"
        ),
        inline=False
    )
    embed.add_field(
        name="Servers to Join",
        value="[Hub](https://discord.gg/UMkFkr7r) • [Challengers](https://discord.gg/J6Yn4RbdPh) • [Legends](https://discord.gg/MVm3wTZnHY) • [Titans](https://discord.gg/SNJhsp8jXZ)",
        inline=False
    )
    embed.set_footer(text="Pentathletes • Rocket Rivals")
    await interaction.response.send_message(embed=embed)


# ── /servers ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="servers", description="All Rocket Rivals division server links")
async def servers(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🌐 ROCKET RIVALS — SERVER LINKS",
        description=(
            "The links below are part of **Rocket Rivals**, one of the leagues **Pentathletes** competes in.\n"
            "Join the server that matches your peak rank."
        ),
        color=0x5865F2
    )
    embed.add_field(name="🔵 Challengers (C1–C3)", value="https://discord.gg/J6Yn4RbdPh", inline=False)
    embed.add_field(name="🟣 Legends (GC1–GC2)", value="https://discord.gg/MVm3wTZnHY", inline=False)
    embed.add_field(name="🔴 Titans (GC3–2K SSL)", value="https://discord.gg/SNJhsp8jXZ", inline=False)
    embed.add_field(
        name="🌐 Rocket Rivals Hub — Join this regardless of division",
        value=(
            "https://discord.gg/UMkFkr7r\n"
            "• Get verified and checked for alt accounts\n"
            "• See announcements and upcoming tournaments\n"
            "• Connect with other competing teams and orgs"
        ),
        inline=False
    )
    embed.set_footer(text="Pentathletes • Rocket Rivals")
    await interaction.response.send_message(embed=embed)


async def main():
    async with bot:
        await bot.load_extension("announcements")
        await bot.load_extension("farewell")
        await bot.load_extension("vc")
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
