import re
import time
from collections import deque

import gevent
import requests
from disco.bot import Plugin

from PunyBot import CONFIG
from PunyBot.constants import Messages
from PunyBot.models import DystopiaFeedCache


# Dystopia team ids -> human labels (2 = Punks, 3 = Corporation; see the stats schema).
TEAM_NAMES = {2: "The Punks", 3: "The Corporation"}

# Feed weapon display-string -> custom-emoji short name (the guild emoji is `dys_<value>`). The feed's
# `kill.weapon` field is a human display NAME (e.g. "MK-808 Rifle", "Minigun"), NOT a raw id - verified
# against the live /api/feed/events feed - so we map by name. Keys are matched case-insensitively after
# trimming (see `_weapon_emoji`); katana variants (Light/Medium/Heavy) all share the one katana emoji.
# Weapons with no entry here (e.g. "Cortex Bomb", "Leg Boosters") fall back to plain "with <weapon>"
# text. Emoji names Mike uploaded: katana, phist, machp, shotgun, ar, minigun, laser, mk808, ion,
# smartlocks, tesla, basilisk, boltgun, gl, rl, emp, frag, spider.
WEAPON_EMOJI = {
    "mk-808 rifle": "mk808",
    "assault rifle": "ar",
    "laser rifle": "laser",
    "tesla rifle": "tesla",
    "bolt gun": "boltgun",
    "boltgun": "boltgun",
    "minigun": "minigun",
    "machine pistol": "machp",
    "smartlock pistols": "smartlocks",
    "smartlock pistol": "smartlocks",
    "shotgun": "shotgun",
    "ion cannon": "ion",
    "basilisk": "basilisk",
    "rocket launcher": "rl",
    "grenade launcher": "gl",
    "frag grenade": "frag",
    "emp grenade": "emp",
    "emp": "emp",
    "spider grenade": "spider",
    "spider mine": "spider",
    "katana (light)": "katana",
    "katana (medium)": "katana",
    "katana (heavy)": "katana",
    "katana": "katana",
    "fatman fist": "phist",
    "power fist": "phist",
}

# How many events to pull per page. The feed API caps `limit` at 200. We page repeatedly (advancing
# the cursor) until the feed reports "caught up", so this is just the page size, not a ceiling on how
# much we can consume in one poll.
FETCH_LIMIT = 100

# dystopia-stats.com is behind Cloudflare Browser Integrity Check, which 403s (error 1010) any
# request whose User-Agent looks non-browser — including python-requests' default. Send a real
# browser UA so the feed poll gets through. (The game-server ingest is unaffected: it uses libcurl,
# which BIC lets through.) See dystopia-hub decisions/2026-07-14-bot-feed-403-browser-ua.md.
FEED_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

# Zero-pad width for the seconds half of a `since` cursor we build ourselves. Must match the stats
# API's cursor format (`<zero-padded-unix>:<id>`, 11 digits) so string comparison stays monotonic.
CURSOR_TS_PAD = 11

# Bound on the in-memory "already posted" id guard (belt-and-suspenders on top of cursor dedupe).
SEEN_MAXLEN = 2000

# Small pause between individual posts so a legitimate multi-event batch doesn't hammer Discord.
POST_SPACING_SECONDS = 0.4

# Events collected in one poll are BATCHED into combined messages (consecutive same-channel lines
# joined with newlines) instead of one message per event - a busy round's kill burst is one or two
# posts, not fifteen, which is what keeps the bot clear of Discord's ~5 msg/5 s per-channel limit.
BATCH_CHAR_LIMIT = 1900   # Discord message cap is 2000; leave headroom
BATCH_MAX_LINES = 25      # readability cap per message

# Safety cap on pages walked in a single drain, so a runaway feed can't spin forever (200k events).
MAX_DRAIN_PAGES = 2000

