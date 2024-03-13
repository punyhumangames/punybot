from datetime import datetime
from peewee import IntegerField, BigIntegerField, DateTimeField, BooleanField, TextField
from playhouse.sqlite_ext import JSONField

from PunyBot.database import SQLiteBase


@SQLiteBase.register
class PickupGame(SQLiteBase):
    class Meta:
        table_name = 'pickup_games'

    id = IntegerField(primary_key=True)
    guild_id = BigIntegerField()
    host_id = BigIntegerField()
    region = TextField()
    chat_channel_id = BigIntegerField()
    start_datetime = DateTimeField(default=datetime.now())
    # One hour from now!
    end_time = DateTimeField(default=(datetime.fromtimestamp(datetime.now().timestamp() + 3600)))
    # 30 Minutes from now!
    next_action_time = DateTimeField(default=(datetime.fromtimestamp(datetime.now().timestamp() + 1800)))
    action_type = IntegerField(default=0)
    active_game_message_id = BigIntegerField()
    control_message_id = BigIntegerField()
    extra_info = JSONField()
    active = BooleanField(default=True)
