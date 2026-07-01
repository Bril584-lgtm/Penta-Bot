# Plan — Rocket League Announcements System (2026-07-01)

Goal: Penta-Bot auto-posts RLCS match alerts, official game updates, and RL news
into separately configurable channels.

## Data sources (all verified working 2026-07-01)

| Category | Source | Notes |
|---|---|---|
| `rlcs` | `https://liquipedia.net/rocketleague/api.php?action=parse&page=Liquipedia:Matches&format=json&prop=text` | Requires gzip + User-Agent with contact info (403/406 otherwise). Match blocks = `<div class="match-info">` with `data-timestamp`, `<span class="name">` teams, `match-info-tournament` title. Poll max every 30 min per API ToS. |
| `updates` | `https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid=252950&count=10&maxlength=400` | `feedname == "steam_community_announcements"` = official Psyonix posts → updates channel. Other feeds (RPS, GamingOnLinux…) → news channel. Dedup by `gid`. |
| `news` | `https://www.reddit.com/r/RocketLeagueEsports/.rss` | Atom XML (JSON API returns 403 from this IP; RSS works). Parse with stdlib ElementTree. Dedup by entry `id`. |

Octane.gg zsr API is dead (connection refused) — do not use.

## Tasks

1. **`announcements.py`** (new file, discord.py Cog)
   - Pure parse functions: `parse_liquipedia_matches(html)`, `parse_reddit_atom(xml)`, `strip_html(text)` — testable without Discord.
   - State in `announce_state.json`: `{"channels": {category: channel_id}, "seen": {category: [ids]}}`. Seen lists capped at 500.
   - Three `tasks.loop`s: rlcs every 30 min, updates every 30 min, news every 15 min.
   - RLCS rule: announce matches starting within the next 60 min, skip TBD vs TBD, filter tournament contains "Rocket League Championship Series" or "RLCS". Dedup key `ts|team1|team2|tournament`.
   - First run per category: seed seen-list silently (no history spam).
   - Slash commands (Manage Server only):
     - `/setannouncements category channel` — route a category to a channel
     - `/announcements` — show current routing
     - `/stopannouncements category` — stop a category
     - `/testannouncement category` — post the latest item now to verify
2. **`bot.py`** — replace `bot.run()` with async main that does `bot.load_extension("announcements")` before `bot.start()`.
3. **Verify**: `python -m py_compile` both files; run parse functions against live data; boot the bot and confirm command sync + loops start.

## Expected output
Bot log shows `Loaded announcements extension`, `Synced 10+ slash commands`, and each loop's first-run seed message.
