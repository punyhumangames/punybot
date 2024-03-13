from datetime import datetime
from peewee import BigIntegerField, DateTimeField, TextField

from PunyBot.database import SQLiteBase


@SQLiteBase.register
class Agreement(SQLiteBase):
    class Meta:
        table_name = 'agreement_submissions'

    user_id = BigIntegerField()
    first_name = TextField()
    last_name = TextField()
    signed_date = DateTimeField(default=datetime.utcnow)
