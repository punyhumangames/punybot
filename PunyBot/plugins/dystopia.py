from collections import deque

import gevent
import requests
from disco.bot import Plugin

from PunyBot import CONFIG
from PunyBot.constants import Messages
from PunyBot.models import DystopiaFeedCache


# Dystopia team ids -> human labels (2 = Punks, 3 = Corporation; see the stats schema).
TEAM_NAMES = {2: "The Punks", 3: "The Corporation"}

# How many events to pull per poll. The feed API caps `limit` at 200; a value comfortably above the
# per-poll cap lets us detect a burst (and summarize) rather than silently truncating it.
FETCH_LIMIT = 100

# Bound on the in-memory "already posted" id guard (belt-and-suspenders on top of cursor dedupe).
SEEN_MAXLEN = 500

# Small pause between individual posts so a legitimate multi-event poll doesn't hammer Discord.
POST_SPACING_SECONDS = 0.4


class DystopiaPlugin(Plugin):
    """Polls the Dystopia stats feed API (``GET /api/feed/events``) and posts high-signal match
    events (round start/end, objective captures, optionally kills) to Discord.

    Mirrors the MediaPlugin pattern: a ``register_schedule`` poller, a peewee cache model for
    dedupe (``DystopiaFeedCache`` stores the last opaque cursor), and posts via
    ``self.bot.client.api.channels_messages_create``. Kills are OFF by default to avoid flooding a
    busy channel; a per-poll cap collapses bursts into a single summary line.
    """

    def load(self, ctx):
        self._seen_ids = deque(maxlen=SEEN_MAXLEN)
        self._seen_set = set()

        cfg = CONFIG.dystopia
        if not cfg or (not cfg.channel_id and not cfg.server_channels):
            self.log.info("Dystopia feed config missing (no channel_id / server_channels), skipping.")
        else:
            self.feed_url = (cfg.feed_url or "https://dystopia-stats.com").rstrip("/")
            interval = cfg.poll_seconds or 20
            self.log.info("Dystopia feed poller starting: %s every %ss (kills=%s)",
                          self.feed_url, interval, cfg.post_kills)
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

    def _format(self, event):
        """Return the message string for a NEW event, or None to skip (e.g. kills when disabled)."""
        cfg = CONFIG.dystopia
        kind = event.get("kind")
        game_map = event.get("mapName") or "unknown"
        server = event.get("serverName") or "a Dystopia server"
        round_url = f"{self.feed_url}/round/{event.get('roundId')}"
        actor = event.get("actor") or {}
        victim = event.get("victim") or {}

        if kind == "round_start":
            return Messages.dystopia_round_start.format(map=game_map, server=server, round_url=round_url)

        if kind == "round_end":
            team = event.get("winningTeam")
            winner = f"{TEAM_NAMES[team]} won" if team in TEAM_NAMES else "Match ended"
            return Messages.dystopia_round_end.format(winner=winner, map=game_map, server=server, round_url=round_url)

        if kind == "capture":
            return Messages.dystopia_capture.format(
                player=actor.get("name") or "Someone",
                objective=event.get("objective") or "an objective",
                map=game_map,
                round_url=round_url,
            )

        if kind == "kill":
            if not cfg.post_kills:
                return None
            return Messages.dystopia_kill.format(
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

    # -- poller ----------------------------------------------------------------------------------

    def poll_feed(self):
        cfg = CONFIG.dystopia

        cache = DystopiaFeedCache.get_or_none(feed_url=self.feed_url)
        params = {"limit": FETCH_LIMIT}
        if cache:
            params["since"] = cache.last_cursor

        try:
            r = requests.get(f"{self.feed_url}/api/feed/events", params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            self.log.error("[dystopia] Feed poll failed: %s", e)
            return

        events = data.get("events") or []
        cursor = data.get("cursor")
        if not cursor:
            self.log.warning("[dystopia] Feed response had no cursor, skipping.")
            return

        # First run: record where the feed is now and DON'T replay the bootstrap batch.
        if not cache:
            DystopiaFeedCache.create(feed_url=self.feed_url, last_cursor=cursor)
            self.log.info("[dystopia] Bootstrapped feed cursor (%d events skipped on first run).", len(events))
            return

        # Nothing new: advance nothing (cursor echoes since) and return.
        if not events:
            return

        # Build the postable set first (kills may be filtered out) so the burst cap reflects what
        # would actually hit the channel.
        postable = []
        for event in events:
            event_id = event.get("id")
            if event_id in self._seen_set:
                continue
            content = self._format(event)
            if content is None:
                continue
            channel_id = self._channel_for(event)
            if not channel_id:
                continue
            postable.append((event_id, channel_id, content))

        # Anti-flood: too many at once -> one summary line instead of a wall of posts.
        if len(postable) > cfg.max_events_per_poll:
            summary = Messages.dystopia_burst_summary.format(count=len(postable), feed_url=self.feed_url)
            target = cfg.channel_id or postable[0][1]
            self._post(target, summary)
            for event_id, _, _ in postable:
                self._mark_seen(event_id)
            self._save_cursor(cache, cursor)
            self.log.info("[dystopia] Burst of %d events summarized to channel %s.", len(postable), target)
            return

        posted = 0
        for event_id, channel_id, content in postable:
            if self._post(channel_id, content):
                posted += 1
            self._mark_seen(event_id)
            gevent.sleep(POST_SPACING_SECONDS)

        self._save_cursor(cache, cursor)
        if posted:
            self.log.info("[dystopia] Posted %d/%d new events.", posted, len(postable))

    def _save_cursor(self, cache, cursor):
        cache.last_cursor = cursor
        cache.save()
