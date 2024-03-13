from datetime import datetime

import gevent
from disco.api.http import APIException
from disco.bot import Plugin
from disco.types.message import ActionRow, MessageComponent, ComponentTypes, SelectOption

from PunyBot.models.kaboom import KaboomMessage
from PunyBot.utils.timing import Eventual


class KaboomPlugin(Plugin):
    def load(self, ctx):

        self.timed_tasks = Eventual(self.kaboom_messages)

        self.spawn_later(5, self.queue_tasks)

        super(KaboomPlugin, self).load(ctx)

    def kaboom_messages(self):

        next_delete = list(KaboomMessage.select().where(
            (KaboomMessage.expire_time < datetime.now())
        ))

        if len(next_delete) > 0:
            self.log.info(f"[Kaboom System]: Attempting to blow up {len(next_delete)} messages!")
            for msg in next_delete:
                try:
                    self.client.api.channels_messages_delete(msg.channel_id, msg.message_id)
                    self.log.info(f"[Kaboom System]: Message {msg.message_id} has been blown up.")
                    KaboomMessage.delete_by_id(msg.message_id)
                except APIException as e:
                    fail = "[Kaboom System] | Failed to blow up message: {}"
                    if e.code == 10008:
                        self.log.error(fail.format("Message not found..."))
                    elif e.code == 10003:
                        self.log.error(fail.format("Channel not found..."))
                    elif e.code == 50013:
                        self.log.error(fail.format("Permission Denied..."))
                    KaboomMessage.delete_by_id(msg.message_id)
                    self.log.error(f"[Kaboom System] | Removing message: {msg.message_id} from the database to stop infinite loop.")
                    with self.bot.plugins['CorePlugin'].send_control_message() as embed:
                        embed.title = "Kaboom Error"
                        embed.color = 0xf04747
                        embed.add_field(name='Message', value=f'``{msg.message_id}``', inline=True)
                        embed.add_field(name='Channel', value=f'``{msg.channel_id}``', inline=True)
                        embed.description = f'```{fail.format(f"API Error {e.code}, {e.msg}")}```'

            gevent.sleep(5)
            self.queue_tasks()
        else:
            return

    def queue_tasks(self):

        next_delete = list(KaboomMessage.select().order_by(KaboomMessage.expire_time.asc()).limit(1))

        if not next_delete:
            self.log.info("[Kaboom System]: There are no messages to blow up at this time.")
        else:
            self.log.info(
                f"[Kaboom System]: Waiting until {next_delete[0].expire_time} for Message ID: {next_delete[0].message_id}")
            self.timed_tasks.set_next_schedule(next_delete[0].expire_time)

    def mark_as_kaboom(self, message, channel, expire_time):
        kaboom, created = KaboomMessage.get_or_create(message_id=message, channel_id=channel,
                                                      expire_time=datetime.fromtimestamp(
                                                          datetime.now().timestamp() + (expire_time * 60)))
        if created:
            self.queue_tasks()
            return True
        else:
            return False

    # TODO: Move all slash commands to a single register event with a "first_run" flag in the database or something.
    @Plugin.command('setupcmds')
    def setup_commands_cmd(self, event):
        message_cmd = {
            "type": 3,
            "name": "💣",
            "default_member_permissions": "0",
        }

        slash_cmd = {
            "type": 1,
            "name": "kaboom",
            "description": "Mark a message to self-destruct after a certain period of time.",
            "default_member_permissions": "0",
            "options": [
                {
                    "name": "message",
                    "type": "3",
                    "description": "The ID of the message you wish to nuke!",
                    "required": True,
                    "autocomplete": True
                },
                {
                    "name": "time",
                    "type": "4",
                    "description": "Amount of time to pass until your message will expire.",
                    "required": True,
                    "choices": [
                        {
                            "name": "1 Minute",
                            "value": 1
                        },
                        {
                            "name": "15 Minutes",
                            "value": 15
                        },
                        {
                            "name": "1 Hour",
                            "value": 60
                        },
                        {
                            "name": "24 Hours",
                            "value": 1440
                        },
                        {
                            "name": "72 Hours",
                            "value": 4320
                        },
                    ]
                }
            ]
        }

        event.channel.send_typing()

        cmds = self.client.api.applications_guild_commands_get(event.guild.id)

        new_cmds = []

        for cmd in cmds:
            if cmd.name == slash_cmd['name'] and cmd.type == 1:
                new_cmds.append(slash_cmd)
                continue
            elif cmd.name == message_cmd['name'] and cmd.type == 3:
                new_cmds.append(message_cmd)
                continue
            new_cmds.append(cmd.to_dict())

        # Ensure that these commands are in there if they weren't previously registered!
        if slash_cmd not in new_cmds:
            new_cmds.append(slash_cmd)
        elif message_cmd not in new_cmds:
            new_cmds.append(message_cmd)

        try:
            self.client.api.applications_guild_commands_bulk_overwrite(event.guild.id, new_cmds)
        except APIException as error:
            raise error

        event.channel.send_typing()
        return event.msg.reply("Commands have been updated!")

    @Plugin.listen('InteractionCreate')
    def kaboom_cmd(self, event):

        min_to_string = {
            1: "1 Minute",
            15: "15 Minutes",
            60: "1 Hour",
            1440: "1 Day",
            4320: "3 Days"
        }

        if event.raw_data['interaction']['type'] == 2 and event.data.name == "💣":
            msg = list(event.data.resolved.messages.values())[0]

            if event.member.id != msg.author.id:
                return event.reply(type=4, content="Unable to blow up. Message is not your own!", flags=(1 << 6))

            components = ActionRow()

            select_menu = MessageComponent()
            select_menu.type = ComponentTypes.STRING_SELECT
            select_menu.custom_id = f"kaboom_select_{msg.id}"
            select_menu.placeholder = "Select Timeout.."

            for key, value in min_to_string.items():
                option = SelectOption()
                option.label = value
                option.value = key
                option.emoji = None

                select_menu.options.append(option)

            select_menu.max_values = 1
            select_menu.min_values = 1

            components.add_component(select_menu)

            return event.reply(type=4,
                               content="Kaboom Activated! Please select the time in which the message will self destruct...",
                               components=[components.to_dict()], flags=(1 << 6))

        if event.raw_data['interaction']['type'] == 3 and event.data.custom_id.startswith("kaboom_select"):
            message_id = int(event.data.custom_id.replace("kaboom_select_", ""))

            will_kaboom = self.mark_as_kaboom(message_id, event.channel.id, int(event.data.values[0]))

            if will_kaboom:
                msg_link = f"https://discord.com/channels/{event.guild.id}/{event.channel.id}/{message_id}"
                self.client.api.channels_messages_reactions_create(event.channel.id, message_id, "💣")
                return event.reply(type=7,
                                   content=f"[This Message]({msg_link}) will self-destruct in {min_to_string[int(event.data.values[0])]}",
                                   flags=(1 << 6))
            else:
                return event.reply(type=7, content="This message is already marked to blow up. Unable to blow up.", flags=(1 << 6))

        if event.raw_data['interaction']['type'] == 2 and event.data.name == "kaboom":

            will_kaboom = self.mark_as_kaboom(int(event.data.options[0].value), event.channel.id, int(event.data.options[1].value))

            if will_kaboom:
                self.client.api.channels_messages_reactions_create(event.channel.id, event.data.options[0].value, "💣")
                msg_link = f"https://discord.com/channels/{event.guild.id}/{event.channel.id}/{event.data.options[0].value}"
                return event.reply(type=4,
                                   content=f"[This Message]({msg_link}) will self-destruct in {min_to_string[int(event.data.options[1].value)]}",
                                   flags=(1 << 6))
            else:
                return event.reply(type=4, content="Unable to blow up. Message is already marked for deletion!", flags=(1 << 6))

        if event.raw_data['interaction']['type'] == 4 and event.data.name == "kaboom":
            messages = self.client.api.channels_messages_list(event.channel.id, limit=25)

            messages = [msg for msg in messages if msg.author.id == event.member.id]

            choices = []
            for msg in messages:
                if len(choices) == 25:
                    break
                if not msg.content:
                    if msg.attachments:
                        names = [msg.attachments[file].filename for file in msg.attachments]
                        name_str = ", ".join(names)
                        if len(name_str) > 100:
                            choices.append({
                                "name": f"{name_str[:97]}...",
                                "value": str(msg.id)
                            })
                        else:
                            choices.append({
                                "name": name_str,
                                "value": str(msg.id)
                            })
                    else:
                        choices.append({
                            "name": "*NO CONTENT*",
                            "value": str(msg.id)
                        })
                elif len(msg.content) > 100:
                    choices.append({
                        "name": f"{msg.content[:97]}...",
                        "value": str(msg.id)
                    })
                else:
                    choices.append({
                        "name": msg.content,
                        "value": str(msg.id)
                    })

            return event.reply(type=8, choices=choices)
