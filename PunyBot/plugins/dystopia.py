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

# Safety cap on pages walked in a single drain, so a runaway feed can't spin forever (200k events).
MAX_DRAIN_PAGES = 2000

# Emit a "still alive, caught up" heartbeat every this-many quiet cycles so a working-but-idle poller
# (nobody playing => nothing to post) is distinguishable in the logs from a dead greenlet.
HEARTBEAT_EVERY = 30

# Message templates live in config/message_templates.yaml (loaded as `Messages`), but that file is a
# mounted config volume in deployment and can lag the repo — a missing key would AttributeError mid-poll
# and block every post. These built-in defaults keep the plugin self-contained: `_tpl` prefers the
# external template when present and falls back here otherwise. Keep them in sync with the yaml.
DEFAULT_TEMPLATES = {
    "dystopia_round_start": "**Match started** on `{map}` - {server}\n<{round_url}>",
    "dystopia_capture": "**{player}** captured **{objective}** on `{map}`\n<{round_url}>",
    "dystopia_round_end": "**{winner}** on `{map}` - {server}\n<{round_url}>",
    "dystopia_kill": "**{player}** killed **{victim}** with {weapon} on `{map}`",
    "dystopia_backfill_summary": ("+**{count}** earlier Dystopia matches from the last "
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
            self.log.info("Dystopia feed poller starting: %s every %ss (kills=%s, backfill_days=%s)",
                          self.feed_url, interval, cfg.post_kills, cfg.backfill_days)
            self.register_schedule(self.poll_feed, interval)

        super(DystopiaPlugin, self).load(ctx)

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
        round_url = f"{self.feed_url}/round/{event.get('roundId')}"
        actor = event.get("actor") or {}
        victim = event.get("victim") or {}

        if kind == "round_start":
            return self._tpl("dystopia_round_start").format(map=game_map, server=server, round_url=round_url)

        if kind == "round_end":
            team = event.get("winningTeam")
            winner = f"{TEAM_NAMES[team]} won" if team in TEAM_NAMES else "Match ended"
            return self._tpl("dystopia_round_end").format(winner=winner, map=game_map, server=server, round_url=round_url)

        if kind == "capture":
            return self._tpl("dystopia_capture").format(
                player=actor.get("name") or "Someone",
                objective=event.get("objective") or "an objective",
                map=game_map,
                round_url=round_url,
            )

        if kind == "kill":
            if not cfg.post_kills:
                return None
            return self._tpl("dystopia_kill").format(
                player=actor.get("name") or "Someone",
                victim=victim.get("name") or "the environment",
                weapon=event.get("weapon") or "an unknown weapon",
                map=game_map,
            )

        return None

    def _post(self, channel_id, content):
        try:
            self.bot.client.api.channels_messages_create(channel_id, content=content)
            return True
        except Exception as e:
            self.log.error("[dystopia] Failed to post to channel %s: %s", channel_id, e)
            return False

    def _postable(self, event):
        """(event_id, channel_id, content, cursor) for a NEW, postable event, or None to skip."""
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

    def _fetch(self, since):
        """One page of the feed. Returns (events, cursor) or None on error."""
        params = {"limit": FETCH_LIMIT}
        if since:
            params["since"] = since
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
        """Walk the feed forward to "caught up", collecting every postable event, then post them with
        the most-recent portion in full and older ones collapsed into one summary line.

        Crash safety comes from the persisted cursor alone (the in-memory seen-set doesn't survive a
        restart): we keep the stored cursor at the START of the backlog while draining/collecting (so a
        crash mid-drain just re-drains, having posted nothing), then advance it as we post - so a crash
        mid-post resumes from the last posted event with no dupes and no misses.
        """
        cfg = CONFIG.dystopia

        postable = []
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

        if not postable:
            # Nothing to post; still advance over any drained (non-postable) tail so we don't re-scan.
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
            self._post(target, summary)
            for event_id, _, _, _ in older:
                self._mark_seen(event_id)
            self._save_cursor(cache, older[-1][3])  # advance past the summarized older events
            self.log.info("[dystopia] Collapsed %d older events into a summary; posting newest %d in full.",
                          len(older), len(to_post))

        posted = 0
        for event_id, channel_id, content, ev_cursor in to_post:
            if self._post(channel_id, content):
                posted += 1
            self._mark_seen(event_id)
            self._save_cursor(cache, ev_cursor)  # incremental: a crash resumes after the last post
            gevent.sleep(POST_SPACING_SECONDS)

        # Advance over any trailing non-postable events (e.g. kills while post_kills=False) so we don't
        # re-drain the same tail every poll.
        self._save_cursor(cache, final_cursor)
        if posted:
            self.log.info("[dystopia] Posted %d event(s); cursor at %s.", posted, cache.last_cursor)
