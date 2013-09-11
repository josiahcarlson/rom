from .column import *
from ..util import _connect
from ..exceptions import InvalidOperation
from ..session import session
class PrimaryKey(Column):
    '''
    This is a primary key column, used when you want the primary key to be
    named something other than 'id'. If you omit a PrimaryKey column on your
    Model classes, one will be automatically cretaed for you.

    Only the ``index`` argument will be used. You may want to enable indexing
    on this column if you want to be able to perform queries and sort the
    results by primary key.

    Used via:

        class MyModel(Model):
            id = PrimaryKey()
    '''
    _allowed = (int, long)

    def __init__(self, index=False):
        Column.__init__(self, required=False, default=None, unique=False, index=index)

    def _init_(self, obj, model, attr, value, loading):
        self._model = model
        self._attr = attr
        if value is None:
            value = _connect(obj).incr('%s:%s:'%(model, attr))
            obj._modified = True
        else:
            value = int(value)
        obj._data[attr] = value
        session.add(obj)

    def __set__(self, obj, value):
        if not obj._init:
            self._init_(obj, *value)
            return
        raise InvalidOperation("Cannot update primary key value")
        
        
