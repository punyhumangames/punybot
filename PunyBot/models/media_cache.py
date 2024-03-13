from peewee import TextField, BigIntegerField, CompositeKey, IntegerField

from PunyBot.database import SQLiteBase


@SQLiteBase.register
class RssCache(SQLiteBase):
    class Meta:
        table_name = 'rss_cache'

    url = TextField(primary_key=True)
    latest_post = TextField(null=False)


@SQLiteBase.register
class SteamNewsCache(SQLiteBase):
    class Meta:
        table_name = 'steam_news_cache'

    app = IntegerField(primary_key=True)
    post_id = BigIntegerField(null=False)
