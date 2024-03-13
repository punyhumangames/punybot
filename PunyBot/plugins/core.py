import contextlib
import os
from datetime import datetime

import gevent
import requests
from disco.api.http import APIException
from disco.bot import Plugin
from disco.bot.command import CommandEvent
from disco.types.application import InteractionType
from disco.types.message import ActionRow, MessageComponent, ComponentTypes, SelectOption, ButtonStyles, MessageEmbed
from disco.types.user import Status, Activity, ActivityTypes
from dotenv import load_dotenv

from PunyBot import CONFIG
from PunyBot.constants import Messages


class CorePlugin(Plugin):
    def load(self, ctx):
        load_dotenv()

        self.guild_menu_roles = {}

        self.current_status_app = None
        self.bad_requests = 0
        self.schedule_restarts = 0

        # Player/Name Cache
        self.game_titles = {}
        self.player_counts = {}

        for gid in CONFIG.roles:
            self.guild_menu_roles[gid] = []
            for role in CONFIG.roles[gid].select_menu:
                self.guild_menu_roles[gid].append(role.role_id)

        super(CorePlugin, self).load(ctx)

    # Method to handle any bad steam requests from status updates
    def handle_bad_requests(self):
        self.bad_requests += 1
        if self.bad_requests > 10:
            self.schedule_restarts += 1
            if self.schedule_restarts > 3:
                # Reset values back to 0.
                self.bad_requests = 0
                self.schedule_restarts = 0

                # Log and kill the scheduled task
                self.log.error("Error: More than 3 restarts on the Status Scheduler. Killing until reboot or forced to restart via command.")
                self.schedules['update_status'].kill()
                return

            # Log, grab tasks, sleep, re-register, kill old one, reset counter.
            self.log.error("Error: More than 10 bad steam reponses. Killing status scheduler, pausing for 15 seconds, and retrying.")
            schedule = self.schedules['update_status']
            gevent.sleep(10)
            self.register_schedule(self.update_status, 5, init=False)
            self.bad_requests = 0
            schedule.kill()
        return

    def update_status(self):
        steam_key = os.getenv("STEAM_API_KEY")
        players = 0
        if not self.player_counts.get(self.current_status_app) or (datetime.now().timestamp() - self.player_counts[self.current_status_app]['last_requested']) > 30:
            try:
                r = requests.get(
                    f"https://partner.steam-api.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?key={steam_key}&appid={self.current_status_app}")
                if r.status_code == 403:
                    self.log.error(f"Error: 403 Forbidden Given when trying to get player count for APP ID: {self.current_status_app}. Are you using a valid API Key?")
                elif not r.json():
                    self.log.error("Error: Unable to grab player information")
                elif r.json()['response'].get('player_count'):
                    players = r.json()['response']['player_count']
                    self.player_counts[self.current_status_app] = {'count': players, 'last_requested': datetime.now().timestamp()}
            except Exception as e:
                self.log.error("Error: Exception when getting players from Steam's API. Possible bad response? Skipping Status Update...")
                self.handle_bad_requests()
                return
        else:
            players = self.player_counts[self.current_status_app]['count']

        app_name = None
        if self.game_titles.get(self.current_status_app):
            app_name = self.game_titles.get(self.current_status_app)
        else:
            try:
                r = requests.get(f"https://store.steampowered.com/api/appdetails?appids={self.current_status_app}")
                if not r.json():
                    self.log.error("Error: Unable to grab store app page")
                else:
                    if not r.json()[str(self.current_status_app)]['success']:
                        self.log.error("Error: App not found on the steam store")
                    else:
                        app_name = r.json()[str(self.current_status_app)]['data']['name']
                        self.game_titles[self.current_status_app] = app_name
            except Exception as e:
                self.log.error("Error: Exception when getting app information from Steam's API. Possible bad response? Skipping Status Update...")
                self.handle_bad_requests()
                return

        self.bot.client.update_presence(Status.ONLINE,
                                        Activity(name=f"{players} {app_name} player{'s' if players != 1 else ''}", type=ActivityTypes.WATCHING))

        if CONFIG.status_apps.index(self.current_status_app) == (len(CONFIG.status_apps) - 1):
            self.current_status_app = CONFIG.status_apps[0]
        else:
            self.current_status_app = CONFIG.status_apps[CONFIG.status_apps.index(self.current_status_app) + 1]

    @contextlib.contextmanager
    def send_control_message(self):
        embed = MessageEmbed()
        embed.set_footer(text='PunyBot Log')
        embed.timestamp = datetime.utcnow().isoformat()
        embed.color = 0x779ecb
        try:
            yield embed
            self.bot.client.api.channels_messages_create(CONFIG.logging_channel, embeds=[embed])
        except APIException:
            return self.log.exception('Failed to send control message:')

    @Plugin.listen('Ready')
    def on_ready(self, event):
        self.log.info(f"Bot connected as {self.client.state.me}")
        gw_info = self.bot.client.api.gateway_bot_get()

        with self.send_control_message() as embed:
            if self.bot.client.gw.reconnects:
                embed.title = 'Reconnected'
                embed.color = 0xffb347
            else:
                embed.title = 'Connected'
                embed.color = 0x77dd77
                self.log.info(f'Started session {event.session_id}')

            embed.add_field(name='Session ID', value=f'`{event.session_id}`', inline=True)
            embed.add_field(name='Session Starts Remaining',
                            value='`{}/{}`'.format(gw_info['session_start_limit']['remaining'],
                                                   gw_info['session_start_limit']['total']), inline=True)
            if self.bot.client.gw.last_conn_state:
                embed.add_field(name='Last Connection Close', value=f'`{self.bot.client.gw.last_conn_state}`',
                                inline=True)

        if not len(CONFIG.status_apps):
            return self.log.info("Status apps is empty, skipping setting bot status.")

        steam_key = os.getenv("STEAM_API_KEY")
        if not steam_key:
            return self.log.error("Error: No valid steam api key found. Please add the following environment variable 'STEAM_API_KEY' to your docker config/.env with the value being a publisher API key.")

        r = requests.get(f"https://partner.steam-api.com/ISteamApps/GetPartnerAppListForWebAPIKey/v1?key={steam_key}")
        if r.status_code == 403:
            return self.log.error("Error: Invalid Publisher Steam API Key. Can't get player counts!")

        from random import choice
        self.current_status_app = choice(CONFIG.status_apps)
        self.register_schedule(self.update_status, 5)

    @Plugin.listen('Resumed')
    def on_resumed(self, event):
        with self.send_control_message() as embed:
            embed.title = 'Resumed'
            embed.color = 0xffb347
            embed.add_field(name='Replayed Events', value=str(self.bot.client.gw.replayed_events))

    @Plugin.listen('MessageCreate')
    def on_command_msg(self, event):
        """
        Borrow by Nadie <iam@nadie.dev> (https://github.com/hackerjef/) [Used with permission]
        """
        if event.message.author.bot:
            return
        if not event.guild:
            return

        has_permission = False
        for role_id in CONFIG.admin_role:
            if role_id in event.member.roles:
                has_permission = True
                break

        if not has_permission:
            return

        commands = self.bot.get_commands_for_message(False, {}, '!', event.message)
        if not commands:
            return
        for command, match in commands:
            return command.plugin.execute(CommandEvent(command, event, match))

    # TODO: Replace with /command (Send message template. Which would include a interaction selection for what ones are active)
    @Plugin.command('sendmenumsg')
    def test_menu(self, event):

        components = ActionRow()

        select_menu = MessageComponent()
        select_menu.type = ComponentTypes.SELECT_MENU
        select_menu.custom_id = f"roles_menu_{event.guild.id}"
        select_menu.placeholder = "Which roles would you like?"

        for role in CONFIG.roles[event.guild.id].select_menu:
            option = SelectOption()
            option.label = role.display_name
            option.value = role.role_id

            select_menu.options.append(option)

        select_menu.max_values = len(CONFIG.roles[event.guild.id].select_menu)
        select_menu.min_values = 0

        components.add_component(select_menu)

        return event.channel.send_message(content="",
                                          components=[components.to_dict()])

    # TODO: Replace with /command (Send message template. Which would include a interaction selection for what ones are active)
    @Plugin.command('sendrulesmsg')
    def send_rules_message(self, event):
        content = Messages.rules_message
        return event.channel.send_message(content=content)

    # TODO: Replace with /command (Send message template. Which would include a interaction selection for what ones are active)
    @Plugin.command('sendrulesbuttonmsg')
    def send_rules_button_message(self, event):
        components = ActionRow()

        # TODO: Replace with component template
        button = MessageComponent()
        button.type = ComponentTypes.BUTTON
        button.style = ButtonStyles.SUCCESS
        button.custom_id = f"rules_{event.guild.id}"
        button.label = "I Agree"
        button.emoji.name = "✅"

        components.add_component(button)

        return event.channel.send_message(content=Messages.rules_message,
                                          components=[components.to_dict()])

    # TODO: Replace with /command
    @Plugin.command('forcestatus')
    def force_status(self, event):
        if self.schedules.get('update_status'):
            self.log.info("'forcestatus' command ran and active schedule found... killing schedule...")
            self.schedules['update_status'].kill()

        self.log.info("'forcestatus' command ran. Restarting schedule...")
        self.register_schedule(self.update_status, 5)
        return event.msg.add_reaction("👍")

    @Plugin.command('echo', '<msg:snowflake> [channel:snowflake|channel] [topic:str...]')
    def echo_command(self, event, msg, channel=None, topic=None):
        api_message = None
        channel_to_send_to = None

        if not channel:
            channel_to_send_to = event.channel
        else:
            if self.client.state.channels.get(channel):
                channel_to_send_to = self.client.state.channels.get(channel)
            elif self.client.state.threads.get(channel):
                channel_to_send_to = self.client.state.threads.get(channel)
            else:
                event.msg.reply("`Error`: **Unknown Channel...**")
                return event.msg.add_reaction("👎")

        try:
            api_message = self.client.api.channels_messages_get(event.channel.id, msg)
        except APIException as e:
            if e.code == 10008:
                return event.msg.reply(
                    "`Error`: **Message not found...Please make sure you are running this command in the "
                    "same channel as your original message!**")
            else:
                raise e

        if not api_message:
            event.msg.add_reaction("👎")
            return event.msg.reply("`Error`: **UNKNOWN ERROR...**")

        content = api_message.content
        attachments = []

        if api_message.attachments:
            for attachment in api_message.attachments:
                tmp = api_message.attachments[attachment]
                r = requests.get(tmp.url)
                r.raise_for_status()
                attachments.append((tmp.filename, r.content))

        # TODO: Split into multiple messages
        if len(content) > 2000:
            event.msg.add_reaction("👎")
            return event.msg.reply(f"`Error`: **Your original message is over 2000 characters** (`{len(content) - 2000} Over, {len(content)} Total`)")

        try:
            if channel_to_send_to.type == 15:
                if not topic:
                    event.msg.reply("`Error:` **Topic not set, please use** `!echo <msgID> <ChannelID> <Thread_Topic>`")
                    return event.msg.add_reaction("👎")
                msg = {'content': content, 'attachments': attachments}
                channel_to_send_to.start_forum_thread(content=content, name=topic, attachments=attachments)
            else:
                channel_to_send_to.send_message(content or None, attachments=attachments)
        except APIException as e:
            if e.code in [50013, 50001]:
                event.msg.add_reaction("👎")
                return event.msg.reply("`Error`: **Missing permission to echo, please check channel perms!**")
            else:
                event.msg.add_reaction("👎")
                raise e
        return event.msg.add_reaction("👍")

    @Plugin.listen('InteractionCreate')
    def test_menu_select(self, event):

        # TODO: Fix after Disco fixes their enum bug.
        if event.raw_data['interaction']['type'] != 3:
            return

        if event.data.custom_id.startswith('roles_menu_'):
            tmp_roles = event.member.roles
            for role_id in self.guild_menu_roles[event.guild.id]:
                if role_id in event.data.values:
                    continue
                if role_id in tmp_roles:
                    tmp_roles.remove(role_id)
            for selection in event.data.values:
                if selection not in tmp_roles:
                    tmp_roles.append(selection)

            event.guild.get_member(event.member.id).modify(roles=tmp_roles, reason="Updating selected roles from menu")
            # event.m.modify(roles=tmp_roles, reason="Updating selected roles from menu")

            return event.reply(type=6)

        if event.data.custom_id.startswith('rules_'):
            if CONFIG.roles[event.guild.id].rules_accepted not in event.member.roles:
                # event.m.add_role(self.guild_rules_roles.get(event.guild.id), reason="Accepted Rules")
                event.guild.get_member(event.member.id).add_role(CONFIG.roles[event.guild.id].rules_accepted,
                                                            reason="Accepted Rules")

            return event.reply(type=6)
