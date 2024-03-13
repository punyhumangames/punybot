from datetime import datetime

import gevent


class Eventual(object):
    """
    A function that will be triggered eventually.
    """

    def __init__(self, func):
        self.func = func
        self._next = None
        self._t = None

    def wait(self, nxt):
        def f():
            wait_time = (self._next - datetime.now())
            gevent.sleep(wait_time.seconds + (wait_time.microseconds / 1000000.0))
            self._next = None
            gevent.spawn(self.func)

        if self._t:
            self._t.kill()

        self._next = nxt
        self._t = gevent.spawn(f)

    def trigger(self):
        if self._t:
            self._t.kill()
        self._next = None
        gevent.spawn(self.func)

    def set_next_schedule(self, date):
        if date < datetime.now():
            return gevent.spawn(self.trigger)

        if not self._next or date < self._next:
            self.wait(date)
