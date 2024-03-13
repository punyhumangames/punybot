# PunyBot

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
