from .column import *

class Text(Column):
    '''
    A unicode string column.

    All standard arguments supported, except for ``unique``.

    Aside from not supporting ``unique`` indices, will generally have the same
    behavior as a ``String`` column, only supporting unicode strings. Data is
    encoded via utf-8 before writing to Redis. If you would like to create
    your own column to encode/decode differently, examine the source find out
    how to do it.

    Used via::

        class MyModel(Model):
            col = Text()
    '''
    _allowed = unicode
    def _to_redis(self, value):
        return value.encode('utf-8')
    def _from_redis(self, value):
        if isinstance(value, str):
            return value.decode('utf-8')
        return value
