from datetime import datetime

from peewee import BigIntegerField, DateTimeField

from PunyBot.database import SQLiteBase


@SQLiteBase.register
class KaboomMessage(SQLiteBase):
    class Meta:
        table_name = 'message_removal_queue'

    message_id = BigIntegerField(primary_key=True, null=False)
    channel_id = BigIntegerField(null=False)
    expire_time = DateTimeField(null=False)
