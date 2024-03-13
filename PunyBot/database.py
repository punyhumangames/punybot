import os

from peewee import Model
from playhouse.sqlite_ext import SqliteExtDatabase

sqlite_db = SqliteExtDatabase(os.getcwd() + '/data/database.db', pragmas={'journal_mode': 'wal'})

REGISTERED_MODELS = []


class SQLiteBase(Model):
    class Meta:
        database = sqlite_db

    @staticmethod
    def register(cls):
        cls.create_table(True)
        if hasattr(cls, 'SQL'):
            sqlite_db.execute_sql(cls.SQL)

        REGISTERED_MODELS.append(cls)
        return cls

