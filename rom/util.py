
'''
Changing connection settings
============================

There are 4 ways to change the way that ``rom`` connects to Redis.

1. Set the global default connection settings by calling
   ``rom.util.set_connection_settings()``` with the same arguments you would
   pass to the redis.Redis() constructor::

    import rom.util

    rom.util.set_connection_settings(host='myhost', db=7)

2. Give each model its own Redis connection on creation, called _connection,
   which will be used whenever any Redis-related calls are made on instances
   of that model::

    import rom

    class MyModel(rom.Model):
        _conn = redis.Redis(host='myhost', db=7)

        attribute = rom.String()
        other_attribute = rom.String()

3. Replace the ``CONNECTION`` object in ``rom.util``::

    import rom.util

    rom.util.CONNECTION = redis.Redis(host='myhost', db=7)

4. Monkey-patch ``rom.util.get_connection`` with a function that takes no
   arguments and returns a Redis connection::

    import rom.util

    def my_connection():
        # Note that this function doesn't use connection pooling,
        # you would want to instantiate and cache a real connection
        # as necessary.
        return redis.Redis(host='myhost', db=7)

    rom.util.get_connection = my_connection
'''

from datetime import datetime, date, time as dtime
import string
import redis

from .exceptions import ORMError
from .session import session

__all__ = '''
    get_connection refresh_indices set_connection_settings'''.split()

CONNECTION = redis.Redis()

def set_connection_settings(*args, **kwargs):
    '''
    Update the global connection settings for models that don't have
    model-specific connections.
    '''
    global CONNECTION
    CONNECTION = redis.Redis(*args, **kwargs)

def get_connection():
    '''
    Override me for one of the ways to change the way I connect to Redis.
    '''
    return CONNECTION

def _connect(obj):
    '''
    Tries to get the _conn attribute from a model. Barring that, gets the
    global default connection using other methods.
    '''
    from .__init__ import Model
    if isinstance(obj, Model):
        obj = obj.__class__
    if hasattr(obj, '_conn'):
        return obj._conn
    return get_connection()

# These are just the default index key generators for numeric and string
# types. Numeric keys are used as values in a ZSET, for range searching and
# sorting. Strings are tokenized trivially, and are used as keys to map into
# standard SETs for fairly simple intersection/union searches (like for tags).
# With a bit of work on the string side of things, you could build a simple
# autocomplete, or on the numeric side of things, you could take things like
# 15.23 and turn it into a "$$" key (representing that the value is between 10
# and 20).
# If you return a dictionary with {'':...}, then you can search by just that
# attribute. But if you return a dictionary with {'x':...}, when searching,
# you must use <attr>:x as the attribute for the column.
# For an example of fetching a range based on a numeric index...
# results = Model.query.filter(col=(start, end)).execute()
# vs.
# results = Model.query.filter(**{'col:ex':(start, end)}).execute()

def _numeric_keygen(val):
    if val is None:
        return None
    if isinstance(val, (datetime, date)):
        val = dt2ts(val)
    elif isinstance(val, dtime):
        val = t2ts(val)
    return {'': repr(val) if isinstance(val, float) else str(val)}

def _boolean_keygen(val):
    return [str(bool(val))]

def _string_keygen(val):
    if val in (None, ''):
        return None
    r = sorted(set(filter(None, [s.lower().strip(string.punctuation) for s in val.split()])))
    if isinstance(val, unicode):
        return [s.encode('utf-8') for s in r]
    return r

def _many_to_one_keygen(val):
    if val is None:
        return []
    if hasattr(val, '_data') and hasattr(val, '_pkey'):
        return {'': val._data[val._pkey]}
    return {'': val.id}

_epoch = datetime(1970, 1, 1)
_epochd = _epoch.date()
def dt2ts(value):
    if isinstance(value, datetime):
        delta = value - _epoch
    else:
        delta = value - _epochd
    return delta.days * 86400 + delta.seconds + delta.microseconds / 1000000.

def ts2dt(value):
    return datetime.utcfromtimestamp(value)

def t2ts(value):
    return value.hour*3600 + value.minute * 60 + value.second + value.microsecond / 1000000.

def ts2t(value):
    hour, value = divmod(value, 3600)
    minute, value = divmod(value, 60)
    second, value = divmod(value, 1)
    return dtime(*map(int, [hour, minute, second, value*1000000]))


def refresh_indices(model, block_size=100):
    '''
    This utility function will iterate over all entities of a provided model,
    refreshing their indices. This is primarily useful after adding an index
    on a column.

    Arguments:

        * *model* - the model whose entities you want to reindex
        * *block_size* - the maximum number of entities you want to fetch from
          Redis at a time, defaulting to 100

    This function will yield its progression through re-indexing all of your
    entities.

    Example use::

        for progress, total in refresh_indices(MyModel, block_size=200):
            print "%s of %s"%(progress, total)

    .. note: This uses the session object to handle index refresh via calls to
      ``.commit()``. If you have any outstanding entities known in the
      session, they will be committed.

    '''
    conn = _connect(model)
    max_id = int(conn.get('%s:%s:'%(model.__name__, model._pkey)) or '0')
    block_size = max(block_size, 10)
    for i in xrange(1, max_id+1, block_size):
        # fetches entities, keeping a record in the session
        models = model.get(range(i, i+block_size))
        models # for pyflakes
        # re-save un-modified data, resulting in index-only updates
        session.commit(all=True)
        yield min(i+block_size, max_id), max_id
