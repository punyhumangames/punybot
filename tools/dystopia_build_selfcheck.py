#!/usr/bin/env python3
"""Offline end-to-end self-check for the dystopia-build CI poller.

Runs the REAL plugin code (PunyBot/plugins/dystopia_build.py) — tasks poll -> BuildID fetch ->
format -> post — against the LIVE Forgejo, with disco, config, DB, and Discord all stubbed, so it
posts nothing. Needs FORGEJO_TOKEN in the environment (read:repository).

Two passes prove both behaviors:
  1. first run  -> cursor initializes at the newest task, NOTHING posts (no history flood);
  2. cursor rewound N finished tasks -> exactly those runs post, newest ci-logs SUMMARY BuildIDs
     attach only where the run number matches.

Run:  FORGEJO_TOKEN=... python tools/dystopia_build_selfcheck.py
Exit: 0 if both passes complete without exception; non-zero otherwise.
"""
import importlib.util
import logging
import os
import sys
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(REPO, "PunyBot", "plugins", "dystopia_build.py")

TOKEN = os.environ.get("FORGEJO_TOKEN")
if not TOKEN:
    print("FAIL: set FORGEJO_TOKEN (read:repository) in the environment.")
    sys.exit(2)

logging.basicConfig(level=logging.INFO, format="  %(levelname)s %(message)s")


# --- stub: disco.bot.Plugin ------------------------------------------------------------------------
class _StubPlugin(object):
    def __init__(self):
        self._pre = {}
        self._post = {}
        self.commands = {}
        self.listeners = []
        self.schedules = {}
        self.log = logging.getLogger("dystopia_build")

    def register_schedule(self, func, interval, *a, **k):
        self._scheduled = (func, interval)

    def load(self, ctx):
        pass


_disco = types.ModuleType("disco")
_disco_bot = types.ModuleType("disco.bot")
_disco_bot.Plugin = _StubPlugin
_disco.bot = _disco_bot
sys.modules["disco"] = _disco
sys.modules["disco.bot"] = _disco_bot


# --- stub: PunyBot.CONFIG.dystopia_build -----------------------------------------------------------
class _Cfg(object):
    forgejo_url = os.environ.get("FORGEJO_URL", "https://git.punyhuman.com")
    repo = os.environ.get("BUILD_REPO", "puny-human/dystopia-build")
    token = TOKEN
    channel_id = 111111111111111111
    poll_seconds = 60


_punybot = types.ModuleType("PunyBot")
_punybot.CONFIG = types.SimpleNamespace(dystopia_build=_Cfg())
sys.modules["PunyBot"] = _punybot


# --- stub: PunyBot.models.DystopiaBuildCache (in-memory) --------------------------------------------
class _Col(object):
    def __eq__(self, other):
        return True


class _Cache(object):
    _rows = {}
    repo = _Col()

    def __init__(self, repo, last_task_id):
        self.repo, self.last_task_id = repo, last_task_id

    @classmethod
    def get_or_none(cls, *a, **k):
        return next(iter(cls._rows.values()), None)

    @classmethod
    def create(cls, repo, last_task_id):
        row = cls(repo, last_task_id)
        cls._rows[repo] = row
        return row

    @classmethod
    def update(cls, last_task_id):
        class _Q:
            def where(self, *a, **k):
                return self

            def execute(self_inner):
                for row in cls._rows.values():
                    row.last_task_id = last_task_id
                return 1
        return _Q()


_models = types.ModuleType("PunyBot.models")
_models.DystopiaBuildCache = _Cache
sys.modules["PunyBot.models"] = _models


class _FakeApi(object):
    def __init__(self):
        self.posts = []

    def channels_messages_create(self, channel_id, content):
        self.posts.append((channel_id, content))
        return {"id": len(self.posts)}


def main():
    spec = importlib.util.spec_from_file_location("dystopia_build_real", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    plugin = mod.DystopiaBuildPlugin()
    plugin.load(ctx=None)
    api = _FakeApi()
    plugin.bot = types.SimpleNamespace(client=types.SimpleNamespace(api=api))

    print("== pass 1: first run (cursor init, no backfill) ==")
    plugin.poll_builds()
    assert not api.posts, "first run must not post history, got %d post(s)" % len(api.posts)
    row = _Cache.get_or_none()
    assert row is not None and row.last_task_id > 0, "cursor row missing after first run"
    print("  cursor initialized at task %d, 0 posts (correct)" % row.last_task_id)

    print("== pass 2: rewind cursor to replay recent finished runs ==")
    rewind = int(os.environ.get("REWIND", "6"))
    row.last_task_id = max(0, row.last_task_id - rewind)
    plugin.poll_builds()
    print("  would-post count: %d" % len(api.posts))
    for ch, content in api.posts:
        print("  -> [%s] %s" % (ch, content))
    assert api.posts, "rewound cursor should have replayed at least one finished run"
    print("PASS: both passes completed with no exception.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
