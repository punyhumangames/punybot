import os
from datetime import datetime
from json import JSONDecodeError

import gevent
import requests
from disco.api.http import APIException
from disco.bot import Plugin
from disco.types.channel import PermissionOverwrite, PermissionOverwriteType
from disco.types.message import ActionRow, MessageComponent, ButtonStyles, ComponentTypes, SelectOption, MessageEmbed, \
    MessageModal, TextInputStyles
from disco.types.permissions import Permissions

from PunyBot import CONFIG
from PunyBot.constants import PickupGamesConfig, Messages
from PunyBot.models import PickupGame
from PunyBot.utils.timing import Eventual


class TimerOffsets(object):
    HALF_TIME = .5
    TEN_MINUTE = 600
    FIVE_MINUTES = 300
    ONE_MINUTE = 60


class ActionType(object):
    HALF_TIME_PROMPT = 0
    TEN_MINUTE_PROMPT = 1
    FIVE_MINUTE_PROMPT = 2
    ONE_MINUTE_PROMPT = 3
    END_GAME = 4


def get_cfg_for_game(guild: int, key: str, value) -> tuple[str, PickupGamesConfig]:
    """
    :param guild: The guild's ID
    :param key: The key needing to be checked. Ex: chat_channels_category
    :param value: The value we are checking against. Ex: 991457779271356496 (The category ID in the config)
    :return: game_cgf_id, PickupGamesConfig - The config key, The config relating to the corresponding game.
    """
    for dict_key, game_cfg in CONFIG.pickup_games[guild].items():
        if game_cfg.to_dict()[key] == value:
            return dict_key, game_cfg


