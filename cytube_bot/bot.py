import re
import json
import logging
import asyncio
import collections

from .error import (
    SocketConfigError, LoginError,
    ChannelPermissionError, Kicked
)
from .socket_io import SocketIO, SocketIOError
from .user import User
from .util import get as default_get


class Bot:
    logger = logging.getLogger(__name__)

    SOCKET_CONFIG_URL = '%(domain)s/socketconfig/%(channel)s.json'
    SOCKET_IO_URL = '%(domain)s/socket.io/'

    GUEST_LOGIN_LIMIT = re.compile(r'guest logins .* ([0-9]+) seconds\.', re.I)
    MUTED = re.compile(r'.*\bmuted', re.I)

    def __init__(self, domain,
                 channel, user=None,
                 restart_on_error=True,
                 loop=None,
                 response_timeout=0.1,
                 get=default_get,
                 socket_io=SocketIO.connect):
        self.get = get
        self.socket_io = socket_io
        self.response_timeout = response_timeout
        self.restart_on_error = restart_on_error
        self.domain = domain
        self.channel = channel
        self.user = user if user is not None else User()
        self.user_count = 0
        self.loop = loop or asyncio.get_event_loop()
        self.server = None
        self.socket = None
        self.handlers = collections.defaultdict(list)
        for attr in dir(self):
            if attr.startswith('_on_'):
                self.on(attr[4:], getattr(self, attr))

    def _on_rank(self, _, data):
        self.user.rank = data

    def _on_setMotd(self, _, data):
        self.channel.motd = data

    def _on_channelCSSJS(self, _, data):
        self.channel.css = data.get('css', '')
        self.channel.js = data.get('js', '')

    def _on_channelOpts(self, _, data):
        self.channel.options = data

    def _on_setPermissions(self, _, data):
        self.channel.permissions = data

    def _on_emoteList(self, _, data):
        self.channel.emotes = data

    def _on_drinkCount(self, _, data):
        self.channel.drink_count = data

    def _on_usercount(self, _, data):
        self.channel.user_count = data

    def _on_needPassword(self, _, data): # pylint:disable=no-self-use
        if data:
            raise LoginError('invalid channel password')

    def _on_noflood(self, _, data):
        self.loger.error('noflood: %s', data)

    def _on_kick(self, _, data): # pylint:disable=no-self-use
        raise Kicked(data)

    def _add_user(self, data):
        if data['name'] == self.user.name:
            self.user.update(**data)
            self.channel.add_user(self.user)
        else:
            self.channel.add_user(User(**data))

    def _on_userlist(self, _, data):
        self.channel.users = []
        for user in data:
            self._add_user(user)
        self.logger.info('userlist: %s', self.channel.users)

    def _on_addUser(self, _, data):
        self._add_user(data)
        self.logger.info('userlist: %s', self.channel.users)

    def _on_userLeave(self, _, data):
        user = data['name']
        try:
            self.channel.remove_user(user)
        except ValueError:
            self.logger.error('userLeave: %s not found', user)
        self.logger.info('userlist: %s', self.channel.users)

    def _on_setUserMeta(self, _, data):
        self.channel.update_user(data['name'], meta=data['meta'])

    def _on_setPlaylistMeta(self, _, data):
        self.channel.playlist.time = data.get('rawTime', 0)

    def _on_mediaUpdate(self, _, data):
        self.channel.playlist.paused = data.get('paused', True)
        self.channel.playlist.current_time = data.get('currentTime', 0)

    def _on_setCurrent(self, _, data):
        self.channel.playlist.current = data
        self.logger.info('setCurrent %s', self.channel.playlist.current)

    def _on_queue(self, _, data):
        self.channel.playlist.add(data['after'], data['item'])
        self.logger.info('queue %s', self.channel.playlist.queue)

    def _on_delete(self, _, data):
        self.channel.playlist.remove(data['uid'])
        self.logger.info('delete %s', self.channel.playlist.queue)

    def _on_setTemp(self, _, data):
        self.channel.playlist.get(data['uid']).temp = data['temp']

    def _on_playlist(self, _, data):
        self.channel.playlist.clear()
        for item in data:
            self.channel.playlist.add(None, item)
        self.logger.info('playlist %s', self.channel.playlist.queue)

    @asyncio.coroutine
    def get_socket_config(self):
        data = {
            'domain': self.domain,
            'channel': self.channel.name
        }
        url = self.SOCKET_CONFIG_URL % data
        if not url.startswith('http'):
            url = 'https://' + url
        self.logger.info('get_socket_config %s', url)
        conf = yield from self.get(url, loop=self.loop)
        conf = json.loads(conf)
        self.logger.info(conf)
        if 'error' in conf:
            raise SocketConfigError(conf['error'])
        try:
            server = next(
                srv['url']
                for srv in conf['servers']
                if srv['secure']
            )
            self.logger.info('secure server %s', server)
        except (KeyError, StopIteration):
            self.logger.info('no secure servers')
            try:
                server = next(srv['url'] for srv in conf['servers'])
                self.logger.info('server %s', server)
            except (KeyError, StopIteration):
                self.logger.info('no servers')
                raise SocketConfigError('no servers in socket config', conf)
        data['domain'] = server
        self.server = self.SOCKET_IO_URL % data

    @asyncio.coroutine
    def disconnect(self):
        if self.socket is None:
            return
        self.logger.info('disconnect %s', self.server)
        try:
            yield from self.socket.close()
        except Exception as ex:
            self.logger.error('socket.close(): %s: %r', self.server, ex)
            raise
        finally:
            self.socket = None
            self.user.rank = -1

    @asyncio.coroutine
    def connect(self):
        yield from self.disconnect()
        if self.server is None:
            yield from self.get_socket_config()
        self.logger.info('connect %s', self.server)
        self.socket = yield from self.socket_io(self.server, loop=self.loop)

    @asyncio.coroutine
    def login(self):
        yield from self.connect()

        self.logger.info('join channel %s', self.channel)
        res = yield from self.socket.emit(
            'joinChannel',
            {
                'name': self.channel.name,
                'pw': self.channel.password
            },
            'needPassword',
            self.response_timeout
        )
        if res:
            raise LoginError('invalid channel password')

        if not self.user.name:
            self.logger.warning('no user')
        else:
            while True:
                self.logger.info('login %s', self.user)
                res = yield from self.socket.emit(
                    'login',
                    {
                        'name': self.user.name,
                        'pw': self.user.password
                    },
                    True
                )
                self.logger.info('login %s', res)
                if res.get('success', False):
                    break
                err = res.get('error', '<no error message>')
                self.logger.error('login error: %s', res)
                match = self.GUEST_LOGIN_LIMIT.match(err)
                if match:
                    try:
                        delay = max(int(match.group(1)), 1)
                        self.logger.warning('sleep(%d)', delay)
                        yield from asyncio.sleep(delay)
                    except ValueError:
                        raise LoginError(err)
                else:
                    raise LoginError(err)
        yield from self.trigger('login', self)

    @asyncio.coroutine
    def run(self):
        try:
            yield from self.login()
            self.logger.info('start')
            while True:
                try:
                    ev, data = yield from self.socket.recv()
                except SocketIOError as ex:
                    self.logger.error('network error: %r', ex)
                    if not self.restart_on_error:
                        break
                    self.logger.error('restarting')
                    yield from asyncio.sleep(self.socket.retry_delay)
                    yield from self.login()
                else:
                    yield from self.trigger(ev, data)
        except asyncio.CancelledError:
            self.logger.info('cancelled')
        finally:
            yield from self.disconnect()

    def on(self, event, *handlers):
        ev_handlers = self.handlers[event]
        for handler in handlers:
            if handler not in ev_handlers:
                ev_handlers.append(handler)
                self.logger.info('on: %s %s', event, handler)
            else:
                self.logger.info('on: handler exists: %s %s', event, handler)
        return self

    def off(self, event, *handlers):
        ev_handlers = self.handlers[event]
        for handler in handlers:
            try:
                ev_handlers.remove(handler)
                self.logger.info('off: %s %s', event, handler)
            except ValueError:
                self.logger.info('off: handler not found: %s %s', event, handler)
        return self

    @asyncio.coroutine
    def trigger(self, event, data):
        self.logger.info('trigger: %s %s', event, data)
        for handler in self.handlers[event]:
            if asyncio.iscoroutinefunction(handler):
                stop = yield from handler(event, data)
            else:
                stop = handler(event, data)
            if stop:
                break

    @asyncio.coroutine
    def chat_message(self, msg, to=None, meta=None):
        self.logger.info('chat_message %s', msg)
        self.channel.check_permission('chat', self.user)

        data = {'msg': msg, 'meta': meta if meta else {}}
        if to is not None:
            ev = 'pm'
            data['to'] = to
        elif self.user.muted or self.user.smuted:
            raise ChannelPermissionError('muted')
        else:
            ev = 'chatMsg'

        res = yield from self.socket.emit(
            ev, data,
            'noflood',
            self.response_timeout
        )
        if res:
            self.logger.error('chat_message: noflood: %s', res)
            raise ChannelPermissionError(res.get('msg', 'noflood'))
            #if self.MUTED.match(res['msg']):
            #    raise ChannelPermissionError('muted')
