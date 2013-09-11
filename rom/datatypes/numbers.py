from decimal import Decimal as _Decimal

__all__ = ['Decimal', 'Float', 'Integer']

from .column import *

class Decimal(Column):
    '''
    A Decimal-only numeric column (converts ints/longs into Decimals
    automatically). Attempts to assign Python float will fail.

    All standard arguments supported.

    Used via::

        class MyModel(Model):
            col = Decimal()
    '''
    _allowed = _Decimal
    def _from_redis(self, value):
        return _Decimal(value)
    def _to_redis(self, value):
        return str(value)


class Float(Column):
    '''
    Numeric column that supports integers and floats (values are turned into
    floats on load from Redis).

    All standard arguments supported.

    Used via::

        class MyModel(Model):
            col = Float()
    '''
    _allowed = (float, int, long)
    

class Integer(Column):
    '''
    Used for integer numeric columns.

    All standard arguments supported.

    Used via::

        class MyModel(Model):
            col = Integer()
    '''
    _allowed = (int, long)