# -- chat relay (in-game ALL-chat -> feed channel) -------------------------------------------------
# Chat is user-controlled text from an untrusted game server, so every line is sanitized before it
# touches Discord. Two dangers to neutralize: (1) markdown/masked-link injection (a message like
# `[click](http://evil)` or `**bait**`), and (2) mention pings (`@everyone`, `@here`, `@user`,
# `<@id>`). The stats side already length-clamps + strips control chars; we defend again here.
CHAT_TEXT_MAX = 240          # hard length cap (matches the stats-side clamp); trimmed with an ellipsis
CHAT_FLUSH_MAX_LINES = 60    # per-flush line cap: a spam flood can't overflow the channel (excess -> note)
_ZWSP = "​"             # zero-width space, inserted to break mention tokens without visible change
# Backslash-escape the inline markdown metacharacters. `\` must be first in the class so it's escaped
# before the others. `[ ] ( )` defang masked-link injection; `` ` * _ ~ | `` defang text formatting.
_MD_META = re.compile(r"([\\`*_~|\[\]()])")
_WS_RUN = re.compile(r"\s+")  # collapse any whitespace run (incl. newlines/tabs) to one space

# Emit a "still alive, caught up" heartbeat every this-many quiet cycles so a working-but-idle poller
# (nobody playing => nothing to post) is distinguishable in the logs from a dead greenlet.
HEARTBEAT_EVERY = 30

# Message templates live in config/message_templates.yaml (loaded as `Messages`), but that file is a
# mounted config volume in deployment and can lag the repo — a missing key would AttributeError mid-poll
# and block every post. These built-in defaults keep the plugin self-contained: `_tpl` prefers the
# external template when present and falls back here otherwise. Keep them in sync with the yaml.
# Every line leads with a [R<roundId>](<url>) tag: events from multiple servers interleave in one
# channel, so each line carries its round's unique id, and the tag doubles as the "watch this round"
# link. The <> inside the masked link suppresses Discord's embed preview. (A "join server" link can't
# live here: Discord doesn't render steam:// links - the round page on the site is the click-through.)
# Per Mike: the map appears ONLY on the round-start line (every other line inherits it from
# context), it's "Round started" (rounds start, matches don't), and the round-start line uses
# Discord's ### header markdown so it visibly breaks up the kill flow.
DEFAULT_TEMPLATES = {
    "dystopia_round_start": "### [R{round_id}](<{round_url}>) Round started - `{map}` - {server}",
    "dystopia_capture": "[R{round_id}](<{round_url}>) **{player}** captured **{objective}**",
    "dystopia_round_end": "[R{round_id}](<{round_url}>) **{winner}** - {server}",
    "dystopia_kill": "[R{round_id}](<{round_url}>) **{player}** killed **{victim}** with {weapon}",
    "dystopia_backfill_summary": ("+**{count}** earlier Dystopia rounds from the last "
                                  "{days} day(s) (catching up). See the full feed: <{feed_url}/feed>"),
}


