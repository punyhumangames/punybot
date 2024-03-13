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


CONFIG = BaseConfig(config_values)

Messages = ConfigUtil.from_file("./config/message_templates.yaml")
