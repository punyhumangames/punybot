# Written by Nadie <iam@nadie.dev> (https://github.com/hackerjef/) [Used with permission]

import os
import signal

from gevent.exceptions import BlockingSwitchOutError

from disco.bot.plugin import Plugin

from PunyBot.database import sqlite_db

PY_CODE_BLOCK = u'```py\n{}\n```'


class ControlPlugin(Plugin):
    def load(self, ctx):
        super(ControlPlugin, self).load(ctx)
        # register Process listeners
        signal.signal(signal.SIGINT, self.process_control)
        signal.signal(signal.SIGTERM, self.process_control)
        # signal.signal(signal.SIGUSR1, self.ProcessControl)

    def process_control(self, signal_number=None, frame=None):
        if signal_number in [2, 15]:
            self.log.warning("Graceful shutdown initiated")
            self.client.gw.shutting_down = True
            self.client.gw.ws.close(status=4000)
            self.client.gw.ws_event.set()
            # unload plugins
            for x in list(self.bot.plugins):
                if x == 'CorePlugin' or x == 'ControlPlugin':
                    self.log.info('Skipping plugin: {}'.format(x))
                    continue
                plugin = next((v for k, v in self.bot.plugins.items() if k.lower() == x.lower()), None)
                if plugin:
                    self.log.info('Unloading plugin: {}'.format(x))
                    try:
                        self.bot.rmv_plugin(plugin)
                    except BlockingSwitchOutError:
                        self.log.warning("Plugin {} Has a active greenlet/schedule, Bruteforce".format(x))
                        plugin.greenlet = []
                        plugin.schedule = []
                        for listener in plugin.listeners:
                            listener.remove()
                        pass
                    except Exception:
                        self.log.exception("Failed to unload: {}".format(x))
            self.log.info("Closing connection to database")
            try:
                sqlite_db.close()
                self.log.info("Database connection closed!")
            except Exception:
                self.log.exception("Failed to close Database connection.")
        elif signal_number == 10:  # sysuser1
            self.log.warning("Resetting shard connection to Discord")
            self.client.gw.ws.close(status=4000)
        else:
            self.log.warning("Force Shutdown initiated")
            os.kill(os.getpid(), signal.SIGKILL)