class DystopiaPlugin(Plugin):
    """Polls the Dystopia stats feed API (``GET /api/feed/events``) and posts high-signal match
    events (round start/end, objective captures, optionally kills) to Discord.

    Durable + gap-free by design:

    * **Resume across restarts.** The last consumed cursor lives in ``DystopiaFeedCache`` (per feed
      URL). On startup we resume from it and post *everything* since - a restart never re-skips or
      double-posts (the cursor advances strictly forward; an in-memory seen-id set is a dupe
      backstop).
    * **Backfill on first run.** With no stored cursor (true first deploy) we start from
      ``now - backfill_days`` (default 2) instead of "now", so the ~2 days of activity a fresh deploy
      would otherwise miss get posted.
    * **All servers.** Nothing filters to "our" servers - every server's events are posted (optional
      per-server channel routing via ``server_channels``; everything else -> ``channel_id``).
    * **Volume safe.** A large backlog (cold start / long downtime) keeps full detail for the most
      recent ``backfill_max_posts`` (default 50) events and collapses the older ones into a single
      "＋N earlier matches" summary line, so we never flood the channel or trip Discord rate limits.
      Kills are posted by default (``post_kills=True``); the same backlog cap keeps a cold-start
      kill backfill from flooding.
    """

    def load(self, ctx):
        self._seen_ids = deque(maxlen=SEEN_MAXLEN)
        self._seen_set = set()
        self._polling = False  # re-entrancy guard: a long backfill must not overlap the next tick
        self._polls = 0          # cycle counter, for a low-frequency "still alive" heartbeat
        self._announced_resume = False  # log the resume cursor + age exactly once at startup
        # Kills are buffered here (as postable tuples) and flushed on a timer / round-end / unload,
        # instead of posting one message per kill. See flush_kills / _drain_and_post.
        self._kill_buffer = []
        self._flushing = False  # re-entrancy guard: timer flush must not overlap a round-end flush
        # Chat is buffered + flushed just like kills, on its own (shorter) cadence. See flush_chat.
        self._chat_buffer = []
        self._flushing_chat = False

        cfg = CONFIG.dystopia
        if not cfg or (not cfg.channel_id and not cfg.server_channels):
            self.log.info("Dystopia feed config missing (no channel_id / server_channels), skipping.")
        else:
            self.feed_url = (cfg.feed_url or "https://dystopia-stats.com").rstrip("/")
            interval = cfg.poll_seconds or 20
            if cfg.reset_cursor:
                dropped = DystopiaFeedCache.delete().where(
                    DystopiaFeedCache.feed_url == self.feed_url).execute()
                self.log.warning("[dystopia] reset_cursor set: dropped %d stored cursor row(s); the "
                                 "next poll will re-backfill %s day(s). Set reset_cursor back to false.",
                                 dropped, cfg.backfill_days)
            batch = cfg.kill_batch_seconds or 90
            chat_batch = cfg.chat_batch_seconds or 20
            self.log.info("Dystopia feed poller starting: %s every %ss (kills=%s, kill_batch=%ss, "
                          "chat=%s, chat_batch=%ss, backfill_days=%s)", self.feed_url, interval,
                          cfg.post_kills, batch, cfg.post_chat, chat_batch, cfg.backfill_days)
            self.register_schedule(self.poll_feed, interval)
            # Flush buffered kills on their own cadence, independent of the poll interval. init=False so
            # we don't fire an immediate (empty) flush at startup.
            self.register_schedule(self.flush_kills, batch, init=False)
            # Chat flushes on its own (shorter) cadence so it stays readable in near-real-time.
            if cfg.post_chat:
                self.register_schedule(self.flush_chat, chat_batch, init=False)

        super(DystopiaPlugin, self).load(ctx)

    def unload(self, ctx):
        # Flush any buffered kills before the schedules are killed, so a redeploy/shutdown never drops
        # the current kill window. super().unload() then kills greenlets/listeners/schedules.
        try:
            self.flush_kills()
        except Exception as e:
            self.log.error("[dystopia] flush on unload failed: %s", e)
        try:
            self.flush_chat()
        except Exception as e:
            self.log.error("[dystopia] chat flush on unload failed: %s", e)
        super(DystopiaPlugin, self).unload(ctx)

    # -- helpers ---------------------------------------------------------------------------------

    def _mark_seen(self, event_id):
        if len(self._seen_ids) == self._seen_ids.maxlen and self._seen_ids:
            self._seen_set.discard(self._seen_ids[0])
        self._seen_ids.append(event_id)
        self._seen_set.add(event_id)

    def _channel_for(self, event):
        cfg = CONFIG.dystopia
        if cfg.server_channels and event.get("serverId") is not None:
            ch = cfg.server_channels.get(event["serverId"])
            if ch:
                return ch
        return cfg.channel_id

    def _backfill_start_cursor(self, cfg):
        """The cursor a true-first-run consumer starts from: ``now - backfill_days``, id ``0`` (which
        sorts before any real event that second). Matches the API's ``<zero-padded-unix>:<id>``."""
        days = cfg.backfill_days if cfg.backfill_days is not None else 2
        start_ts = int(time.time()) - int(days) * 86400
        return "{ts:0{pad}d}:0".format(ts=max(0, start_ts), pad=CURSOR_TS_PAD)

    def _tpl(self, name):
        """Message template ``name``: the external one (config/message_templates.yaml) if it's defined,
        else the built-in default. Guards against a stale/partial mounted template file crashing a poll."""
        return getattr(Messages, name, None) or DEFAULT_TEMPLATES[name]

    def _format(self, event):
        """Return the message string for an event, or None to skip (e.g. kills when disabled)."""
        cfg = CONFIG.dystopia
        kind = event.get("kind")
        game_map = event.get("mapName") or "unknown"
        server = event.get("serverName") or "a Dystopia server"
        round_id = event.get("roundId")
        round_url = f"{self.feed_url}/round/{round_id}"
        actor = event.get("actor") or {}
        victim = event.get("victim") or {}

        if kind == "round_start":
            return self._tpl("dystopia_round_start").format(
                map=game_map, server=server, round_id=round_id, round_url=round_url)

        if kind == "round_end":
            team = event.get("winningTeam")
            winner = f"{TEAM_NAMES[team]} won" if team in TEAM_NAMES else "Round ended"
            return self._tpl("dystopia_round_end").format(
                winner=winner, map=game_map, server=server, round_id=round_id, round_url=round_url)

        if kind == "capture":
            return self._tpl("dystopia_capture").format(
                player=actor.get("name") or "Someone",
                objective=event.get("objective") or "an objective",
                map=game_map,
                round_id=round_id,
                round_url=round_url,
            )

        # Kills are NOT formatted here: they're batched (see _format_kill / _drain_and_post / flush_kills).
        return None

    # -- kill line rendering ---------------------------------------------------------------------

    def _round_tag(self, round_id):
        """Round tag for batched kill lines: the last 5 digits (zero-padded) HYPERLINKED to the round
        page, wrapped in LITERAL brackets - e.g. 2000000112 -> `[00112]` where `00112` links to the
        round and the `[` `]` are plain text. Per Mike: link the digits, not the brackets. The brackets
        are backslash-escaped so Discord's masked-link parser does not fold them into the link, and the
        <> around the url suppresses the embed preview (same as the round-start/end templates)."""
        last5 = str(round_id or 0)[-5:].zfill(5)
        round_url = f"{self.feed_url}/round/{round_id}"
        return f"\\[[{last5}](<{round_url}>)\\]"

    def _escape_md(self, s):
        """Neutralize a user-controlled string for inline use in a Discord message: collapse whitespace
        (no newline/`\\n` line injection), backslash-escape inline markdown metacharacters (so `**`,
        `` ` ``, and `[..](..)` masked links render literally instead of formatting), and defang mention
        tokens (`@everyone`/`@here`/`@user` and `<@id>`/`<#chan>`/`<@&role>`) by inserting a zero-width
        space after each `@` and `<`. Player names AND chat text both pass through here."""
        if not s:
            return ""
        s = _WS_RUN.sub(" ", s.replace(_ZWSP, "")).strip()  # strip pre-seeded ZWSP, then collapse ws
        s = _MD_META.sub(r"\\\1", s)
        return s.replace("@", "@" + _ZWSP).replace("<", "<" + _ZWSP)

    def _player_link(self, player):
        """A killer/victim as `**[Name](<{feed}/player/<communityId>>)**`, or bold-only if we have no
        stable id (environment/suicide victims have no communityId). communityId is the steamid64 the
        stats site keys player pages on (verified: GET /player/<communityId> -> 200). The name is
        markdown-escaped (`_escape_md`) so a crafted name can't break out of the masked link or ping."""
        raw = (player or {}).get("name")
        if not raw:
            return None
        name = self._escape_md(raw)
        cid = (player or {}).get("communityId")
        if cid:
            return "**[{name}](<{url}/player/{cid}>)**".format(name=name, url=self.feed_url, cid=cid)
        return "**{}**".format(name)

    def _emoji_guild(self):
        """The guild whose `dys_*` weapon emojis we resolve against. Prefer the configured guild_id;
        otherwise fall back to the first guild in state that has any `dys_` emoji."""
        guilds = self.state.guilds or {}
        gid = CONFIG.dystopia.guild_id
        if gid and gid in guilds:
            return guilds[gid]
        for g in guilds.values():
            if any((e.name or "").startswith("dys_") for e in g.emojis.values()):
                return g
        return None

    def _weapon_emoji(self, weapon):
        """Custom-emoji markup (`<:dys_x:id>`) for a feed weapon display-name, resolved BY NAME from the
        guild at runtime (survives Mike re-uploading the emojis with new ids), or None to fall back to
        plain text. Maps the feed's weapon name -> `dys_<short>` via WEAPON_EMOJI."""
        if not weapon:
            return None
        short = WEAPON_EMOJI.get(weapon.strip().lower())
        if not short:
            return None
        guild = self._emoji_guild()
        if not guild:
            return None
        name = "dys_" + short
        for e in guild.emojis.values():
            if e.name == name:
                return str(e)  # disco Emoji.__str__ -> "<:name:id>" (or "<a:name:id>")
        return None

    def _format_kill(self, event):
        """The batched kill line: plain round tag, player-stats links, weapon emoji.
        `[00071] **[Killer](<url>)** killed **[Victim](<url>)** <:dys_x:id>`."""
        killer = self._player_link(event.get("actor")) or "**Someone**"
        victim = self._player_link(event.get("victim")) or "**the environment**"
        weapon = event.get("weapon")
        emoji = self._weapon_emoji(weapon)
        weapon_part = emoji if emoji else "with {}".format(weapon or "an unknown weapon")
        return "{tag} {killer} killed {victim} {weapon}".format(
            tag=self._round_tag(event.get("roundId")), killer=killer, victim=victim, weapon=weapon_part)

    def _sanitize_chat(self, text):
        """A chat message body, made safe for Discord: escaped/defanged via `_escape_md`, then length
        capped (belt-and-suspenders on top of the stats-side clamp). Returns "" for empty/blank."""
        s = self._escape_md(text)
        if len(s) > CHAT_TEXT_MAX:
            s = s[:CHAT_TEXT_MAX].rstrip() + "…"
        return s

    def _format_chat(self, event):
        """The batched chat line: round tag, linked speaker, sanitized message.
        `[00071] **[Speaker](<url>)**: hey team`. Returns None if the message is empty after sanitizing."""
        text = self._sanitize_chat(event.get("text"))
        if not text:
            return None
        actor = event.get("actor") or {}
        speaker = self._player_link(actor) or "**{}**".format(self._escape_md(actor.get("name")) or "Someone")
        return "{tag} {speaker}: {text}".format(
            tag=self._round_tag(event.get("roundId")), speaker=speaker, text=text)

    def _post_message(self, channel_id, content):
        # NB: do NOT name this `_post` — disco's Plugin base reserves `self._pre` / `self._post` as its
        # command-hook registry dicts, which would shadow the method (self._post(...) -> dict call ->
        # TypeError). Same reason to avoid `_pre`, `commands`, `listeners`, `schedules`, `_events`.
        try:
            self.bot.client.api.channels_messages_create(channel_id, content=content)
            return True
        except Exception as e:
            self.log.error("[dystopia] Failed to post to channel %s: %s", channel_id, e)
            return False

    def _postable(self, event):
        """(event_id, channel_id, content, cursor) for a NEW, postable NON-kill event, or None to skip."""
        event_id = event.get("id")
        if not event_id or event_id in self._seen_set:
            return None
        content = self._format(event)
        if content is None:
            return None
        channel_id = self._channel_for(event)
        if not channel_id:
            return None
        return (event_id, channel_id, content, event.get("cursor"))

    def _postable_kill(self, event):
        """(event_id, channel_id, content, cursor) for a NEW kill to be buffered, or None to skip."""
        if not CONFIG.dystopia.post_kills:
            return None
        event_id = event.get("id")
        if not event_id or event_id in self._seen_set:
            return None
        channel_id = self._channel_for(event)
        if not channel_id:
            return None
        return (event_id, channel_id, self._format_kill(event), event.get("cursor"))

    def _postable_chat(self, event):
        """(event_id, channel_id, content, cursor) for a NEW chat line to be buffered, or None to skip."""
        if not CONFIG.dystopia.post_chat:
            return None
        event_id = event.get("id")
        if not event_id or event_id in self._seen_set:
            return None
        channel_id = self._channel_for(event)
        if not channel_id:
            return None
        content = self._format_chat(event)
        if content is None:
            return None
        return (event_id, channel_id, content, event.get("cursor"))

    def _chunk(self, entries):
        """Yield (channel_id, joined_content, group) chunks: consecutive same-channel entries joined
        with newlines, split so each message stays under Discord's cap (BATCH_CHAR_LIMIT / _MAX_LINES)."""
        i, n = 0, len(entries)
        while i < n:
            channel_id = entries[i][1]
            j, size = i, 0
            while (j < n and entries[j][1] == channel_id and (j - i) < BATCH_MAX_LINES
                   and size + len(entries[j][2]) + 1 <= BATCH_CHAR_LIMIT):
                size += len(entries[j][2]) + 1
                j += 1
            yield channel_id, "\n".join(e[2] for e in entries[i:j]), entries[i:j]
            i = j

    def flush_kills(self):
        """Post all buffered kills as combined message(s) and clear the buffer. Called on the batch
        timer, on round-end, and on unload. The stored cursor was already advanced past these kills
        when they were buffered (see _drain_and_post), so flushing never touches the cursor."""
        if self._flushing or not self._kill_buffer:
            return
        self._flushing = True
        try:
            # Swap the buffer out atomically (before any gevent yield) so a concurrent flush is a no-op.
            buf, self._kill_buffer = self._kill_buffer, []
            posted = messages = 0
            for channel_id, content, group in self._chunk(buf):
                if self._post_message(channel_id, content):
                    posted += len(group)
                    messages += 1
                gevent.sleep(POST_SPACING_SECONDS)
            if posted:
                self.log.info("[dystopia] Flushed %d buffered kill(s) in %d message(s).", posted, messages)
        finally:
            self._flushing = False

    def flush_chat(self):
        """Post buffered chat as combined message(s) and clear the buffer. Called on the chat batch
        timer and on unload. Like kills, the cursor was already advanced past these when they were
        buffered, so flushing never touches the cursor. A per-flush line cap (CHAT_FLUSH_MAX_LINES)
        bounds a spam burst: excess (oldest-first is kept in order) collapses into a trailing note."""
        if self._flushing_chat or not self._chat_buffer:
            return
        self._flushing_chat = True
        try:
            # Swap the buffer out atomically (before any gevent yield) so a concurrent flush is a no-op.
            buf, self._chat_buffer = self._chat_buffer, []
            dropped = 0
            if len(buf) > CHAT_FLUSH_MAX_LINES:
                dropped = len(buf) - CHAT_FLUSH_MAX_LINES
                buf = buf[:CHAT_FLUSH_MAX_LINES]
            posted = messages = 0
            for channel_id, content, group in self._chunk(buf):
                if self._post_message(channel_id, content):
                    posted += len(group)
                    messages += 1
                gevent.sleep(POST_SPACING_SECONDS)
            if dropped and buf:
                # Note the suppressed flood on the same channel as the batch (belt: don't ping/format).
                self._post_message(buf[0][1], "_… {} more chat message(s) this window suppressed._".format(dropped))
            if posted:
                self.log.info("[dystopia] Flushed %d chat line(s) in %d message(s)%s.", posted, messages,
                              " (+%d suppressed)" % dropped if dropped else "")
        finally:
            self._flushing_chat = False

    def _fetch(self, since):
        """One page of the feed. Returns (events, cursor) or None on error."""
        params = {"limit": FETCH_LIMIT}
        if since:
            params["since"] = since
        # Opt in to chat events. Chat is EXCLUDED from the default feed (what the website uses) by
        # design - the stats side only returns `kind:"chat"` when this param is present, so chat
        # reaches the bot without ever surfacing on dystopia-stats.com. (Contract: Chat relay.)
        if CONFIG.dystopia.post_chat:
            params["include"] = "chat"
        try:
            r = requests.get(f"{self.feed_url}/api/feed/events", params=params,
                             headers=FEED_HEADERS, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            self.log.error("[dystopia] Feed poll failed: %s", e)
            return None
        return data.get("events") or [], data.get("cursor")

    def _save_cursor(self, cache, cursor):
        if cursor and cursor != cache.last_cursor:
            cache.last_cursor = cursor
            cache.save()

    # -- poller ----------------------------------------------------------------------------------

    def poll_feed(self):
        if self._polling:
            # Previous drain (likely a cold-start backfill) still running - don't overlap.
            return
        self._polling = True
        try:
            self._poll_once()
            if self._polls and self._polls % HEARTBEAT_EVERY == 0:
                cache = DystopiaFeedCache.get_or_none(feed_url=self.feed_url)
                self.log.info("[dystopia] poll alive: %d cycles, caught up at cursor %s.",
                              self._polls, cache.last_cursor if cache else "?")
        except Exception as e:
            # A single bad cycle (feed 500, transient network, one malformed event) must be logged
            # and retried next tick — NOT propagate out of the scheduled callback and kill the
            # greenlet (which is how it silently stopped after "poller starting"). Same guard the
            # RSS/presence schedules already have.
            self.log.exception("[dystopia] poll cycle failed (retrying next tick): %s", e)
        finally:
            self._polling = False

    def _cursor_age_seconds(self, cursor):
        """Age (seconds) of a `<unix>:<id>` cursor, or None if it doesn't parse."""
        try:
            return int(time.time()) - int(str(cursor).split(":", 1)[0])
        except (ValueError, AttributeError):
            return None

    def _poll_once(self):
        cfg = CONFIG.dystopia
        self._polls += 1

        cache = DystopiaFeedCache.get_or_none(feed_url=self.feed_url)
        first_run = cache is None
        if first_run:
            since = self._backfill_start_cursor(cfg)
            cache = DystopiaFeedCache.create(feed_url=self.feed_url, last_cursor=since)
            self.log.info("[dystopia] First run: backfilling the last %s day(s) from cursor %s.",
                          cfg.backfill_days, since)
        elif not self._announced_resume:
            # Distinguish "resumed from a stale cursor near HEAD" (nothing to backfill) from a real
            # first-run backfill — this is what makes a silent-but-alive poller diagnosable.
            self._announced_resume = True
            age = self._cursor_age_seconds(cache.last_cursor)
            self.log.info("[dystopia] Resuming from stored cursor %s (age %s); posting everything since. "
                          "Set dystopia.reset_cursor=true to force a full %s-day re-backfill instead.",
                          cache.last_cursor,
                          "unknown" if age is None else "{:.1f}h".format(age / 3600.0),
                          cfg.backfill_days)

        page = self._fetch(cache.last_cursor)
        if page is None:
            return
        events, cursor = page
        if not cursor:
            self.log.warning("[dystopia] Feed response had no cursor, skipping.")
            return

        # One unified path for every poll: drain the feed forward to "caught up" and post. A steady
        # caught-up tick is just a one-page drain; a cold-start backfill or a long-downtime catch-up is
        # a many-page drain. Either way the volume cap keeps the newest `backfill_max_posts` in full and
        # collapses anything older into a single summary line, so we never flood the channel.
        self._drain_and_post(cache, events, cursor)

    def _drain_and_post(self, cache, first_events, first_cursor):
        """Walk the feed forward to "caught up". NON-kill events (round start/end, captures) are posted
        this poll, most-recent in full and older ones collapsed into one summary line. KILLS are routed
        into `self._kill_buffer` instead and flushed on a timer / round-end / unload (see flush_kills).

        Crash safety (non-kills) comes from the persisted cursor alone (the in-memory seen-set doesn't
        survive a restart): we keep the stored cursor at the START of the backlog while draining (so a
        crash mid-drain just re-drains, having posted nothing), then advance it as we post - so a crash
        mid-post resumes with no dupes and no misses. Buffered kills DO have the cursor advanced past
        them (so interleaved round/capture lines aren't re-posted after a crash); the buffer is flushed
        on every graceful stop (unload/round-end/timer), and only a hard crash loses the in-flight kill
        window - a deliberate trade (kill lines are noise; duplicated round summaries are not).
        """
        cfg = CONFIG.dystopia

        postable = []
        kills = []
        chats = []
        saw_round_end = False
        final_cursor = first_cursor
        since = cache.last_cursor
        events, cursor = first_events, first_cursor
        pages = 0
        while True:
            pages += 1
            if not events:
                # Empty page => the feed echoes `since`; we're caught up.
                break
            for e in events:
                if e.get("kind") == "kill":
                    k = self._postable_kill(e)
                    if k:
                        kills.append(k)
                    continue
                if e.get("kind") == "chat":
                    c = self._postable_chat(e)
                    if c:
                        chats.append(c)
                    continue
                if e.get("kind") == "round_end":
                    saw_round_end = True
                p = self._postable(e)
                if p:
                    postable.append(p)
            final_cursor = cursor
            if cursor == since:
                break  # safety: cursor didn't advance despite events (shouldn't happen)
            if pages >= MAX_DRAIN_PAGES:
                self.log.warning("[dystopia] Drain hit page cap (%d); will continue next poll.", pages)
                break
            since = cursor
            nxt = self._fetch(since)
            if nxt is None:
                break  # transient error: post what we have; the stored cursor lets us resume later
            events, cursor = nxt
            if not cursor:
                break

        # Buffer this drain's kills (mark them seen so a same-process re-drain won't re-buffer). The
        # cursor is advanced past them below.
        if kills:
            for event_id, _, _, _ in kills:
                self._mark_seen(event_id)
            self._kill_buffer.extend(kills)

        # Buffer this drain's chat the same way (seen-marked; cursor advanced past them below). Flushed
        # on the chat timer / unload - NOT on round_end (chat isn't round-bound; it flushes fast anyway).
        if chats:
            for event_id, _, _, _ in chats:
                self._mark_seen(event_id)
            self._chat_buffer.extend(chats)

        # A round ended this drain: flush the kill buffer NOW so the round's kills post promptly (and
        # ahead of the round_end line) rather than waiting up to kill_batch_seconds.
        if saw_round_end:
            self.flush_kills()

        if not postable:
            # No non-kill posts; still advance over the drained tail (kills/other) so we don't re-scan.
            self._save_cursor(cache, final_cursor)
            return

        if pages > 1 or len(postable) > cfg.backfill_max_posts:
            self.log.info("[dystopia] Backlog drained: %d pages, %d postable events.", pages, len(postable))

        cap = cfg.backfill_max_posts
        to_post = postable
        if cap and len(postable) > cap:
            older, to_post = postable[:len(postable) - cap], postable[len(postable) - cap:]
            summary = self._tpl("dystopia_backfill_summary").format(
                count=len(older), days=cfg.backfill_days, feed_url=self.feed_url)
            target = cfg.channel_id or older[0][1]
            self._post_message(target, summary)
            for event_id, _, _, _ in older:
                self._mark_seen(event_id)
            self._save_cursor(cache, older[-1][3])  # advance past the summarized older events
            self.log.info("[dystopia] Collapsed %d older events into a summary; posting newest %d in full.",
                          len(older), len(to_post))

        # Batch consecutive same-channel events into combined messages (rate-limit safety; see the
        # BATCH_* constants). Crash semantics unchanged: each chunk marks its events seen and
        # advances the cursor to its last event, so a crash resumes after the last posted CHUNK.
        posted = 0
        messages = 0
        for channel_id, content, group in self._chunk(to_post):
            if self._post_message(channel_id, content):
                posted += len(group)
                messages += 1
            for event_id, _, _, _ in group:
                self._mark_seen(event_id)
            self._save_cursor(cache, group[-1][3])
            gevent.sleep(POST_SPACING_SECONDS)

        # Advance over any trailing non-postable events (e.g. kills while post_kills=False) so we don't
        # re-drain the same tail every poll.
        self._save_cursor(cache, final_cursor)
        if posted:
            self.log.info("[dystopia] Posted %d event(s) in %d message(s); cursor at %s.",
                          posted, messages, cache.last_cursor)
