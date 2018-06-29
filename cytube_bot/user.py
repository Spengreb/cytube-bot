from .util import uncloak_ip


class User:
    """CyTube user.

    Attributes
    ----------
    name : `str`
    password : `str` or `None`
    ip : `str` or `None`
    rank : `int`
    image : `str`
    text : `str`
    afk : `bool`
    muted : `bool`
    """

    def __init__(self,
                 name='', password=None,
                 rank=-1, profile=None, meta=None):
        self.name = name
        self.password = password
        self.rank = rank
        self.image = ''
        self.text = ''
        self.afk = False
        self.muted = False
        self.smuted = False
        self._ip = None
        self.uncloaked_ip = None
        self.aliases = []
        self.update(profile=profile, meta=meta)

    def __str__(self):
        if self.ip is None:
            return '<user "%s" (rank %d)>' % (self.name, self.rank)
        return '<user "%s" [%s %s] (rank %d)>' % (
            self.name, self.ip, self.uncloaked_ip, self.rank
        )

    __repr__ = __str__

    def __eq__(self, user):
        if isinstance(user, User):
            return self.name == user.name
        if isinstance(user, str):
            return self.name == user
        return False

    @property
    def ip(self):
        return self._ip

    @ip.setter
    def ip(self, ip):
        self._ip = ip
        if ip is None:
            self.uncloaked_ip = None
        else:
            self.uncloaked_ip = uncloak_ip(ip)

    @property
    def profile(self):
        return {
            'image': self.image,
            'text': self.text
        }

    @profile.setter
    def profile(self, profile):
        if profile is None:
            profile = {}
        self.image = profile.get('image', '')
        self.text = profile.get('text', '')

    @property
    def meta(self):
        return {
            'afk': self.afk,
            'muted': self.muted,
            'smuted': self.smuted,
            'ip': self.ip,
            'aliases': self.aliases
        }

    @meta.setter
    def meta(self, meta):
        if meta is None:
            meta = {}
        self.afk = meta.get('afk', False)
        self.muted = meta.get('muted', False)
        self.smuted = meta.get('smuted', False)
        self.ip = meta.get('ip', None)
        self.aliases = meta.get('aliases', [])

    def update(self,
               name=None, rank=None,
               profile=None, meta=None):
        if name is not None:
            self.name = name
        if rank is not None:
            self.rank = rank
        if profile is not None:
            self.profile = profile
        if meta is not None:
            self.meta = meta