class PickupPlugin(Plugin):
    def load(self, ctx):

        if len(CONFIG.pickup_games) == 0:
            self.log.info("Pickup config empty, disabling plugin.")
            super(PickupPlugin, self).unload(ctx)

        self.info_cache = {}

        self.timed_tasks = Eventual(self.action_games)

        self.spawn_later(5, self.queue_tasks)

        super(PickupPlugin, self).load(ctx)

    # TODO: Check given server credentials to get server information during PUG creation.
    def check_steam_server(self, server_host, cfg):
        steam_key = os.getenv("STEAM_API_KEY")
        if not steam_key:
            self.log.error(
                "Error: No valid steam api key found. Please add the following environment variable 'STEAM_API_KEY' to your docker config/.env with the value being a SteamWeb API key.")
            return None

        try:
            r = requests.get(
                f"https://api.steampowered.com/IGameServersService/GetServerList/v1/?filter=\appid\\{cfg.game_id}&limit=5000&key={steam_key}")
            if r.status_code == 403:
                self.log.error(
                    f"Error: 403 Forbidden Given when trying to get server list for APP ID: {cfg.game_id}. Are you using a valid API Key?")
                return None
            elif not r.json():
                self.log.error("Error: Unable to grab server information. Response is not JSON")
                return None
        except JSONDecodeError as e:
            self.log.error(
                "Error: JSONDecodeException when getting Server List from Steam's API. Possible bad response? Skipping...")
            return None

    def end_games(self):
        needs_to_end = list(PickupGame.select().where(
            (PickupGame.active == True) &
            (PickupGame.end_time < datetime.now())
        ))

        if len(needs_to_end) > 0:
            self.log.info(f"[PUG Ending System]: Attempting to end {len(needs_to_end)} games!")
            for game in needs_to_end:
                self.end_game(game, from_tasks=True)

            gevent.sleep(5)
            self.queue_tasks()
        else:
            return

    def action_games(self):
        next_game = list(PickupGame.select().where(
            (PickupGame.active == True) &
            (PickupGame.next_action_time < datetime.now())
        ))

        if len(next_game) > 0:
            self.log.info(f"[PUG Warning System]: Attempting to prompt {len(next_game)} games!")
            for game in next_game:

                game_channel = self.client.state.channels.get(game.chat_channel_id)
                content = f"**Warning** This channel will expire <t:{int(game.end_time.timestamp())}:R>. If you need to extend the time, please click the button below!"

                control_buttons = ActionRow()
                extend_timer_button = MessageComponent()
                extend_timer_button.type = ComponentTypes.BUTTON
                extend_timer_button.style = ButtonStyles.SECONDARY
                extend_timer_button.emoji = None
                extend_timer_button.label = "Extend Channel Time"
                extend_timer_button.custom_id = "ag_extend_time"
                control_buttons.add_component(extend_timer_button)

                if game.action_type == ActionType.END_GAME:
                    self.log.info(f"Ending Game!: {game.id}")
                    self.end_game(game, from_tasks=True)
                    return

                if game.action_type == ActionType.ONE_MINUTE_PROMPT:
                    self.log.info(f"One Minute Left!: {game.id}")
                    game.action_type = ActionType.END_GAME
                    game.next_action_time = datetime.fromtimestamp(datetime.now().timestamp() + 60)
                    # game.next_action_time = datetime.fromtimestamp(datetime.now().timestamp() + 10)

                if game.action_type == ActionType.FIVE_MINUTE_PROMPT:
                    self.log.info(f"Five Minutes Left!: {game.id}")
                    game.action_type = ActionType.ONE_MINUTE_PROMPT
                    game.next_action_time = datetime.fromtimestamp(datetime.now().timestamp() + 240)
                    # game.next_action_time = datetime.fromtimestamp(datetime.now().timestamp() + 10)

                if game.action_type == ActionType.TEN_MINUTE_PROMPT:
                    self.log.info(f"Ten Minutes Left!: {game.id}")
                    game.action_type = ActionType.FIVE_MINUTE_PROMPT
                    game.next_action_time = datetime.fromtimestamp(datetime.now().timestamp() + 300)
                    # game.next_action_time = datetime.fromtimestamp(datetime.now().timestamp() + 10)

                if game.action_type == ActionType.HALF_TIME_PROMPT:
                    self.log.info(f"Half Time Prompt!: {game.id}")
                    game.action_type = ActionType.TEN_MINUTE_PROMPT
                    game.next_action_time = datetime.fromtimestamp(datetime.now().timestamp() + 1200)
                    # game.next_action_time = datetime.fromtimestamp(datetime.now().timestamp() + 10)

                msg = game_channel.send_message(content=content,
                                                allowed_mentions={"parse": ["roles"]},
                                                components=[control_buttons.to_dict()])

                # Track prompt messages to use with timer extensions
                if not game.extra_info.get('prompt_messages'):
                    game.extra_info['prompt_messages'] = [msg.id]
                else:
                    game.extra_info['prompt_messages'].append(msg.id)

                game.save()

            gevent.sleep(5)
            self.queue_tasks()
        else:
            return

    def queue_tasks(self):

        next_action = list(PickupGame.select().where(
            (PickupGame.active == True) &
            (~(PickupGame.next_action_time >> None))
        ).order_by(PickupGame.next_action_time.asc()).limit(1))

        if not next_action:
            self.log.info("[PUG Warning System]: No currently active games pending action.")
        else:
            self.log.info(
                f"[PUG Warning System]: Waiting until {next_action[0].next_action_time} for Game ID: {next_action[0].id}")
            self.timed_tasks.set_next_schedule(next_action[0].next_action_time)

    def start_game(self, event, information, config):

        server_info = ""
        if information.get('server_info'):
            # TODO: Modify answer based on steam server response.
            # server = self.check_steam_server(information['server_info']['server_name'], config)
            server_info = Messages.pickup_chat_server_information.format(
                server_name=information['server_info']['server_name'],
                server_password=information['server_info'].get('server_password') or 'None')

        channel = event.guild.create_voice_channel(name=f"{information['region']}",
                                                   parent_id=config.chat_channels_category,
                                                   reason=f"{event.member} is starting a pickup game!")

        channel.create_overwrite(entity=event.member, allow=(Permissions.VIEW_CHANNEL + Permissions.CONNECT)).save()

        timestamp = int(datetime.now().timestamp())
        timestamp_expire = int(datetime.fromtimestamp(timestamp + 3600).timestamp())
        timestamp_prompt = int(datetime.fromtimestamp(timestamp + 1800).timestamp())

        content = Messages.pickup_chat_channel_message_base.format(host_id=event.member.id,
                                                                   game_region=information['region'],
                                                                   server_info=server_info,
                                                                   expire_timestamp=timestamp_expire,
                                                                   action_time=timestamp_prompt)

        control_buttons = ActionRow()

        region_button = MessageComponent()
        region_button.type = ComponentTypes.BUTTON
        region_button.style = ButtonStyles.SECONDARY
        region_button.emoji = None
        region_button.label = "Change Region"
        region_button.custom_id = "ag_change_region"

        server_information = MessageComponent()
        server_information.type = ComponentTypes.BUTTON
        server_information.style = ButtonStyles.SECONDARY
        server_information.emoji = None
        server_information.label = "Edit Server Information"
        server_information.custom_id = "ag_edit_server_information"

        change_host = MessageComponent()
        change_host.type = ComponentTypes.BUTTON
        change_host.style = ButtonStyles.SECONDARY
        change_host.emoji = None
        change_host.label = "Change Game Host"
        change_host.custom_id = "ag_change_game_host"

        end_game = MessageComponent()
        end_game.type = ComponentTypes.BUTTON
        end_game.style = ButtonStyles.DANGER
        end_game.emoji = None
        end_game.label = "End Game"
        end_game.custom_id = "ag_end_game"

        leave_game = MessageComponent()
        leave_game.type = ComponentTypes.BUTTON
        leave_game.style = ButtonStyles.DANGER
        leave_game.emoji = None
        leave_game.label = "Leave Game"
        leave_game.custom_id = "ag_leave_game"

        control_buttons.add_component(region_button)
        control_buttons.add_component(server_information)
        control_buttons.add_component(change_host)
        control_buttons.add_component(end_game)
        control_buttons.add_component(leave_game)

        cm = channel.send_message(content=content, components=[control_buttons.to_dict()],
                                  allowed_mentions={"parse": ["users"]})
        # TODO: Pin the message whenever discord decides to implement this feature :(
        # msg.pin()

        # Create the message saying the game has started!
        active_channel = self.client.state.channels.get(config.active_games_channel)

        pug_content = f"Hello <@&{config.lfg_role}>, A new game is starting!"
        embed = MessageEmbed()
        embed.set_author(name=f"Host: {event.member}", icon_url=event.member.get_avatar_url())
        embed.description = f"**Region**: `{information['region']}`\n**Chat**: <#{channel.id}>\n**Players**: `1`"  # {server_info}"

        button_row = ActionRow()

        join_game = MessageComponent()
        join_game.type = ComponentTypes.BUTTON
        join_game.style = ButtonStyles.SECONDARY
        join_game.emoji = None
        join_game.label = "Click to join!"
        join_game.custom_id = f"pug_join_game"

        button_row.add_component(join_game)

        am = active_channel.send_message(content=pug_content, embeds=[embed], allowed_mentions={"parse": ["roles"]},
                                         components=[button_row.to_dict()])

        extra_info = {
            "players": [event.member.id],
            "server": information.get('server_info') or {}
        }

        # test_time = datetime.fromtimestamp(timestamp + 300)
        # test_time2 = datetime.fromtimestamp(timestamp + 10)

        new_game = PickupGame().create(host_id=event.member.id, chat_channel_id=channel.id,
                                       active_game_message_id=am.id,
                                       control_message_id=cm.id, extra_info=extra_info, guild_id=event.guild.id,
                                       region=information['region'], action_type=0)

        channel.set_name(f"{new_game.region}-{new_game.id}")

        # Just make sure all the tasks are up to date!
        self.queue_tasks()
        return event.reply(type=7, content="Setup completed.").after(10).delete()

    def update_game_info(self, game, config, update_player_count=False):

        # Grab the message in the active games channel to edit!
        host = self.client.state.guilds[game.guild_id].get_member(game.host_id)

        try:
            agc_message = self.client.api.channels_messages_get(config.active_games_channel,
                                                                game.active_game_message_id)

            new_content = f"Hello <@&{config.lfg_role}>, A new game is starting!"
            embed = MessageEmbed()
            embed.set_author(name=f"Host: {host}", icon_url=host.get_avatar_url())
            embed.description = f"**Region**: `{game.region}`\n**Chat**: <#{game.chat_channel_id}>\n**Players**: {len(game.extra_info['players'])}"  # {server_info}"

            agc_message.edit(content=new_content, embeds=[embed])

        except APIException as e:
            if e.code == 10008:
                self.log.error(
                    f"Unable to update Active Game Chat Message: Message not found! Game ID: {game.id}, Message ID: {game.control_message_id}")
            else:
                raise e

        # if update_player_count:
        #     try:
        #         self.client.api.channels_get(game.chat_channel_id).set_name(f"({game.region}) {len(game.extra_info['players'])} Player{'s' if len(game.extra_info['players']) > 1 else ''}")
        #     except APIException as e:
        #         if e.code == 10008:
        #             self.log.error(
        #                 f"Unable to update Channel Name: Channel not found! Game ID: {game.id}, Channel ID: {game.chat_channel_id}")
        #         else:
        #             raise e

        # Grab the message in the game's chat channel to edit!
        try:
            control_message = self.client.api.channels_messages_get(game.chat_channel_id, game.control_message_id)

            server_info = ""
            if game.extra_info.get('server'):
                server_info = Messages.pickup_chat_server_information.format(
                    server_name=game.extra_info['server']['server_name'],
                    server_password=game.extra_info['server'].get('server_password') or 'None')

            # content = f"Me too"
            content = Messages.pickup_chat_channel_message_base.format(server=server_info, host_id=game.host_id,
                                                                       game_region=game.region, server_info=server_info,
                                                                       expire_timestamp=int(game.end_time.timestamp()),
                                                                       action_time=int(
                                                                           game.next_action_time.timestamp()))

            control_message.edit(content=content)

        except APIException as e:
            if e.code == 10008:
                self.log.error(
                    f"Unable to update Game Control Message: Message not found! Game ID: {game.id}, Message ID: {game.control_message_id}")
            else:
                raise e

    def end_game(self, game, from_tasks=False):

        channel = self.client.api.channels_get(game.chat_channel_id)

        key, config = get_cfg_for_game(game.guild_id, 'chat_channels_category', channel.parent_id)

        try:
            self.client.api.channels_messages_delete(config.active_games_channel,
                                                     game.active_game_message_id)
        except APIException as e:
            if e.code == 10008:
                self.log.error(
                    f"[PUG Ending System]: Unable to delete Active Game Message - Message not found! Game ID: {game.id}, Message ID: {game.control_message_id}")

        try:
            self.client.api.channels_delete(game.chat_channel_id, reason="PUG Ending!")
        except APIException as e:
            if e.code == 10003:
                self.log.error(
                    f"[PUG Ending System]: Unable to delete PUG Chat - Channel not found! Game ID: {game.id}, Channel ID: {game.chat_channel_id}")

        game.active = False
        game.save()

        if not from_tasks:
            # Requeue in case this was at the front of the line!
            return self.queue_tasks()

    # The listener for active games.
    # This will listen for and respond to button presses from the control messages from active games.
    @Plugin.listen('InteractionCreate')
    def ag_tiv_listener(self, event):

        if event.raw_data['interaction']['type'] not in [3, 5]:
            return

        if not event.data.custom_id.startswith("ag_"):
            return

        function = event.data.custom_id[3:]

        game = PickupGame.get_or_none(chat_channel_id=event.channel.id)

        if not game:
            return event.reply(type=4, content="**Error**: `Game not found`", flags=(1 << 6))

        channel = self.client.api.channels_get(game.chat_channel_id)

        key, config = get_cfg_for_game(game.guild_id, 'chat_channels_category', channel.parent_id)

        if function == "leave_game":
            if event.member.id not in game.extra_info['players']:
                return event.reply(type=4, content="**Unable To Leave Game**: `Not a player`", flags=(1 << 6))
            elif event.member.id == game.host_id:
                return event.reply(type=4, content="**Error**: `Must migrate game host before leaving!`",
                                   flags=(1 << 6))

            game.extra_info['players'].remove(event.member.id)

            game.save()

            self.client.api.channels_permissions_delete(event.channel.id, event.member.id)

            event.channel.send_message(content=f"<@{event.member.id}> has left the game!",
                                       allowed_mentions={"parse": ["users"]})

            try:
                event.guild.get_member(event.member.id).disconnect()
            except:
                self.log.error(f"Unable to disconnect {event.member} from the PUG voice channel.")

            self.update_game_info(game, config, update_player_count=True)

            return event.reply(type=6)

        # The rest of the functions require you to be game host.
        # So if you're not, then they may not proceed.
        if event.member.id != game.host_id:
            return event.reply(type=4, content="**Error**: `Only the host may edit the game details`", flags=(1 << 6))

        if function == "edit_server_information":
            server_name = MessageComponent()
            server_name.type = ComponentTypes.TEXT_INPUT
            server_name.style = TextInputStyles.SHORT
            server_name.label = "Server Host"
            server_name.placeholder = "127.0.0.1:27015"
            server_name.value = game.extra_info['server'].get('server_name') or None
            server_name.required = True
            server_name.custom_id = "server_name"

            server_password = MessageComponent()
            server_password.type = ComponentTypes.TEXT_INPUT
            server_password.style = TextInputStyles.SHORT
            server_password.label = "Server Password"
            server_password.placeholder = "Password1234"
            server_password.value = game.extra_info['server'].get('server_password') or None
            server_password.required = False
            server_password.custom_id = "server_password"

            ar1 = ActionRow()
            ar1.add_component(server_name)
            ar2 = ActionRow()
            ar2.add_component(server_password)

            modal = MessageModal()
            modal.title = "Server Information"
            modal.custom_id = "ag_set_server_info"
            modal.add_component(ar1)
            modal.add_component(ar2)

            return event.reply(type=9, modal=modal)

        if function == "set_server_info":
            new_server_info = {}

            for action_row in event.data.components:
                for component in action_row.components:
                    if component.custom_id == "server_name":
                        new_server_info['server_name'] = component.value
                        break
                    if component.custom_id == "server_password":
                        if component.value:
                            new_server_info['server_password'] = component.value
                        break

            game.extra_info['server'] = new_server_info
            game.save()

            self.update_game_info(game, config)

            return event.reply(type=4, content="Server information updated!", flags=(1 << 6))

        if function == "change_region":

            select_row = ActionRow()

            select_menu = MessageComponent()
            select_menu.type = ComponentTypes.STRING_SELECT
            select_menu.custom_id = "ag_select_region"
            select_menu.placeholder = "Select a region!"

            for region in ["NA", "EU", "Oceanic"]:
                option = SelectOption()
                option.label = region
                option.value = region
                option.emoji = None

                select_menu.options.append(option)

            select_menu.max_values = 1
            select_menu.min_values = 1

            select_row.add_component(select_menu)

            return event.reply(type=4, content="Please select the new region for the game!",
                               components=[select_row.to_dict()], flags=(1 << 6))

        if function == "select_region":

            if game.region == event.data.values[0]:
                return event.reply(type=7, content=f"Region is already set to {game.region}")

            game.region = event.data.values[0]
            game.save()

            self.update_game_info(game, config)

            return event.reply(type=7, content="Region has been updated!")

        if function == "select_host":
            game.host_id = event.data.values[0]
            game.save()

            self.update_game_info(game, config)

            return event.reply(type=7, content="Host has been updated!\n*Note: You are no longer be able to update "
                                               "game settings.*")

        if function == "change_game_host":
            select_row = ActionRow()

            select_menu = MessageComponent()
            select_menu.type = ComponentTypes.STRING_SELECT
            select_menu.custom_id = "ag_select_host"
            select_menu.placeholder = "Select a new host!"

            if len(game.extra_info['players']) == 1:
                return event.reply(type=4, content="Not enough players to change game host.", flags=(1 << 6))

            for player in game.extra_info['players']:

                if player == game.host_id:
                    continue

                option = SelectOption()
                option.label = f"{event.guild.get_member(player)}"
                option.value = player
                option.emoji = None

                select_menu.options.append(option)

            select_menu.max_values = 1
            select_menu.min_values = 1

            select_row.add_component(select_menu)

            return event.reply(type=4, content="Please select the new game host!\n*Note: Selecting a new host will "
                                               "remove your ability to update any game settings*",
                               components=[select_row.to_dict()], flags=(1 << 6))

        if function == "end_game":
            content = f"Are you sure that you would like to end the game?"

            yes_no = ActionRow()

            yes = MessageComponent()
            yes.type = ComponentTypes.BUTTON
            yes.style = ButtonStyles.SUCCESS
            yes.emoji = None
            yes.label = "Yes"
            yes.custom_id = "ag_end_yes"

            no = MessageComponent()
            no.type = ComponentTypes.BUTTON
            no.style = ButtonStyles.DANGER
            no.emoji = None
            no.label = "No"
            no.custom_id = "ag_end_no"

            yes_no.add_component(yes)
            yes_no.add_component(no)

            return event.reply(type=4, content=content,
                               components=[yes_no.to_dict()], flags=(1 << 6))

        if function == "end_yes":
            return self.end_game(game)

        if function == "end_no":
            return event.reply(type=7, content="Cancelled.")

        if function == "extend_time":
            if event.member.id != game.host_id:
                event.reply(type=4, content="Only the host may extend the timer.", flags=(1 << 6))
            else:
                game.active = False
                game.save()
                self.queue_tasks()

                # Get new ending time
                new_end_time = datetime.fromtimestamp(datetime.now().timestamp() + 3600)
                game.end_time = new_end_time
                # Reset the next action to half time
                game.action_type = ActionType.HALF_TIME_PROMPT
                # Reset the next action time
                game.next_action_time = datetime.fromtimestamp(new_end_time.timestamp() - 1800)
                # Save the game to DB
                game.active = True
                game.save()
                self.queue_tasks()
                event.reply(type=4,
                            content=f"Time Extended, the channel will now expire at <t:{int(new_end_time.timestamp())}:T> (<t:{int(new_end_time.timestamp())}:R>)",
                            flags=(1 << 6))

                control_buttons = ActionRow()
                extend_timer_button = MessageComponent()
                extend_timer_button.type = ComponentTypes.BUTTON
                extend_timer_button.style = ButtonStyles.SECONDARY
                extend_timer_button.label = "Extend Channel Time"
                extend_timer_button.custom_id = "ag_extend_time"
                extend_timer_button.disabled = True
                control_buttons.add_component(extend_timer_button)

                event.message.edit(components=[control_buttons.to_dict()])

                for msg in game.extra_info['prompt_messages']:
                    try:
                        self.client.api.channels_messages_modify(game.chat_channel_id, msg,
                                                                 components=[control_buttons.to_dict()])
                    except APIException as e:
                        fail = "[PUG System] | Failed to edit timer message: {}"
                        if e.code == 10008:
                            self.log.error(fail.format("Message not found..."))
                        elif e.code == 10003:
                            self.log.error(fail.format("Channel not found..."))

                        with self.bot.plugins['CorePlugin'].send_control_message() as embed:
                            embed.title = "PUG Error"
                            embed.color = 0xf04747
                            embed.add_field(name='Message', value=f'``{msg}``', inline=True)
                            embed.add_field(name='Channel', value=f'``{game.chat_channel_id}``', inline=True)
                            embed.description = f'```{fail.format(f"API Error {e.code}, {e.msg}")}```'

    # This is the listener for game creation/role handout for LFG.
    # 4 == Reply to message || 7 == Edit message || 9 == Reply w/modal
    @Plugin.listen('InteractionCreate')
    def pug_listener(self, event):

        if event.raw_data['interaction']['type'] not in [3, 5]:
            return

        if not event.data.custom_id.startswith("pug_"):
            return

        function = event.data.custom_id[4:]

        key, config = get_cfg_for_game(event.guild.id, 'active_games_channel', event.channel.id)

        if function == "toggle_lfg_role":

            if config.lfg_role in event.member.roles:
                self.client.state.guilds[event.guild.id].get_member(event.member.id).remove_role(
                    config.lfg_role, reason="Toggled LFG role through button.")
            else:
                self.client.state.guilds[event.guild.id].get_member(event.member.id).add_role(
                    config.lfg_role, reason="Toggled LFG role through button.")

            return event.reply(type=6)

        if function == "start_game":
            select_row = ActionRow()

            select_menu = MessageComponent()
            select_menu.type = ComponentTypes.STRING_SELECT
            select_menu.custom_id = "pug_select_region"
            select_menu.placeholder = "Select a region!"

            for region in ["NA", "EU", "Oceanic"]:
                option = SelectOption()
                option.label = region
                option.value = region
                option.emoji = None

                select_menu.options.append(option)

            select_menu.max_values = 1
            select_menu.min_values = 1

            select_row.add_component(select_menu)

            return event.reply(type=4, content="Please select the initial region for the game!",
                               components=[select_row.to_dict()], flags=(1 << 6))

        if function == "select_region":
            content = f"**Selected Region**: {event.data.values[0]}\nWould you like to supply server information now?"

            yes_no = ActionRow()

            yes = MessageComponent()
            yes.type = ComponentTypes.BUTTON
            yes.style = ButtonStyles.SUCCESS
            yes.emoji = None
            yes.label = "Yes"
            yes.custom_id = "pug_supply_server_yes"

            no = MessageComponent()
            no.type = ComponentTypes.BUTTON
            no.style = ButtonStyles.DANGER
            no.label = "No"
            no.emoji = None
            no.custom_id = "pug_supply_server_no"

            yes_no.add_component(yes)
            yes_no.add_component(no)

            self.info_cache[event.member.id] = {"region": event.data.values[0]}

            return event.reply(type=7, content=content,
                               components=[yes_no.to_dict()], flags=(1 << 6))

        if function == "supply_server_yes":
            server_name = MessageComponent()
            server_name.type = ComponentTypes.TEXT_INPUT
            server_name.style = TextInputStyles.SHORT
            server_name.label = "Server Host"
            server_name.placeholder = "127.0.0.1:27015"
            server_name.required = True
            server_name.custom_id = "server_name"

            server_password = MessageComponent()
            server_password.type = ComponentTypes.TEXT_INPUT
            server_password.style = TextInputStyles.SHORT
            server_password.label = "Server Password"
            server_password.placeholder = "Password1234"
            server_password.required = False
            server_password.custom_id = "server_password"

            ar1 = ActionRow()
            ar1.add_component(server_name)
            ar2 = ActionRow()
            ar2.add_component(server_password)

            modal = MessageModal()
            modal.title = "Server Information"
            modal.custom_id = "pug_server_info"
            modal.add_component(ar1)
            modal.add_component(ar2)

            event.reply(type=9, modal=modal)
            return event.edit(content="Continue through modal!")
            # TODO: Delete previous message
            # return event.delete()

        if function == "supply_server_no":
            return self.start_game(event, self.info_cache.pop(event.member.id), config)

        if function == "server_info":

            server_info = {}

            for action_row in event.data.components:
                for component in action_row.components:
                    if component.custom_id == "server_name":
                        server_info['server_name'] = component.value
                        break
                    if component.custom_id == "server_password":
                        if component.value:
                            server_info['server_password'] = component.value
                        break

            self.info_cache[event.member.id]['server_info'] = server_info

            content = Messages.pickup_pregame_confirm_server_info.format(
                region=self.info_cache[event.member.id]['region'],
                server_name=server_info['server_name'],
                server_password=server_info.get('server_password') or "~~NONE~~")
            yes_no = ActionRow()

            yes = MessageComponent()
            yes.type = ComponentTypes.BUTTON
            yes.style = ButtonStyles.SUCCESS
            yes.label = "Yes"
            yes.emoji = None
            yes.custom_id = "pug_confirm_yes"

            no = MessageComponent()
            no.type = ComponentTypes.BUTTON
            no.style = ButtonStyles.DANGER
            no.label = "No"
            no.emoji = None
            no.custom_id = "pug_confirm_no"

            yes_no.add_component(yes)
            yes_no.add_component(no)

            return event.reply(type=4, content=content,
                               components=[yes_no.to_dict()], flags=(1 << 6))

        if function == "confirm_yes":
            return self.start_game(event, self.info_cache.pop(event.member.id), config)

        if function == "confirm_no":
            return event.reply(type=7, content="Setup cancelled.").after(10).delete()

        if function == "join_game":
            game = PickupGame.get_or_none(active_game_message_id=event.message.id)

            if not game:
                return event.reply(type=4, content="**Unable To Join Game**: `Game not found`", flags=(1 << 6))

            if event.member.id in game.extra_info['players']:
                return event.reply(type=4, content="**Unable To Join Game**: `Already a player`", flags=(1 << 6))

            game.extra_info['players'].append(event.member.id)

            game.save()

            chat_channel = self.client.state.channels.get(game.chat_channel_id) or self.client.api.channels_get(
                game.chat_channel_id)
            chat_channel.create_overwrite(event.member, allow=(Permissions.VIEW_CHANNEL + Permissions.CONNECT))

            chat_channel.send_message(content=f"Welcome to the game <@{event.member.id}>!",
                                      allowed_mentions={"parse": ["users"]})

            self.update_game_info(game, config, update_player_count=True)

            return event.reply(type=4, content="Game joined!", flags=(1 << 6)).after(10).delete()

    @Plugin.command('sendpugmsg')
    def send_pug_msg(self, event):
        content = "replaced with template"
        buttons_row = ActionRow()

        start_game = MessageComponent()
        start_game.type = ComponentTypes.BUTTON
        start_game.style = ButtonStyles.SUCCESS
        start_game.label = "Click to Start a Game!"
        start_game.custom_id = "pug_start_game"

        toggle_role = MessageComponent()
        toggle_role.type = ComponentTypes.BUTTON
        toggle_role.style = ButtonStyles.SECONDARY
        toggle_role.label = "Click to toggle LFG role!"
        toggle_role.custom_id = "pug_toggle_lfg_role"

        buttons_row.add_component(toggle_role)
        buttons_row.add_component(start_game)

        return event.channel.send_message(content=content, components=[buttons_row.to_dict()])
