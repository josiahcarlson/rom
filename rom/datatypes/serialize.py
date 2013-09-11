import json
from .column import *
__all__ = ['Json']

class Json(Column):
    '''
    Allows for more complicated nested structures as attributes.

    All standard arguments supported. The ``keygen`` argument must be provided
    if ``index`` is ``True``.

    Used via::

        class MyModel(Model):
            col = Json()
    '''
    _allowed = (dict, list, tuple)
    def _to_redis(self, value):
        return json.dumps(value)
    def _from_redis(self, value):
        if isinstance(value, self._allowed):
            return value
        return json.loads(value)
        
