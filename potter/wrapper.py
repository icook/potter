import datetime
import json
import logging
try:
    import jinja2
except ImportError:
    jinja2 = None

logger = logging.getLogger('potter')


class Image(object):
    """ A wrapper for images that potter has generated """
    def __init__(self, resp, cache=False):
        self.id = resp.pop('Id')
        self.created = datetime.datetime.utcfromtimestamp(resp.pop('Created', 0))
        self.extra = resp
        self.cache = cache  # Is this image cached?

        labels = {key[7:]: val for key, val in resp['Labels'].items() if key.startswith("potter_")}
        self.config = json.loads(labels.pop('config'))
        self.config_hash = labels.pop('config_hash')
        self.step = int(labels.pop('step'))
        self.runtime = float(labels.pop('runtime', 0))
        self.potter_labels = labels

        assert len(self.id) == 64

    @classmethod
    def from_inspect(cls, resp):
        new_resp = resp['Config']
        new_resp['Id'] = resp['Id']
        obj = cls(new_resp)
        without_milli = resp['Created'].rsplit(".", 1)[0]
        obj.created = datetime.datetime.strptime(without_milli, "%Y-%m-%dT%H:%M:%S")
        return obj

    @property
    def delta(self):
        return str(datetime.datetime.utcnow() - self.created)

    @property
    def short_id(self):
        return self.id[:12]

    def __hash__(self):
        return int(self.id, 16)

    def __str__(self):
        return "<Image {}>".format(self.id[:12])
