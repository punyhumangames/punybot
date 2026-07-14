from peewee import TextField

from PunyBot.database import SQLiteBase


@SQLiteBase.register
class DystopiaFeedCache(SQLiteBase):
    """Tracks how far the Dystopia stats feed poller has consumed, per feed URL.

    ``last_cursor`` is the opaque cursor returned by the stats feed API
    (``GET /api/feed/events``); the poller passes it back as ``?since=`` so it
    only ever sees NEW events and never re-posts. Keyed by ``feed_url`` so a
    single bot could poll more than one stats instance without cross-talk.
    """

    class Meta:
        table_name = 'dystopia_feed_cache'

    feed_url = TextField(primary_key=True)
    last_cursor = TextField(null=False)
