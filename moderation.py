"""Moderation — bulk message deletion (admin only).

/purge subcommands, registered alphabetically so they list in ABC order:
  all      — wipe EVERY message by cloning + deleting the channel (confirm button)
  bots     — delete recent messages sent by bots
  contains — delete recent messages containing text
  last     — delete the last N messages
  user     — delete recent messages from one user

Discord only bulk-deletes messages younger than 14 days, so filtered
purges skip older ones (/purge all has no such limit — it recreates the
channel). Bot needs Manage Messages + Read Message History; /purge all
also needs Manage Channels. Administrator-only: hidden from non-admins
AND hard-checked in code, so permission overrides can't expose it.
"""

from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

SCAN_LIMIT = 1000  # how far back to look when a filter is set
BULK_WINDOW = timedelta(days=13, hours=23)  # stay inside Discord's 14-day bulk limit


def admin_only():
    return app_commands.checks.has_permissions(administrator=True)


class ConfirmWipe(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @discord.ui.button(label="Yes, wipe everything", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self.stop()
        await interaction.response.edit_message(content="💣 Wiping channel…", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled — nothing deleted.", view=None)


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

    # ── shared deletion logic ────────────────────────────────────────────────

    async def _purge(self, interaction: discord.Interaction, amount: int,
                     predicate=None, label: str = ""):
        await interaction.response.defer(ephemeral=True)

        matched = 0

        def check(message: discord.Message) -> bool:
            nonlocal matched
            if matched >= amount:
                return False
            if predicate and not predicate(message):
                return False
            matched += 1
            return True

        filtered = predicate is not None
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

        n = len(deleted)
        msg = f"🧹 Deleted **{n}** message{'s' if n != 1 else ''}{label}."
        if n < amount:
            if filtered:
                msg += f"\n(That's all that matched in the last {SCAN_LIMIT} messages / 14 days.)"
            else:
                msg += "\n(Messages older than 14 days can't be bulk-deleted — use `/purge all`.)"
        await interaction.followup.send(msg)

    # ── /purge group (subcommands registered in ABC order) ──────────────────

    purge = app_commands.Group(
        name="purge",
        description="Delete messages in this channel (admin only)",
        default_permissions=discord.Permissions(administrator=True),
    )

    @purge.command(name="all", description="Wipe EVERY message by recreating this channel")
    @admin_only()
    async def purge_all(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "This only works in a regular text channel.", ephemeral=True)
            return
        view = ConfirmWipe(interaction.user.id)
        await interaction.response.send_message(
            f"⚠️ This wipes **every message** in {channel.mention} by deleting and "
            f"recreating the channel.\n"
            f"Pins are lost and the **channel ID changes** — re-run `/setannouncements` "
            f"or `/setfarewell` if they point here.",
            view=view, ephemeral=True,
        )
        await view.wait()
        if not view.confirmed:
            return
        try:
            new_channel = await channel.clone(reason=f"/purge all by {interaction.user}")
            await new_channel.edit(position=channel.position)
            await channel.delete(reason=f"/purge all by {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I need **Manage Channels** to do that.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Wipe failed: {e}", ephemeral=True)
            return
        try:
            await new_channel.send(f"🧹 Channel wiped by {interaction.user.mention}.")
        except discord.HTTPException:
            pass

    @purge.command(name="bots", description="Delete recent messages sent by bots")
    @app_commands.describe(amount="How many bot messages to delete (1–100)")
    @admin_only()
    async def purge_bots(self, interaction: discord.Interaction,
                         amount: app_commands.Range[int, 1, 100]):
        await self._purge(interaction, amount,
                          predicate=lambda m: m.author.bot,
                          label=" from bots")

    @purge.command(name="contains", description="Delete recent messages containing specific text")
    @app_commands.describe(text="Delete messages containing this text",
                           amount="How many matching messages to delete (1–100)")
    @admin_only()
    async def purge_contains(self, interaction: discord.Interaction, text: str,
                             amount: app_commands.Range[int, 1, 100] = 100):
        needle = text.lower()
        await self._purge(interaction, amount,
                          predicate=lambda m: needle in m.content.lower(),
                          label=f" containing `{text}`")

    @purge.command(name="last", description="Delete the last N messages in this channel")
    @app_commands.describe(amount="How many messages to delete (1–100)")
    @admin_only()
    async def purge_last(self, interaction: discord.Interaction,
                         amount: app_commands.Range[int, 1, 100]):
        await self._purge(interaction, amount)

    @purge.command(name="user", description="Delete recent messages from a specific user")
    @app_commands.describe(user="Whose messages to delete",
                           amount="How many of their messages to delete (1–100)")
    @admin_only()
    async def purge_user(self, interaction: discord.Interaction, user: discord.Member,
                         amount: app_commands.Range[int, 1, 100] = 100):
        await self._purge(interaction, amount,
                          predicate=lambda m: m.author.id == user.id,
                          label=f" from {user.mention}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
    print("Loaded moderation extension")
