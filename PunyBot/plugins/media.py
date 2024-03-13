import os
from datetime import datetime

import feedparser
import re
import gevent
import requests
# import tweepy
import dateutil.parser as parser
from disco.bot import Plugin
from urllib.parse import urlparse

from PunyBot import CONFIG
from PunyBot.constants import Messages
from PunyBot.models import SteamNewsCache, RssCache


# class TwitterStream(tweepy.StreamingClient):
#
#     bad_connections = 0
#
#     def __init__(self, bot, twitter_config, twitter_api_client, bearer_token, **kwargs):
#         self.bot = bot
#         self.twitter_config = twitter_config
#         self.twitter_api_client = twitter_api_client
#         self.bearer_token = bearer_token
#         super().__init__(bearer_token, **kwargs)
#
#     def on_tweet(self, tweet):
#         user = self.twitter_api_client.get_user(id=tweet.author_id).data
#         url = f"https://twitter.com/{user.username}/status/{tweet.id}"
#         data = {
#             "content": url
#         }
#         for webhook in self.twitter_config[user.username]:
#             info = webhook.split("/")
#             self.bot.client.api.webhooks_token_execute(info[0], info[1], data=data)
#
#     def on_closed(self, response):
#         self.bot.log.error("Received close stream requests from Twitter. Waiting 10 seconds and then reattempting connection...")
#         gevent.sleep(10)
#         self.bad_connections += 1
#         if self.bad_connections > 3:
#             self.bot.log.error("More than 3 disconnects, twitter stream shutting down, Process Restart required.")
#         else:
#             if self.running:
#                 self.bot.log.info("Twitter client still connected, disconnecting...")
#                 self.disconnect()
#             self.bot.plugins['MediaPlugin'].start_twitter_client()


