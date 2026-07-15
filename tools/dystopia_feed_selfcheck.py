#!/usr/bin/env python3
"""Offline end-to-end self-check for the Dystopia feed poller.

Runs the REAL plugin code (PunyBot/plugins/dystopia.py) — fetch -> format -> drain -> post — against
the LIVE stats feed, but with the disco framework, gevent, config, DB, and Discord client all stubbed,
so it needs no bot token and posts nothing to Discord. It records what WOULD be posted and asserts the
whole path reaches "Posted N" without an exception.

Why this exists: the post path broke one line at a time across several redeploys (403 -> round_id NaN ->
stale cursor -> missing template attr -> `_post` shadowed by disco's hook dict). Each was invisible until
the prior fix. This harness exercises the full chain locally so the next such bug is caught before ship.

Run:  python tools/dystopia_feed_selfcheck.py
Exit: 0 if the chain completes (posts >= 0 recorded, no exception); non-zero on any failure.
"""
import importlib.util
import logging
import os
import sys
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(REPO, "PunyBot", "plugins", "dystopia.py")
FEED_URL = os.environ.get("FEED_URL", "https://dystopia-stats.com")

logging.basicConfig(level=logging.INFO, format="  %(levelname)s %(message)s")


# --- stub: disco.bot.Plugin (mirrors the attrs the real base sets on every instance) --------------
class _StubPlugin(object):
    def __init__(self):
        # These four are exactly why `_post`/`_pre` are unusable as method names: disco reserves them
        # as instance dicts. If the plugin defines a method with one of these names, the dict wins.
        self._pre = {}
        self._post = {}
        self.commands = {}
        self.listeners = []
        self.schedules = {}
        self.log = logging.getLogger("dystopia")

    def register_schedule(self, func, interval, *a, **k):
        self._scheduled = (func, interval)  # captured, not run

    def load(self, ctx):
        pass


_disco = types.ModuleType("disco")
_disco_bot = types.ModuleType("disco.bot")
_disco_bot.Plugin = _StubPlugin
_disco.bot = _disco_bot
sys.modules["disco"] = _disco
sys.modules["disco.bot"] = _disco_bot

# --- stub: gevent (only sleep is used) ------------------------------------------------------------
_gevent = types.ModuleType("gevent")
_gevent.sleep = lambda *a, **k: None
sys.modules["gevent"] = _gevent


# --- stub: PunyBot.CONFIG.dystopia ----------------------------------------------------------------
class _Cfg(object):
    feed_url = FEED_URL
    channel_id = 111111111111111111
    poll_seconds = 20
    post_kills = True
    backfill_days = 2
    backfill_max_posts = 50
    server_channels = {}
    reset_cursor = True  # force the first-run backfill so there's a backlog to drive the whole path


_punybot = types.ModuleType("PunyBot")
_punybot.CONFIG = types.SimpleNamespace(dystopia=_Cfg())
sys.modules["PunyBot"] = _punybot

# Messages with NO dystopia_ attrs -> also exercises the _tpl() built-in fallback.
_constants = types.ModuleType("PunyBot.constants")
_constants.Messages = object()
sys.modules["PunyBot.constants"] = _constants


# --- stub: PunyBot.models.DystopiaFeedCache (in-memory) -------------------------------------------
class _Col(object):
    """Stand-in for a peewee Field so `DystopiaFeedCache.feed_url == url` (class-level, in the plugin's
    delete().where(...)) is a harmless no-op expression rather than an AttributeError."""
    def __eq__(self, other):
        return True


class _Cache(object):
    _rows = {}
    feed_url = _Col()  # class-level column; instances set their own self.feed_url (plain attr, no setter)

    def __init__(self, feed_url, last_cursor):
        self.feed_url, self.last_cursor = feed_url, last_cursor

    @classmethod
    def get_or_none(cls, feed_url):
        return cls._rows.get(feed_url)

    @classmethod
    def create(cls, feed_url, last_cursor):
        row = cls(feed_url, last_cursor)
        cls._rows[feed_url] = row
        return row

    def save(self):
        _Cache._rows[self.feed_url] = self

    @classmethod
    def delete(cls):
        class _Q:
            def where(self, *a, **k):
                return self

            def execute(self_inner):
                n = len(cls._rows)
                cls._rows.clear()
                return n
        return _Q()


_models = types.ModuleType("PunyBot.models")
_models.DystopiaFeedCache = _Cache
sys.modules["PunyBot.models"] = _models


# --- fake Discord sink ----------------------------------------------------------------------------
class _FakeApi(object):
    def __init__(self):
        self.posts = []

    def channels_messages_create(self, channel_id, content):
        self.posts.append((channel_id, content))
        return {"id": len(self.posts)}


def main():
    spec = importlib.util.spec_from_file_location("dystopia_real", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    plugin = mod.DystopiaPlugin()          # runs _StubPlugin.__init__ -> sets self._post = {} (the trap)
    plugin.load(ctx=None)                  # our load(): reset_cursor drop + register_schedule + attrs

    # Prove the shadow/fix: disco's reserved name is a dict; our real method survives under its new name.
    assert isinstance(plugin._post, dict), "expected disco's _post hook dict on the instance"
    assert callable(getattr(plugin, "_post_message", None)), \
        "_post_message must be a callable method (renamed off disco's reserved _post)"

    api = _FakeApi()
    plugin.bot = types.SimpleNamespace(client=types.SimpleNamespace(api=api))

    print("== driving poll_feed() against the LIVE feed: %s ==" % FEED_URL)
    plugin.poll_feed()                     # full chain: live fetch -> format -> drain -> _post_message

    print("== self-check result ==")
    print("  would-post count: %d" % len(api.posts))
    for ch, content in api.posts[:5]:
        print("  -> [%s] %s" % (ch, content.splitlines()[0]))
    if len(api.posts) > 5:
        print("  ... (%d more)" % (len(api.posts) - 5))
    print("PASS: post path completed with no exception (%d message(s) would be sent)." % len(api.posts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
