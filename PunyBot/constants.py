import os
import yaml
from disco.types.base import SlottedModel, snowflake, Field, text, ListField, DictField, AutoDictField
from disco.util.config import Config as ConfigUtil

with open(os.getcwd() + "/config/config.yaml", 'r') as f:
    config_values = yaml.load(f.read(), Loader=yaml.SafeLoader)
    f.close()


class SelectMenuConfig(SlottedModel):
    display_name = Field(text, default="")
    role_id = Field(snowflake, default=None)


class RolesConfig(SlottedModel):
    select_menu = ListField(SelectMenuConfig, default=[])
    rules_accepted = Field(snowflake, default=None)


class TwitterConfig(SlottedModel):
    url = Field(text, default="")
    following = ListField(text, default=[])


class SteamConfig(SlottedModel):
    url = Field(text, default="")
    apps = ListField(text, default=[])


class MediaConfig(SlottedModel):
    twitter = DictField(text, TwitterConfig, default={})
    steam = DictField(text, SteamConfig, default={})
    rss = DictField(snowflake, ListField(text), default=[])


class AgreementConfig(SlottedModel):
    pre_process_role = Field(snowflake, default=None)
    post_process_role = Field(snowflake, default=None)


class DystopiaConfig(SlottedModel):
    # Base URL of the Dystopia stats site. The feed is polled at <feed_url>/api/feed/events and
    # in-message links (/round, /player, /server) are built off this same host.
    feed_url = Field(text, default="https://dystopia-stats.com")
    # Default channel that feed events are posted to (overridable per-server via server_channels).
    channel_id = Field(snowflake, default=None)
    # How often (seconds) to poll the feed.
    poll_seconds = Field(int, default=20)
    # Post individual kill events. OFF by default: a busy server would flood the channel.
    post_kills = Field(bool, default=True)
    # Per-round threads: each LIVE round's feed goes into its own Discord thread hung off a round-start
    # header in the channel (kills/captures/round-end land in the thread), keeping the channel a clean
    # index. Backfilled/old rounds still post flat. Only admins can post in these threads by channel
    # perms, which also sets up the future Discord->server chat bridge.
    thread_per_round = Field(bool, default=True)
    # Kills are BATCHED: instead of one message per kill, they're buffered and flushed as combined
    # message(s) on this cadence (seconds). Non-kill events (round start/end, captures) still post on
    # the normal poll cadence. The buffer is also flushed on round-end and on plugin unload/shutdown.
    kill_batch_seconds = Field(int, default=90)
    # Guild whose custom weapon emojis (named `dys_<weapon>`) are used to render the weapon icon on
    # batched kill lines. Resolved by NAME at runtime, so re-uploading the emojis (new ids) is fine.
    # If unset, the plugin scans every guild it's in for a `dys_*` emoji. Weapons with no matching
    # emoji fall back to plain "with <weapon>" text.
    guild_id = Field(snowflake, default=None)
    # On a TRUE first run (no stored cursor) backfill this many days of missed activity instead of
    # starting at "now". Restarts always resume from the stored cursor regardless of this.
    backfill_days = Field(int, default=2)
    # One-shot maintenance flag: when true, the stored cursor is DROPPED on load so the next poll
    # takes the first-run path and re-backfills `backfill_days`. Use to recover from a stale cursor
    # that advanced past a backlog without posting it. Flip true, redeploy once, then set back to
    # false (leaving it true re-drops the cursor on every restart).
    reset_cursor = Field(bool, default=False)
    # When draining a backlog (cold-start backfill or long-downtime catch-up), keep full detail for
    # only the most recent this-many events; older ones collapse into one "＋N earlier matches" line.
    backfill_max_posts = Field(int, default=50)
    # Optional per-server routing: { <stats server_id>: <discord channel_id> }. Servers not listed
    # fall back to channel_id.
    server_channels = DictField(int, snowflake, default={})
    # Relay in-game ALL-chat to the feed channel. When on, the feed poll opts in (`?include=chat`) so
    # the stats side sends chat (it is EXCLUDED from the public website feed by design - opt-in only).
    # Chat text is sanitized for Discord (markdown escaped, @mentions/@everyone defanged, links
    # neutralized) before posting. Off by default until the game+stats sides ship chat ingest.
    post_chat = Field(bool, default=False)
    # Chat is BATCHED like kills but on a shorter cadence (people chat a lot): buffered and flushed as
    # combined message(s) every this-many seconds. A per-flush line cap (see CHAT_FLUSH_MAX_LINES)
    # bounds a spam burst. Also flushed on plugin unload/shutdown.
    chat_batch_seconds = Field(int, default=20)


class PickupGamesConfig(SlottedModel):
    chat_channels_category = Field(snowflake, default=None)
    active_games_channel = Field(snowflake, default=None)
    lfg_role = Field(snowflake, default=None)
    # notify_time = Field(int, default=0)


class DystopiaBuildConfig(SlottedModel):
    # Forgejo instance base URL and the repo whose Actions runs are announced.
    forgejo_url = Field(text, default="https://git.punyhuman.com")
    repo = Field(text, default="puny-human/dystopia-build")
    # Forgejo API token (read:repository) used for BOTH the tasks poll and the ci-logs raw fetch.
    # Bot-side only, per hub decision 2026-07-15-build-posts-use-punybot-not-a-webhook.md - the
    # CI runners never see a Discord or bot credential.
    token = Field(text, default=None)
    # Builds channel (separate from the stats/match feed channel).
    channel_id = Field(snowflake, default=None)
    # How often (seconds) to poll for finished runs.
    poll_seconds = Field(int, default=60)


class BaseConfig(SlottedModel):
    admin_role = ListField(snowflake, default=[])
    status_apps = ListField(int, default=[])
    logging_channel = Field(snowflake, default=None)
    roles = DictField(snowflake, RolesConfig, default={})
    media = Field(MediaConfig)
    agreement = Field(AgreementConfig, default=None)
    pickup_games = DictField(snowflake, DictField(text, PickupGamesConfig, default={}), default={})
    dystopia = Field(DystopiaConfig, default=None)
    dystopia_build = Field(DystopiaBuildConfig, default=None)


CONFIG = BaseConfig(config_values)

Messages = ConfigUtil.from_file("./config/message_templates.yaml")
