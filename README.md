# PunyBot

# Current Feature List Per Plugin

-----------------------

# Agreement
## Features
* Post a message marked as an "Terms and Conditions" to be given access to private channels
* Upon clicking the button, user recieves pop up, asking for information, and upon submission is saved to DB, and then roles are swapped for the one granting channel(s) acess.

# Control
* Process control, please ignore.

# Core
## Features
* Rotates the bot user's status based on player count on certain steam games. The games used are the ones in the config file with the  `status_apps` key.
* Logs to a channel that the bot has connected/resumed to discord's gateway
* Handles basic commands (chat commands that start with "!")
* Assigns member role based on the configured role in `roles.SERVER_ID.rules_accepted` once they click the "agree to rules" 
* Handles the role modification for the role selection menu based on the `roles.SERVER_ID.select_menu` key.
## Commands
* `!echo <msg_id> [channel_id] [topic]` - Will echo a message into either the same channel or a different channel. If channel is a forum channel, the topic will be used as the new thread's title.
* `!forcestatus` - Sometime's discord's precenses break, this kills the internal scheduler and restarts it
* `!sendrulesbuttonmsg` *will be replaced* - Sends the rules agreement message with correct message components
* `!sendrulesmsg` *will be replaced* - Sends the rules agreement message without button
* `!sendmenumsg`  *will be replaced* - Sends the select menu message for the role selection.

# Kaboom
## Features
* When the appropriate command/menu action is completed, the bot will mark a message with a :bomb: emoji, and delete it following a user selected period of time.
## Commands
* `/kaboom message_id time` - Will mark the message for deletion. It's a slash command with a up to date message selection.
* `!setupcmds` - Registers the menu/chat commands to the guild it is ran in.

# Dystopia
## Features
* Polls the [Dystopia stats](https://dystopia-stats.com) live feed API (`/api/feed/events`) and posts high-signal match events for **all** servers to Discord.
* Default events: match started (round start), objective captures, and match ended (round end, with the winning team). Individual kills are an opt-in config flag (`dystopia.post_kills`, off by default) so a busy server doesn't flood the channel.
* **Durable resume:** the last consumed cursor lives in a sqlite cache (`DystopiaFeedCache`), so a restart resumes exactly where it left off and posts everything since — it never re-skips or double-posts (cursor advances strictly forward; an in-memory seen-id set is a dupe backstop).
* **Backfill on first run:** with no stored cursor (true first deploy) it starts from `now - backfill_days` (`dystopia.backfill_days`, default 2) instead of "now", so the days of activity a fresh deploy would otherwise miss get posted.
* **Volume safe:** every poll drains the feed forward to "caught up" (a steady tick is one page; a cold start / long downtime is many). The newest `dystopia.backfill_max_posts` (default 50) events are posted in full and anything older collapses into one "＋N earlier matches" summary line, so the channel is never flooded. Posts are spaced to respect Discord rate limits.
* Optional per-server channel routing via `dystopia.server_channels` (default: everything → `channel_id`). Links point to `https://dystopia-stats.com` (`/round/<id>`, `/player/<communityId>`, `/server/<id>`).

### Config keys (`dystopia`)
| key | default | meaning |
| --- | --- | --- |
| `feed_url` | `https://dystopia-stats.com` | stats site base URL (feed + links) |
| `channel_id` | — | default channel for feed posts |
| `poll_seconds` | `20` | poll interval |
| `post_kills` | `false` | post individual kill events |
| `backfill_days` | `2` | on true first run, backfill this many days |
| `backfill_max_posts` | `50` | keep newest N of a backlog in full; summarize the rest |
| `server_channels` | `{}` | optional `{stats server_id: channel_id}` routing |

# Media
## Features
* Pools steam news into channels using webhooks.
* Pools news from various RSS feeds into channels.
* Stores cache in a sqlite DB to ensure no duplicates
* Note: Twitter disabled due to unknown API status

# Pickup
## Features
* Allows users to create "games" via a interaction menu in an "active games" channel.
* Interactively guides users to creating a channel
* Allows users to acquire a "looking-for-group" group to get pinged about new games
* When a user joins a game, they are given permissions to the joined game's voice channel
  * The host has a menu at the top of the channel allowing them to select a new host, change regions, end game, edit server information, and leave (for normal players only)
* Channels/Active game messages are cleaned up either after a game ends, or a set amount of time passes.
-----------------------

## Before starting the bot
1) fill out the `config-example.yaml` file and rename/copy to `config.yaml`
2) If using a compose file:
   - Fill out the env variables in the compose file, or in a .env file.
       - `DISCORD_TOKEN` is a [discord bot token](https://discord.com/developers/applications).
       - `TWITTER_BEARER_TOKEN` is a [twitter developer token](https://dev.twitter.com) (Although WIP for now since API changes)
       - `STEAM_API_KEY` is a steam api key.
   - Proceed to next section

   If wanting to run locally:
    - Proceed to running bot locally from source

### Running the bot with Docker (and Compose)

It is highly recommended to use docker-compose to run the production or local (dev) instances of the bot. This ensures you have all the dependencies required, and that local works as it should in production. 

The production.yml docker-compose file expects to map the `config` directory to the container. No extra invocations are needed. 
The development.yml docker-compose file will map both the `config` and a `data` directory to the container for ease of access to the sqlite database for development.


### Running the bot locally from source. 

Locally, the bot uses [poetry](https://python-poetry.org/) for dependencies, which would also make running the bot easier.

1) Because we aren't running on docker, add the config value "token" at the top of your config.yaml file with your discord bot token.
2) `poetry install`/`poetry update`
3) `poetry run python -m disco.cli --config config/config.yaml`
    - Note: Only the bot token won't load from a .env file, the other tokens will.

## WIP
- Redo all commands into slash commands 
  - echo
    - `/echo <msg:Snowflake> [channel:Channel] [Topic:Str..]`
  - sendtemplatemessage
    - `/sendtemplate <msg_template:str> [interaction_section:str]`
      - Auto complete for both msg_template and interaction_section


- More social webhooks:
  - Reimplement Twitter
  - Instagram
  - Facebook
  - YouTube
  - Twitch (Official streams?)


- Configurable Message components (Buttons)
