"""Moderation — bulk message deletion.

/purge amount [user] [contains] — delete recent messages in the current
channel, optionally only those from a specific user and/or containing text.

Discord only allows bulk deletion of messages younger than 14 days, so
older messages are skipped. Bot needs Manage Messages + Read Message
History in the channel. Administrator-only: hidden from non-admins AND
hard-checked in code, so server permission overrides can't expose it.
"""

from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

SCAN_LIMIT = 1000  # how far back to look when a filter is set
BULK_WINDOW = timedelta(days=13, hours=23)  # stay inside Discord's 14-day bulk limit


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_app_command_error(self, interaction: discord.Interaction,
                                    error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            msg = "❌ Only **administrators** can use this command."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return
        raise error

    @app_commands.command(name="purge", description="Delete recent messages in this channel")
    @app_commands.describe(
        amount="How many messages to delete (1–100)",
        user="Only delete messages from this user",
        contains="Only delete messages containing this text",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def purge(self, interaction: discord.Interaction,
                    amount: app_commands.Range[int, 1, 100],
                    user: discord.Member | None = None,
                    contains: str | None = None):
        await interaction.response.defer(ephemeral=True)

        matched = 0

        def check(message: discord.Message) -> bool:
            nonlocal matched
            if matched >= amount:
                return False
            if user and message.author.id != user.id:
                return False
            if contains and contains.lower() not in message.content.lower():
                return False
            matched += 1
            return True

        filtered = bool(user or contains)
        try:
            deleted = await interaction.channel.purge(
                limit=SCAN_LIMIT if filtered else amount,
                check=check,
                after=datetime.now(timezone.utc) - BULK_WINDOW,
                oldest_first=False,
                reason=f"/purge by {interaction.user}",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I need **Manage Messages** and **Read Message History** in this channel.")
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Purge failed: {e}")
            return

        parts = [f"🧹 Deleted **{len(deleted)}** message{'s' if len(deleted) != 1 else ''}"]
        if user:
            parts.append(f"from {user.mention}")
        if contains:
            parts.append(f"containing `{contains}`")
        note = ""
        if filtered and len(deleted) < amount:
            note = f"\n(That's all that matched in the last {SCAN_LIMIT} messages / 14 days.)"
        elif not filtered and len(deleted) < amount:
            note = "\n(Messages older than 14 days can't be bulk-deleted.)"
        await interaction.followup.send(" ".join(parts) + "." + note)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
    print("Loaded moderation extension")