class MediaPlugin(Plugin):
    def load(self, ctx):

        if not os.path.exists(os.getcwd() + "/data"):
            raise FileExistsError("Missing Data Directory!")

        if os.getenv("TWITTER_BEARER_TOKEN"):
            self.log.warning("Twitter currently disabled. WIP for now.")
            # self.start_twitter_client()
        else:
            self.log.info("Twitter API Key not provided, skipping twitter hook.")

        if len(CONFIG.media.steam) > 0:
            self.steam_news_config = {}

            for key in CONFIG.media.steam:
                for steam_app in CONFIG.media.steam[key].apps:
                    if not self.steam_news_config.get(steam_app):
                        self.steam_news_config[steam_app] = [CONFIG.media.steam[key].url]
                    else:
                        self.steam_news_config[steam_app].append(CONFIG.media.steam[key].url)

            self.register_schedule(self.get_steam_news, 60)

        else:
            self.log.info("Steam config empty, skipping.")

        if len(CONFIG.media.rss) > 0:
            self.rss_config = {}

            for key in CONFIG.media.rss:
                for rss_feed in CONFIG.media.rss[key]:
                    if not self.rss_config.get(rss_feed):
                        self.rss_config[rss_feed] = [key]
                    else:
                        self.rss_config[rss_feed].append(key)

            self.register_schedule(self.check_rss, 300)

        else:
            self.log.info("RSS News config empty, skipping.")

        super(MediaPlugin, self).load(ctx)

    def unload(self, ctx):
        if hasattr(self, "twitter_stream"):
            self.log.info("Disconnecting Twitter Stream")
            self.twitter_stream.disconnect()

        super(MediaPlugin, self).unload(ctx)

    def start_twitter_client(self):
        if len(CONFIG.media.twitter) == 0:
            self.log.info("Twitter Config empty, skipping.")
            return

        twitter_config = {}

        for key in CONFIG.media.twitter:
            for twitter_user in CONFIG.media.twitter[key].following:
                if not twitter_config.get(twitter_user):
                    twitter_config[twitter_user] = [CONFIG.media.twitter[key].url]
                else:
                    twitter_config[twitter_user].append(CONFIG.media.twitter[key].url)

        self.log.info("Creating Stream Client")
        self.twitter_stream = TwitterStream(self.bot, twitter_config,
                                            tweepy.Client(CONFIG.media.api.twitter_bearer_token),
                                            CONFIG.media.api.twitter_bearer_token)
        self.log.info("Generating and Updating Stream Rules")
        response = self.twitter_stream.get_rules()
        rules = []
        if response.data:
            rules = [rule.id for rule in response.data]
        if rules:
            self.twitter_stream.delete_rules(rules)
        users = [f"from:{key}" for key in twitter_config.keys()]
        self.twitter_stream.add_rules(tweepy.StreamRule(' OR '.join(users)))
        self.twitter_stream.filter(threaded=True, expansions=["author_id"])

        self.log.info("Twitter Client Started!")

    def get_steam_news(self):
        for app_id in self.steam_news_config.keys():
            r = requests.get(
                f"https://api.steampowered.com/ISteamNews/GetNewsForApp/v0002/?appid={app_id}&count=1&maxlength=400&format=json")
            if not r.json():
                return
            post = r.json()['appnews']['newsitems'][0]

            cache = SteamNewsCache.get_or_none(app=app_id)

            if not cache:
                SteamNewsCache.create(app=app_id, post_id=post["gid"])
            elif int(post["gid"]) == cache.post_id:
                continue
            else:
                cache.post_id = post["gid"]
                cache.save()

            img = None
            information = post['contents']
            if post['contents'].startswith('{STEAM_CLAN_IMAGE}'):
                hash = post['contents'].split(' ')[0]
                img = f"https://cdn.akamai.steamstatic.com/steamcommunity/public/images/clans/{hash[19:]}"
                information = information[len(hash):]
            data = {
                "content": "",
                "embeds": [
                    {
                        "type": "rich",
                        "title": post['title'],
                        "description": information,
                        "color": 0xe9e9e9,
                        "footer": {
                            "text": f"Posted by {post['author']}"
                        },
                        "url": post['url']
                    }
                ]
            }
            if img:
                data['embeds'][0]['image'] = {'url': img}

            for webhook in self.steam_news_config[app_id]:
                info = webhook.split("/")
                self.bot.client.api.webhooks_token_execute(info[0], info[1], data=data)

    def check_rss(self):

        def pubdate_to_timestamp(pub_date):
            return int(parser.parse(pub_date).timestamp())

        def sort_post_by_published(e):
            return pubdate_to_timestamp(e['published'])

        for feed_url in self.rss_config.keys():
            feed = feedparser.parse(feed_url)

            if not feed.get('entries') or len(feed['entries']) == 0:
                continue

            unsorted_feed = [entry for entry in feed['entries'] if entry.get('published')]

            if len(unsorted_feed) == 0:
                continue

            sorted_feed = sorted(unsorted_feed, key=sort_post_by_published, reverse=True)

            domain = urlparse(sorted_feed[0]['link']).netloc

            if pubdate_to_timestamp(sorted_feed[0]['published']) < int(datetime.now().timestamp() - 3600):
                continue

            cache = RssCache.get_or_none(url=feed_url)

            if cache:
                if cache.latest_post == sorted_feed[0]['link']:
                    continue
                else:
                    cache.latest_post = sorted_feed[0]['link']
                    cache.save()
            else:
                RssCache.create(url=feed_url, latest_post=sorted_feed[0]['link'])

            author = None
            if 'author_detail' in sorted_feed[0].keys():
                author = f" by: {sorted_feed[0]['author_detail']['name']}"

            title = re.sub("<[^>]*>", "", sorted_feed[0]['title'], count=0, flags=0)

            timestamp = pubdate_to_timestamp(sorted_feed[0]['published'])

            content = Messages.rss_news_message.format(title=title, author=author or '', timestamp=timestamp, url=sorted_feed[0]['link'])

            # content = f"📰 | **{title}**{author or ''} (<t:{timestamp}:R>)\n\n** {sorted_feed[0]['link']} **"

            for channel in self.rss_config[feed_url]:
                msg = self.bot.client.api.channels_messages_create(channel, content=content)
                # If wanted to use announcement channels and have the bot auto-publish articles
                # try:
                #     self.bot.client.api.channels_messages_publish(channel, msg.id)
                # except:
                #     continue
