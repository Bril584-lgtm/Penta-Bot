"""Rocket League announcements — RLCS match alerts, game updates, and news.

Categories, each routed to its own channel via /setannouncements:
  rlcs    — RLCS matches starting within the next hour (Liquipedia)
  updates — official Psyonix/Steam announcements and patch notes (Steam news API)
  news    — r/RocketLeagueEsports posts + external RL articles (Reddit RSS + Steam external feeds)
"""

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "announce_state.json")

USER_AGENT = "Penta-Bot/1.0 (https://github.com/Bril584-lgtm/Penta-Bot; batmanmovie09@gmail.com)"
LIQUIPEDIA_URL = (
    "https://liquipedia.net/rocketleague/api.php"
    "?action=parse&page=Liquipedia:Matches&format=json&prop=text"
)
STEAM_NEWS_URL = (
    "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
    "?appid=252950&count=10&maxlength=400"
)
REDDIT_RSS_URL = "https://www.reddit.com/r/RocketLeagueEsports/.rss"
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_OAUTH_URL = "https://oauth.reddit.com/r/RocketLeagueEsports/new?limit=25&raw_json=1"
GOOGLE_NEWS_URL = (
    "https://news.google.com/rss/search"
    "?q=%22rocket+league%22+when:7d&hl=en-US&gl=US&ceid=US:en"
)

CATEGORIES = ["rlcs", "updates", "news"]
COLORS = {"rlcs": 0xE67E22, "updates": 0x57F287, "news": 0x5865F2}
SEEN_CAP = 500
ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}


# ── parsing (pure functions, no Discord) ─────────────────────────────────────

def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[/?[a-z*][^\]]*\]", "", text)  # bbcode from Steam posts
    text = re.sub(r"\s+", " ", text).strip()
    return text


def pretty_tournament(title: str) -> str:
    title = title.split("#")[0]
    title = title.replace("Rocket League Championship Series", "RLCS")
    return " — ".join(p.strip() for p in title.split("/") if p.strip())


def parse_liquipedia_matches(html: str) -> list:
    """Returns [{ts, team1, team2, tournament, url}] for every match block."""
    matches = []
    for block in re.split(r'<div class="match-info">', html)[1:]:
        ts = re.search(r'data-timestamp="(\d+)"', block)
        names = re.findall(r'<span class="name"[^>]*>(?:<a[^>]*>)?([^<]+)', block)
        tour = re.search(
            r'match-info-tournament.*?<a href="(/rocketleague/[^"]+)" title="([^"]+)"',
            block, re.S,
        )
        if not ts or len(names) < 2 or not tour:
            continue
        matches.append({
            "ts": int(ts.group(1)),
            "team1": names[0].strip(),
            "team2": names[1].strip(),
            "tournament": pretty_tournament(tour.group(2)),
            "url": "https://liquipedia.net" + tour.group(1).split("#")[0],
        })
    return matches


def parse_reddit_atom(xml_text: str) -> list:
    """Returns [{id, title, url, author}] for every Atom entry."""
    entries = []
    root = ET.fromstring(xml_text)
    for entry in root.findall("a:entry", ATOM_NS):
        eid = entry.findtext("a:id", "", ATOM_NS)
        title = entry.findtext("a:title", "", ATOM_NS)
        author = entry.findtext("a:author/a:name", "", ATOM_NS)
        link = entry.find("a:link", ATOM_NS)
        url = link.get("href") if link is not None else ""
        if eid and title:
            entries.append({"id": eid, "title": title, "url": url, "author": author})
    return entries


def parse_gnews_rss(xml_text: str) -> list:
    """Returns [{id, title, url, author}] from a Google News RSS feed."""
    entries = []
    root = ET.fromstring(xml_text)
    for item in root.findall(".//item"):
        guid = item.findtext("guid", "")
        title = item.findtext("title", "")
        if guid and title:
            entries.append({
                "id": guid,
                "title": title,
                "url": item.findtext("link", ""),
                "author": item.findtext("source", "Google News"),
            })
    return entries


