"""Temp voice channels — join-to-create hubs with owner controls.

Admin runs /setupvc to create a "➕ Create VC" hub channel; it can be run
again (with a different name/category) to add more hubs — a guild isn't
limited to one. Anyone who joins a hub gets their own VC spawned and is
moved into it as owner. Owners manage their VC with /vc claim, kick, limit,
lock, mute, rename, unlock, unmute (registered alphabetically so they list
in ABC order). Empty temp VCs are deleted automatically, including
leftovers swept on startup after a redeploy.

Bot needs: Manage Channels + Move Members (server-wide or on the category).
"""

import json
import os

import discord
from discord import app_commands
from discord.ext import commands

from statepath import state_file

STATE_FILE = state_file("vc_state.json")

HUB_NAME = "➕ Create VC"
DEFAULT_CATEGORY = "🔊 Voice Lounge"


class TempVC(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load_state()

    # ── state ────────────────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        state = {"guilds": {}, "temp": {}}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                state["guilds"] = loaded.get("guilds", {})
                state["temp"] = loaded.get("temp", {})
            except (json.JSONDecodeError, OSError) as e:
                print(f"[vc] Could not read state file, starting fresh: {e}")
        for gid, cfg in state["guilds"].items():
            if "hub_id" in cfg and "hubs" not in cfg:
                # migrate pre-multi-hub format (single hub_id/category_id per guild)
                state["guilds"][gid] = {
                    "hubs": {str(cfg["hub_id"]): {"category_id": cfg.get("category_id", 0)}}
                }
        return state

    def _save_state(self):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def _guild_cfg(self, guild_id: int) -> dict | None:
        return self.state["guilds"].get(str(guild_id))

    def _hubs(self, guild_id: int) -> dict:
        """hub_id (str) -> {"category_id": int} for this guild."""
        cfg = self._guild_cfg(guild_id)
        return cfg.get("hubs", {}) if cfg else {}

    # ── lifecycle ────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self):
        """Sweep empty temp VCs left over from before a restart/redeploy."""
        for gid, cfg in list(self.state["guilds"].items()):
            guild = self.bot.get_guild(int(gid))
            if not guild:
                continue
            hubs = cfg.get("hubs", {})
            hub_ids = {int(hid) for hid in hubs}
            category_ids = {info.get("category_id", 0) for info in hubs.values()}
            for cat_id in category_ids:
                category = guild.get_channel(cat_id)
                if not category:
                    continue
                for ch in category.voice_channels:
                    if ch.id in hub_ids:
                        continue
                    if not ch.members:
                        try:
                            await ch.delete(reason="Temp VC cleanup (empty after restart)")
                        except discord.HTTPException as e:
                            print(f"[vc] cleanup failed for #{ch.name}: {e}")
                        self.state["temp"].pop(str(ch.id), None)
        self._save_state()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                    before: discord.VoiceState,
                                    after: discord.VoiceState):
        if member.bot:
            return
        # joined a hub → spawn a personal VC
        if after.channel and str(after.channel.id) in self._hubs(member.guild.id):
            await self._spawn(member, after.channel)
        # left a temp VC that is now empty → delete it
        if (before.channel
                and str(before.channel.id) in self.state["temp"]
                and not before.channel.members):
            try:
                await before.channel.delete(reason="Temp VC empty")
            except discord.HTTPException as e:
                print(f"[vc] delete failed for #{before.channel.name}: {e}")
            self.state["temp"].pop(str(before.channel.id), None)
            self._save_state()

    async def _spawn(self, member: discord.Member, hub: discord.VoiceChannel):
        overwrites = {
            member: discord.PermissionOverwrite(
                connect=True, speak=True, move_members=True, manage_channels=True)
        }
        try:
            vc = await member.guild.create_voice_channel(
                name=f"🔊 {member.display_name}'s VC"[:100],
                category=hub.category,
                overwrites=overwrites,
                reason=f"Temp VC for {member}",
            )
        except discord.HTTPException as e:
            print(f"[vc] create failed (does the bot have Manage Channels?): {e}")
            return
        try:
            await member.move_to(vc, reason="Moving owner into their temp VC")
        except discord.HTTPException:
            # they left the hub before we could move them — don't leave an orphan
            try:
                await vc.delete(reason="Owner left before move")
            except discord.HTTPException:
                pass
            return
        self.state["temp"][str(vc.id)] = member.id
        self._save_state()

    # ── owner helpers ────────────────────────────────────────────────────────

    def _current_temp_vc(self, interaction: discord.Interaction):
        """Temp VC the invoker is connected to, or None."""
        voice = getattr(interaction.user, "voice", None)
        ch = voice.channel if voice else None
        if ch and str(ch.id) in self.state["temp"]:
            return ch
        return None

    def _owned_vc(self, interaction: discord.Interaction):
        """(channel, error) — channel only if invoker is in a temp VC they own."""
        ch = self._current_temp_vc(interaction)
        if not ch:
            return None, f"You're not in a temp VC. Join **{HUB_NAME}** to create one."
        if self.state["temp"][str(ch.id)] != interaction.user.id:
            return None, "You don't own this VC. Ask the owner, or use `/vc claim` if they left."
        return ch, None

    # ── /setupvc ─────────────────────────────────────────────────────────────

    @app_commands.command(name="setupvc", description="Create a join-to-create VC hub for this server (can be run more than once for multiple hubs)")
    @app_commands.describe(category="Category to put temp VCs in (default: creates one)",
                           name="Name for this hub channel (default: \"➕ Create VC\")")
    @app_commands.default_permissions(manage_guild=True)
    async def setup_vc(self, interaction: discord.Interaction,
                       category: discord.CategoryChannel | None = None,
                       name: str | None = None):
        await interaction.response.defer()
        guild = interaction.guild
        hub_name = (name or HUB_NAME)[:100]
        try:
            if category is None:
                category = await guild.create_category(DEFAULT_CATEGORY, reason="Temp VC system")
            hub = await guild.create_voice_channel(hub_name, category=category,
                                                   reason="Temp VC hub")
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"❌ Couldn't create channels — make sure I have **Manage Channels**: {e}")
            return
        cfg = self.state["guilds"].setdefault(str(guild.id), {"hubs": {}})
        cfg["hubs"][str(hub.id)] = {"category_id": category.id}
        self._save_state()
        total = len(cfg["hubs"])
        await interaction.followup.send(
            f"✅ Temp VC hub ready: join {hub.mention} and you'll get your own channel.\n"
            f"Owners control theirs with `/vc lock`, `/vc kick`, `/vc limit`, `/vc rename`, and more.\n"
            + (f"This server now has **{total} hubs** set up." if total > 1 else "")
        )

    @app_commands.command(name="removevc", description="Disable a join-to-create VC hub (or all, if only one exists)")
    @app_commands.describe(hub="Which hub to remove (only needed if you have more than one)")
    @app_commands.default_permissions(manage_guild=True)
    async def remove_vc(self, interaction: discord.Interaction,
                        hub: discord.VoiceChannel | None = None):
        hubs = self._hubs(interaction.guild_id)
        if not hubs:
            await interaction.response.send_message("Temp VCs weren't set up.")
            return
        if hub is None:
            if len(hubs) > 1:
                mentions = ", ".join(f"<#{hid}>" for hid in hubs)
                await interaction.response.send_message(
                    f"This server has multiple hubs ({mentions}) — specify which one with the `hub` option.",
                    ephemeral=True)
                return
            target_id = next(iter(hubs))
        else:
            target_id = str(hub.id)
            if target_id not in hubs:
                await interaction.response.send_message(
                    f"{hub.mention} isn't a temp VC hub.", ephemeral=True)
                return
        del hubs[target_id]
        if not hubs:
            self.state["guilds"].pop(str(interaction.guild_id), None)
        self._save_state()
        hub_channel = interaction.guild.get_channel(int(target_id))
        if hub_channel:
            try:
                await hub_channel.delete(reason="Temp VC hub disabled")
            except discord.HTTPException:
                pass
        remaining = f" {len(hubs)} hub(s) still active." if hubs else ""
        await interaction.response.send_message(
            f"🛑 Hub disabled.{remaining} Existing temp channels will still auto-delete when empty.")

    # ── /vc group (subcommands registered in ABC order) ─────────────────────

    vc = app_commands.Group(name="vc", description="Control your temp voice channel")

    @vc.command(name="claim", description="Take ownership of this VC if the owner left")
    async def vc_claim(self, interaction: discord.Interaction):
        ch = self._current_temp_vc(interaction)
        if not ch:
            await interaction.response.send_message(
                f"You're not in a temp VC. Join **{HUB_NAME}** to create one.", ephemeral=True)
            return
        owner_id = self.state["temp"].get(str(ch.id))
        if owner_id == interaction.user.id:
            await interaction.response.send_message("You already own this VC.", ephemeral=True)
            return
        if owner_id and any(m.id == owner_id for m in ch.members):
            await interaction.response.send_message(
                "The owner is still here — they keep control.", ephemeral=True)
            return
        self.state["temp"][str(ch.id)] = interaction.user.id
        self._save_state()
        await ch.set_permissions(interaction.user, connect=True, speak=True,
                                 move_members=True, manage_channels=True)
        await interaction.response.send_message(f"👑 You now own {ch.mention}.", ephemeral=True)

    @vc.command(name="kick", description="Kick someone out of your VC")
    @app_commands.describe(user="Who to kick", block="Also block them from rejoining")
    async def vc_kick(self, interaction: discord.Interaction,
                      user: discord.Member, block: bool = False):
        ch, err = self._owned_vc(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message("You can't kick yourself.", ephemeral=True)
            return
        if user not in ch.members:
            await interaction.response.send_message(f"{user.mention} isn't in your VC.", ephemeral=True)
            return
        await user.move_to(None, reason=f"Kicked from temp VC by {interaction.user}")
        note = ""
        if block:
            await ch.set_permissions(user, connect=False)
            note = " and blocked from rejoining"
        await interaction.response.send_message(f"👢 Kicked {user.mention}{note}.", ephemeral=True)

    @vc.command(name="limit", description="Set a user cap on your VC (0 = no limit)")
    @app_commands.describe(cap="Max users, 0–99")
    async def vc_limit(self, interaction: discord.Interaction,
                       cap: app_commands.Range[int, 0, 99]):
        ch, err = self._owned_vc(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await ch.edit(user_limit=cap)
        label = "no limit" if cap == 0 else f"{cap} users"
        await interaction.response.send_message(f"👥 {ch.mention} capped at **{label}**.", ephemeral=True)

    @vc.command(name="lock", description="Lock your VC so nobody else can join")
    async def vc_lock(self, interaction: discord.Interaction):
        ch, err = self._owned_vc(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await ch.set_permissions(interaction.guild.default_role, connect=False)
        await interaction.response.send_message(f"🔒 {ch.mention} is locked.", ephemeral=True)

    @vc.command(name="mute", description="Mute someone in your VC")
    @app_commands.describe(user="Who to mute")
    async def vc_mute(self, interaction: discord.Interaction, user: discord.Member):
        ch, err = self._owned_vc(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await ch.set_permissions(user, speak=False)
        await interaction.response.send_message(f"🔇 Muted {user.mention} in {ch.mention}.", ephemeral=True)

    @vc.command(name="rename", description="Rename your VC")
    @app_commands.describe(name="New channel name")
    async def vc_rename(self, interaction: discord.Interaction, name: str):
        ch, err = self._owned_vc(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await ch.edit(name=name[:100])
        await interaction.response.send_message(f"✏️ Renamed to **{name[:100]}**.", ephemeral=True)

    @vc.command(name="unlock", description="Unlock your VC so anyone can join")
    async def vc_unlock(self, interaction: discord.Interaction):
        ch, err = self._owned_vc(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await ch.set_permissions(interaction.guild.default_role, connect=None)
        await interaction.response.send_message(f"🔓 {ch.mention} is unlocked.", ephemeral=True)

    @vc.command(name="unmute", description="Unmute someone in your VC")
    @app_commands.describe(user="Who to unmute")
    async def vc_unmute(self, interaction: discord.Interaction, user: discord.Member):
        ch, err = self._owned_vc(interaction)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await ch.set_permissions(user, speak=None)
        await interaction.response.send_message(f"🔊 Unmuted {user.mention}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TempVC(bot))
    print("Loaded vc extension")
