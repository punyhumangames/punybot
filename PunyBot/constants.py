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
    # On a TRUE first run (no stored cursor) backfill this many days of missed activity instead of
    # starting at "now". Restarts always resume from the stored cursor regardless of this.
    backfill_days = Field(int, default=2)
    # When draining a backlog (cold-start backfill or long-downtime catch-up), keep full detail for
    # only the most recent this-many events; older ones collapse into one "＋N earlier matches" line.
    backfill_max_posts = Field(int, default=50)
    # Optional per-server routing: { <stats server_id>: <discord channel_id> }. Servers not listed
    # fall back to channel_id.
    server_channels = DictField(int, snowflake, default={})


class PickupGamesConfig(SlottedModel):
    chat_channels_category = Field(snowflake, default=None)
    active_games_channel = Field(snowflake, default=None)
    lfg_role = Field(snowflake, default=None)
    # notify_time = Field(int, default=0)


class BaseConfig(SlottedModel):
    admin_role = ListField(snowflake, default=[])
    status_apps = ListField(int, default=[])
    logging_channel = Field(snowflake, default=None)
    roles = DictField(snowflake, RolesConfig, default={})
    media = Field(MediaConfig)
    agreement = Field(AgreementConfig, default=None)
    pickup_games = DictField(snowflake, DictField(text, PickupGamesConfig, default={}), default={})
    dystopia = Field(DystopiaConfig, default=None)


CONFIG = BaseConfig(config_values)

Messages = ConfigUtil.from_file("./config/message_templates.yaml")