# ── cog ──────────────────────────────────────────────────────────────────────

class Announcements(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.state = self._load_state()
        self._reddit_token = None
        self._reddit_token_expiry = 0.0
        self.rlcs_loop.start()
        self.updates_loop.start()
        self.news_loop.start()

    def cog_unload(self):
        self.rlcs_loop.cancel()
        self.updates_loop.cancel()
        self.news_loop.cancel()

    # ── state ────────────────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        state = {"channels": {}, "seen": {c: [] for c in CATEGORIES}}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                state["channels"] = loaded.get("channels", {})
                for c in CATEGORIES:
                    state["seen"][c] = loaded.get("seen", {}).get(c, [])
            except (json.JSONDecodeError, OSError) as e:
                print(f"[announce] Could not read state file, starting fresh: {e}")
        return state

    def _save_state(self):
        for c in CATEGORIES:
            self.state["seen"][c] = self.state["seen"][c][-SEEN_CAP:]
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def _channel_for(self, category: str):
        cid = self.state["channels"].get(category)
        return self.bot.get_channel(cid) if cid else None

    def _mark_seen(self, category: str, key: str):
        self.state["seen"][category].append(key)

    def _is_seen(self, category: str, key: str) -> bool:
        return key in self.state["seen"][category]

    # ── http ─────────────────────────────────────────────────────────────────

    async def _get(self, url: str, as_json: bool):
        headers = {"User-Agent": USER_AGENT}
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                return await (resp.json() if as_json else resp.text())

    # ── fetchers ─────────────────────────────────────────────────────────────

    async def _fetch_rlcs(self) -> list:
        data = await self._get(LIQUIPEDIA_URL, as_json=True)
        matches = parse_liquipedia_matches(data["parse"]["text"]["*"])
        return [
            m for m in matches
            if "RLCS" in m["tournament"]
            and not (m["team1"] == "TBD" and m["team2"] == "TBD")
        ]

    async def _fetch_steam(self) -> list:
        data = await self._get(STEAM_NEWS_URL, as_json=True)
        return data.get("appnews", {}).get("newsitems", [])

    async def _fetch_news(self) -> list:
        """Google News is the primary source (works from cloud IPs, no signup).
        Reddit is added best-effort: it 429s from datacenter IPs unless
        REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are set for the real API."""
        entries = parse_gnews_rss(await self._get(GOOGLE_NEWS_URL, as_json=False))
        try:
            entries += await self._fetch_reddit()
        except Exception as e:
            print(f"[announce:news] reddit unavailable (fine, google news still works): {e}")
        return entries

    async def _fetch_reddit(self) -> list:
        client_id = os.getenv("REDDIT_CLIENT_ID")
        client_secret = os.getenv("REDDIT_CLIENT_SECRET")
        if client_id and client_secret:
            return await self._fetch_reddit_oauth(client_id, client_secret)
        return parse_reddit_atom(await self._get(REDDIT_RSS_URL, as_json=False))

    async def _fetch_reddit_oauth(self, client_id: str, client_secret: str) -> list:
        now = time.time()
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}, timeout=timeout) as session:
            if not self._reddit_token or now >= self._reddit_token_expiry:
                auth = aiohttp.BasicAuth(client_id, client_secret)
                async with session.post(REDDIT_TOKEN_URL, auth=auth,
                                        data={"grant_type": "client_credentials"}) as resp:
                    resp.raise_for_status()
                    tok = await resp.json()
                self._reddit_token = tok["access_token"]
                self._reddit_token_expiry = now + tok.get("expires_in", 3600) - 300
            async with session.get(REDDIT_OAUTH_URL,
                                   headers={"Authorization": f"Bearer {self._reddit_token}"}) as resp:
                resp.raise_for_status()
                data = await resp.json()
        entries = []
        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {})
            if d.get("name") and d.get("title"):
                entries.append({
                    "id": d["name"],  # t3_xxx — same format as RSS entry ids
                    "title": d["title"],
                    "url": "https://www.reddit.com" + d.get("permalink", ""),
                    "author": "/u/" + d.get("author", "unknown"),
                })
        return entries

    # ── embed builders ───────────────────────────────────────────────────────

    def _rlcs_embed(self, m: dict) -> discord.Embed:
        embed = discord.Embed(
            title=f"🚨 RLCS MATCH STARTING SOON — {m['team1']} vs {m['team2']}",
            description=f"**{m['tournament']}**\nKickoff: <t:{m['ts']}:F> (<t:{m['ts']}:R>)",
            url=m["url"],
            color=COLORS["rlcs"],
        )
        embed.set_footer(text="Pentathletes • RLCS via Liquipedia")
        return embed

    def _steam_embed(self, item: dict, official: bool) -> discord.Embed:
        embed = discord.Embed(
            title=("🛠️ " if official else "📰 ") + item.get("title", "Rocket League News"),
            description=strip_html(item.get("contents", ""))[:500],
            url=item.get("url", ""),
            color=COLORS["updates" if official else "news"],
        )
        source = "Psyonix (official)" if official else item.get("feedlabel", "External")
        embed.set_footer(text=f"Pentathletes • {source}")
        if item.get("date"):
            embed.timestamp = datetime.fromtimestamp(item["date"], tz=timezone.utc)
        return embed

    def _news_embed(self, entry: dict) -> discord.Embed:
        embed = discord.Embed(
            title="📰 " + entry["title"][:250],
            url=entry["url"],
            description=f"via {entry['author']}",
            color=COLORS["news"],
        )
        embed.set_footer(text="Pentathletes • Rocket League News")
        return embed

    # ── loops ────────────────────────────────────────────────────────────────

    @tasks.loop(minutes=30)
    async def rlcs_loop(self):
        channel = self._channel_for("rlcs")
        if not channel:
            return
        try:
            matches = await self._fetch_rlcs()
        except Exception as e:
            print(f"[announce:rlcs] fetch failed: {e}")
            return
        now = time.time()
        for m in matches:
            key = f"{m['ts']}|{m['team1']}|{m['team2']}|{m['tournament']}"
            if self._is_seen("rlcs", key):
                continue
            if 0 <= m["ts"] - now <= 3600:  # starting within the hour
                await channel.send(embed=self._rlcs_embed(m))
                self._mark_seen("rlcs", key)
        self._save_state()

    @tasks.loop(minutes=30)
    async def updates_loop(self):
        updates_ch = self._channel_for("updates")
        news_ch = self._channel_for("news")
        if not updates_ch and not news_ch:
            return
        try:
            items = await self._fetch_steam()
        except Exception as e:
            print(f"[announce:updates] fetch failed: {e}")
            return
        first_run = not self.state["seen"]["updates"]
        for item in reversed(items):  # oldest first so channel reads chronologically
            gid = str(item.get("gid", ""))
            if not gid or self._is_seen("updates", gid):
                continue
            self._mark_seen("updates", gid)
            if first_run:
                continue  # seed silently, don't spam history
            official = item.get("feedname") == "steam_community_announcements"
            target = updates_ch if official else news_ch
            if target:
                await target.send(embed=self._steam_embed(item, official))
        if first_run:
            print(f"[announce:updates] first run — seeded {len(items)} items silently")
        self._save_state()

    @tasks.loop(minutes=15)
    async def news_loop(self):
        channel = self._channel_for("news")
        if not channel:
            return
        try:
            entries = await self._fetch_news()
        except Exception as e:
            print(f"[announce:news] fetch failed: {e}")
            return
        first_run = not self.state["seen"]["news"]
        for entry in reversed(entries):
            if self._is_seen("news", entry["id"]):
                continue
            self._mark_seen("news", entry["id"])
            if first_run:
                continue
            await channel.send(embed=self._news_embed(entry))
        if first_run:
            print(f"[announce:news] first run — seeded {len(entries)} posts silently")
        self._save_state()

    @rlcs_loop.before_loop
    @updates_loop.before_loop
    @news_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    # ── slash commands ───────────────────────────────────────────────────────

    category_choices = [
        app_commands.Choice(name="RLCS match alerts", value="rlcs"),
        app_commands.Choice(name="Game updates (official)", value="updates"),
        app_commands.Choice(name="RL news (Reddit + articles)", value="news"),
    ]

    @app_commands.command(name="setannouncements", description="Route an announcement category to a channel")
    @app_commands.describe(category="What to announce", channel="Channel to post in")
    @app_commands.choices(category=category_choices)
    @app_commands.default_permissions(manage_guild=True)
    async def set_announcements(self, interaction: discord.Interaction,
                                category: app_commands.Choice[str],
                                channel: discord.TextChannel):
        self.state["channels"][category.value] = channel.id
        self._save_state()
        await interaction.response.send_message(
            f"✅ **{category.name}** will now post in {channel.mention}."
        )

    @app_commands.command(name="announcements", description="Show current announcement channel routing")
    async def show_announcements(self, interaction: discord.Interaction):
        lines = []
        names = {c.value: c.name for c in self.category_choices}
        for cat in CATEGORIES:
            ch = self._channel_for(cat)
            lines.append(f"• **{names[cat]}** → {ch.mention if ch else '*not set*'}")
        embed = discord.Embed(title="📣 Announcement Routing", description="\n".join(lines),
                              color=0x5865F2)
        embed.set_footer(text="Set with /setannouncements • Stop with /stopannouncements")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="stopannouncements", description="Stop an announcement category")
    @app_commands.choices(category=category_choices)
    @app_commands.default_permissions(manage_guild=True)
    async def stop_announcements(self, interaction: discord.Interaction,
                                 category: app_commands.Choice[str]):
        removed = self.state["channels"].pop(category.value, None)
        self._save_state()
        msg = f"🛑 **{category.name}** announcements stopped." if removed \
            else f"**{category.name}** wasn't routed anywhere."
        await interaction.response.send_message(msg)

    @app_commands.command(name="testannouncement", description="Post the latest item from a category right now")
    @app_commands.choices(category=category_choices)
    @app_commands.default_permissions(manage_guild=True)
    async def test_announcement(self, interaction: discord.Interaction,
                                category: app_commands.Choice[str]):
        await interaction.response.defer()
        cat = category.value
        target = self._channel_for(cat) or interaction.channel
        try:
            if cat == "rlcs":
                matches = await self._fetch_rlcs()
                upcoming = [m for m in matches if m["ts"] > time.time()]
                if not upcoming:
                    await interaction.followup.send("No upcoming RLCS matches found on Liquipedia right now.")
                    return
                await target.send(embed=self._rlcs_embed(min(upcoming, key=lambda m: m["ts"])))
            elif cat == "updates":
                items = await self._fetch_steam()
                official = [i for i in items if i.get("feedname") == "steam_community_announcements"]
                item = (official or items)[0]
                await target.send(embed=self._steam_embed(item, bool(official)))
            else:
                entries = await self._fetch_news()
                if not entries:
                    await interaction.followup.send("No news found.")
                    return
                await target.send(embed=self._news_embed(entries[0]))
            await interaction.followup.send(f"✅ Test posted to {target.mention}.")
        except aiohttp.ClientResponseError as e:
            if e.status == 429 and "reddit" in str(e.request_info.url):
                await interaction.followup.send(
                    "❌ Reddit is rate-limiting this server's IP. "
                    "Set `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` in the host's "
                    "environment variables to use the authenticated API instead."
                )
            else:
                await interaction.followup.send(f"❌ Test failed: {e}")
        except Exception as e:
            await interaction.followup.send(f"❌ Test failed: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Announcements(bot))
    print("Loaded announcements extension")
