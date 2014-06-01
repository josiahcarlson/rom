
'''
Changing connection settings
============================

There are 4 ways to change the way that ``rom`` connects to Redis.

1. Set the global default connection settings by calling
   ``rom.util.set_connection_settings()``` with the same arguments you would
   pass to the redis.Redis() constructor::

    import rom.util

    rom.util.set_connection_settings(host='myhost', db=7)

2. Give each model its own Redis connection on creation, called _conn, which
   will be used whenever any Redis-related calls are made on instances of that
   model::

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
import threading
import weakref

import redis
from redis.client import BasePipeline
import six

from .exceptions import ORMError

__all__ = '''
    get_connection Session refresh_indices set_connection_settings'''.split()

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
    from .columns import MODELS
    if isinstance(obj, MODELS['Model']):
        obj = obj.__class__
    if hasattr(obj, '_conn'):
        return obj._conn
    return get_connection()

class ClassProperty(object):
    '''
    Borrowed from: https://gist.github.com/josiahcarlson/1561563
    '''
    def __init__(self, get, set=None, delete=None):
        self.get = get
        self.set = set
        self.delete = delete

    def __get__(self, obj, cls=None):
        if cls is None:
            cls = type(obj)
        return self.get(cls)

    def __set__(self, obj, value):
        cls = type(obj)
        self.set(cls, value)

    def __delete__(self, obj):
        cls = type(obj)
        self.delete(cls)

    def getter(self, get):
        return ClassProperty(get, self.set, self.delete)

    def setter(self, set):
        return ClassProperty(self.get, set, self.delete)

    def deleter(self, delete):
        return ClassProperty(self.get, self.set, delete)

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
    if isinstance(val, float):
        val = repr(val)
    elif val in (None, ''):
        return None
    elif not isinstance(val, six.string_types):
        val = str(val)
    r = sorted(set([x for x in [s.lower().strip(string.punctuation) for s in val.split()] if x]))
    if isinstance(val, six.string_types) and not isinstance(val, str):  # unicode on py2k
        return [s.encode('utf-8') for s in r]
    return r

def _many_to_one_keygen(val):
    if val is None:
        return []
    if hasattr(val, '_data') and hasattr(val, '_pkey'):
        return {'': val._data[val._pkey]}
    return {'': val.id}

def _to_score(v, s=False):
    v = repr(v) if isinstance(v, float) else str(v)
    if s:
        if v[:1] != '(':
            return '(' + v
    return v.lstrip('(')

# borrowed and modified from:
# https://gist.github.com/josiahcarlson/8459874
def _bigint_to_float(v):
    assert isinstance(v, six.integer_types)
    sign = -1 if v < 0 else 1
    v *= sign
    assert v < 0x7fe0000000000000
    exponent, mantissa = divmod(v, 2**52)
    return sign * (2**52 + mantissa) * 2.0**(exponent-52-1022)

def _prefix_score(v, next=False):
    if isinstance(v, six.text_type):
        v = v.encode('utf-8')
    # We only get 7 characters of score-based prefix.
    score = 0
    for ch in six.iterbytes(v[:7]):
        score *= 258
        score += ch + 1
    if next:
        score += 1
    score *= 258 ** max(0, 7-len(v))
    return repr(_bigint_to_float(score))

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

class Session(threading.local):
    '''
    This is a very dumb session. All it tries to do is to keep a cache of
    loaded entities, offering the ability to call ``.save()`` on modified (or
    all) entities with ``.flush()`` or ``.commit()``.

    This is exposed via the ``session`` global variable, which is available
    when you ``import rom`` as ``rom.session``.

    .. note: calling ``.flush()`` or ``.commit()`` doesn't cause all objects
        to be written simultanously. They are written one-by-one, with any
        error causing the call to fail.
    '''
    def _init(self):
        try:
            self.known
        except AttributeError:
            self.known = {}
            self.wknown = weakref.WeakValueDictionary()

    def add(self, obj):
        '''
        Adds an entity to the session.
        '''
        self._init()
        self.known[obj._pk] = obj
        self.wknown[obj._pk] = obj

    def forget(self, obj):
        '''
        Forgets about an entity (automatically called when an entity is
        deleted). Call this to ensure that an entity that you've modified is
        not automatically saved on ``session.commit()`` .
        '''
        self._init()
        self.known.pop(obj._pk, None)
        self.wknown.pop(obj._pk, None)

    def get(self, pk):
        '''
        Fetches an entity from the session based on primary key.
        '''
        self._init()
        return self.known.get(pk) or self.wknown.get(pk)

    def rollback(self):
        '''
        Forget about all entities in the session (``.commit()`` will do
        nothing).
        '''
        self.known = {}
        self.wknown = weakref.WeakValueDictionary()

    def flush(self, full=False, all=False):
        '''
        Call ``.save()`` on all modified entities in the session. Use when you
        want to flush changes to Redis, but don't want to lose your local
        session cache.

        See the ``.commit()`` method for arguments and their meanings.
        '''
        self._init()
        changes = 0
        for value in self.known.values():
            if not value._deleted and (all or value._modified):
                changes += value.save(full)
        return changes

    def commit(self, full=False, all=False):
        '''
        Call ``.save()`` on all modified entities in the session. Also forgets
        all known entities in the session, so this should only be called at
        the end of a request.

        Arguments:

            * *full* - pass ``True`` to force save full entities, not only
              changes
            * *all* - pass ``True`` to save all entities known, not only those
              entities that have been modified.
        '''
        changes = self.flush(full, all)
        self.known = {}
        return changes

    def save(self, *objects, **kwargs):
        '''
        This method an alternate API for saving many entities (possibly not
        tracked by the session). You can call::

            session.save(obj)
            session.save(obj1, obj2, ...)
            session.save([obj1, obj2, ...])

        And the entities will be flushed to Redis.

        You can pass the keyword arguments ``full`` and ``all`` with the same
        meaning and semantics as the ``.commit()`` method.
        '''
        from rom import Model
        full = kwargs.get('full')
        all = kwargs.get('all')
        changes = 0
        for o in objects:
            if isinstance(o, (list, tuple)):
                changes += self.save(*o, full=full, all=all)
            elif isinstance(o, Model):
                if not o._deleted and (all or o._modified):
                    changes += o.save(full)
            else:
                raise ORMError(
                    "Cannot save an object that is not an instance of a Model (you provided %r)"%(
                        o,))
        return changes

    def refresh(self, *objects, **kwargs):
        '''
        This method is an alternate API for refreshing many entities (possibly
        not tracked by the session). You can call::

            session.refresh(obj)
            session.refresh(obj1, obj2, ...)
            session.refresh([obj1, obj2, ...])

        And all provided entities will be reloaded from Redis.

        To force reloading for modified entities, you can pass ``force=True``.
        '''
        from rom import Model
        force = kwargs.get('force')
        for o in objects:
            if isinstance(o, (list, tuple)):
                self.refresh(*o, force=force)
            elif isinstance(o, Model):
                if not o._new:
                    o.refresh(force=force)
                else:
                    # all objects are re-added to the session after refresh,
                    # except for deleted entities...
                    self.add(o)
            else:
                raise ORMError(
                    "Cannot refresh an object that is not an instance of a Model (you provided %r)"%(
                        o,))

    def refresh_all(self, *objects, **kwargs):
        '''
        This method is an alternate API for refreshing all entities tracked
        by the session. You can call::

            session.refresh_all()
            session.refresh_all(force=True)

        And all entities known by the session will be reloaded from Redis.

        To force reloading for modified entities, you can pass ``force=True``.
        '''
        self.refresh(*self.known.values(), force=kwargs.get('force'))

session = Session()

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
    for i in range(1, max_id+1, block_size):
        # fetches entities, keeping a record in the session
        models = model.get(list(range(i, i+block_size)))
        models # for pyflakes
        # re-save un-modified data, resulting in index-only updates
        session.commit(all=True)
        yield min(i+block_size, max_id), max_id

def _script_load(script):
    '''
    Borrowed/modified from my book, Redis in Action:
    https://github.com/josiahcarlson/redis-in-action/blob/master/python/ch11_listing_source.py

    Used for Lua scripting support when writing against Redis 2.6+ to allow
    for multiple unique columns per model.
    '''
    sha = [None]
    def call(conn, keys=[], args=[], force_eval=False):
        if not force_eval:
            if not sha[0]:
                ec = conn.immediate_execute_command if isinstance(conn, BasePipeline) else conn.execute_command
                sha[0] = ec("SCRIPT", "LOAD", script, parse="LOAD")

            try:
                return conn.execute_command(
                    "EVALSHA", sha[0], len(keys), *(keys+args))

            except redis.exceptions.ResponseError as msg:
                if not msg.args[0].startswith("NOSCRIPT"):
                    raise

        return conn.execute_command(
            "EVAL", script, len(keys), *(keys+args))

    return call
