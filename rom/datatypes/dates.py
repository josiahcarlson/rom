from datetime import datetime, date, time as dtime
from .column import Column
from ..util import (dt2ts, ts2dt, ts2t, t2ts)

__all__ = ['DateTime', 'Date', 'Time']

class DateTime(Column):
    '''
    A datetime column.

    All standard arguments supported.

    Used via::

        class MyModel(Model):
            col = DateTime()

    .. note:: tzinfo objects are not stored
    '''
    _allowed = datetime
    def _from_redis(self, value):
        return ts2dt(float(value))
    def _to_redis(self, value):
        return repr(dt2ts(value))


class Date(Column):
    '''
    A date column.

    All standard arguments supported.

    Used via::

        class MyModel(Model):
            col = Date()
    '''
    _allowed = date
    def _from_redis(self, value):
        return ts2dt(float(value)).date()
    def _to_redis(self, value):
        return repr(dt2ts(value))



class Time(Column):
    '''
    A time column. Timezones are ignored.

    All standard arguments supported.

    Used via::

        class MyModel(Model):
            col = Time()

    .. note:: tzinfo objects are not stored
    '''
    _allowed = dtime
    def _from_redis(self, value):
        return ts2t(float(value))
    def _to_redis(self, value):
        return repr(t2ts(value))