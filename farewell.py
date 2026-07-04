"""Farewell messages — posts a goodbye when a member leaves the server.

Route with /setfarewell, check with /farewell, stop with /stopfarewell,
preview with /testfarewell. Custom messages support placeholders:
  {name}   — display name of the member who left
  {user}   — username (e.g. bril584)
  {server} — server name
  {count}  — member count after they left
"""

import json
import os

import discord
from discord import app_commands
from discord.ext import commands

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "farewell_state.json")

DEFAULT_MESSAGE = "**{name}** has left {server}. Safe travels! o7"
FAREWELL_COLOR = 0xED4245  # discord red


def render_message(template: str, name: str, user: str, server: str, count: int) -> str:
    """Substitute placeholders without str.format so stray braces can't crash."""
    return (template
            .replace("{name}", name)
            .replace("{user}", user)
            .replace("{server}", server)
            .replace("{count}", str(count)))


class Farewell(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load_state()

    # ── state ────────────────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded.get("guilds"), dict):
                    return loaded
            except (json.JSONDecodeError, OSError) as e:
                print(f"[farewell] Could not read state file, starting fresh: {e}")
        return {"guilds": {}}

    def _save_state(self):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def _config_for(self, guild_id: int) -> dict | None:
        return self.state["guilds"].get(str(guild_id))

    # ── embed ────────────────────────────────────────────────────────────────

    def _farewell_embed(self, member: discord.Member | discord.User,
                        guild: discord.Guild, template: str) -> discord.Embed:
        count = guild.member_count or 0
        text = render_message(template, member.display_name, member.name,
                              guild.name, count)
        embed = discord.Embed(title="👋 Farewell", description=text, color=FAREWELL_COLOR)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Pentathletes • {count} members remain")
        return embed

    # ── event ────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        config = self._config_for(member.guild.id)
        if not config:
            return
        channel = self.bot.get_channel(config["channel_id"])
        if not channel:
            return
        try:
            await channel.send(embed=self._farewell_embed(
                member, member.guild, config.get("message") or DEFAULT_MESSAGE))
        except discord.HTTPException as e:
            print(f"[farewell] Could not post farewell for {member}: {e}")

    # ── slash commands ───────────────────────────────────────────────────────

    @app_commands.command(name="setfarewell", description="Post a farewell message here whenever someone leaves")
    @app_commands.describe(
        channel="Channel to post farewells in",
        message="Optional custom message — placeholders: {name} {user} {server} {count}",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def set_farewell(self, interaction: discord.Interaction,
                           channel: discord.TextChannel,
                           message: str | None = None):
        self.state["guilds"][str(interaction.guild_id)] = {
            "channel_id": channel.id,
            "message": message,
        }
        self._save_state()
        note = f"\nCustom message: `{message}`" if message else ""
        await interaction.response.send_message(
            f"✅ Farewell messages will now post in {channel.mention}.{note}\n"
            f"Preview with `/testfarewell`."
        )

    @app_commands.command(name="farewell", description="Show the current farewell setup")
    async def show_farewell(self, interaction: discord.Interaction):
        config = self._config_for(interaction.guild_id)
        if not config:
            await interaction.response.send_message(
                "Farewell messages are **off**. Set them up with `/setfarewell`.")
            return
        channel = self.bot.get_channel(config["channel_id"])
        embed = discord.Embed(
            title="👋 Farewell Setup",
            description=(
                f"**Channel:** {channel.mention if channel else '*deleted channel — re-run /setfarewell*'}\n"
                f"**Message:** `{config.get('message') or DEFAULT_MESSAGE}`"
            ),
            color=FAREWELL_COLOR,
        )
        embed.set_footer(text="Change with /setfarewell • Stop with /stopfarewell")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="stopfarewell", description="Stop posting farewell messages")
    @app_commands.default_permissions(manage_guild=True)
    async def stop_farewell(self, interaction: discord.Interaction):
        removed = self.state["guilds"].pop(str(interaction.guild_id), None)
        self._save_state()
        msg = "🛑 Farewell messages stopped." if removed else "Farewell messages weren't set up."
        await interaction.response.send_message(msg)

    @app_commands.command(name="testfarewell", description="Preview the farewell message using you as the leaver")
    @app_commands.default_permissions(manage_guild=True)
    async def test_farewell(self, interaction: discord.Interaction):
        config = self._config_for(interaction.guild_id)
        if not config:
            await interaction.response.send_message(
                "Farewell messages aren't set up yet — run `/setfarewell` first.")
            return
        target = self.bot.get_channel(config["channel_id"]) or interaction.channel
        await target.send(embed=self._farewell_embed(
            interaction.user, interaction.guild, config.get("message") or DEFAULT_MESSAGE))
        await interaction.response.send_message(f"✅ Test farewell posted to {target.mention}.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Farewell(bot))
    print("Loaded farewell extension")
