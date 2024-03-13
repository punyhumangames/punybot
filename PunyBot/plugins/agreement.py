from disco.bot import Plugin
from disco.types.application import InteractionType
from disco.types.message import MessageModal, ActionRow, TextInputStyles, ComponentTypes, MessageComponent, ButtonStyles

from PunyBot import CONFIG
from PunyBot.constants import Messages
from PunyBot.models import Agreement


class AgreementPlugin(Plugin):
    def load(self, ctx):
        super(AgreementPlugin, self).load(ctx)

    @Plugin.listen('InteractionCreate')
    def button_listener(self, event):

        #Todo: Switch back to event.type after lib patch.
        if not (event.raw_data['interaction']['type'] != 3 or event.raw_data['interaction']['type'] != 5):
            return

        if event.raw_data['interaction']['type'] != InteractionType.MODAL_SUBMIT:
            if event.data.custom_id != "agreement_start":
                return

            if CONFIG.agreement.pre_process_role not in event.member.roles or CONFIG.agreement.post_process_role in event.member.roles:
                return event.reply(type=6)

            first_name = MessageComponent()
            first_name.type = ComponentTypes.TEXT_INPUT
            first_name.style = TextInputStyles.SHORT
            first_name.label = "First Name"
            first_name.placeholder = "John"
            first_name.required = True
            first_name.custom_id = "first_name"

            last_name = MessageComponent()
            last_name.type = ComponentTypes.TEXT_INPUT
            last_name.style = TextInputStyles.SHORT
            last_name.label = "Last Name"
            last_name.placeholder = "Doe"
            last_name.required = True
            last_name.custom_id = "last_name"

            ar1 = ActionRow()
            ar1.add_component(first_name)
            ar2 = ActionRow()
            ar2.add_component(last_name)

            modal = MessageModal()
            modal.title = "Confidentiality Rules"
            modal.custom_id = "agreement_submit"
            modal.add_component(ar1)
            modal.add_component(ar2)

            return event.reply(type=9, modal=modal)
        else:
            if event.data.custom_id != "agreement_submit":
                return

            first_name = None
            last_name = None

            for action_row in event.data.components:
                for component in action_row.components:
                    if component.custom_id == "first_name":
                        first_name = component.value
                        break
                    if component.custom_id == "last_name":
                        last_name = component.value
                        break

            try:
                guild = self.client.state.guilds[event.guild.id]

                tmp_roles = event.member.roles
                tmp_roles.remove(CONFIG.agreement.pre_process_role)
                tmp_roles.append(CONFIG.agreement.post_process_role)

                guild.get_member(event.member.id).modify(roles=tmp_roles, reason="User signed agreement. Assigning proper role!")
            except:
                self.log.error(f"Unable to add role to user who signed the agreement. User ID {event.member.id}")
            Agreement.create(user_id=event.member.id, first_name=first_name, last_name=last_name)

            return event.reply(type=6)

    @Plugin.command("sendagreementmsg")
    def send_agreement_msg(self, event):
        # Moved message over to template file
        msg = Messages.agreement_message

        ar = ActionRow()

        button = MessageComponent()
        button.type = ComponentTypes.BUTTON
        button.style = ButtonStyles.SECONDARY
        button.emoji = None
        button.label = "Click to Sign"
        button.custom_id = "agreement_start"

        ar.add_component(button)

        event.channel.send_message(content=msg, components=[ar.to_dict()])
