
'''
Changing connection settings
============================

There are four ways to change the way that ``rom`` connects to Redis.

1. Set the global default connection settings by calling
   ``rom.util.set_connection_settings()`` with the same arguments you would
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


Using a non-caching session object
==================================

If you would like to stop ``rom`` from caching your data for later
``session.commit()`` or faster data fetching (and there are several reasons for
doing this), ``rom`` offers two methods to enable or disable caching on either a
global or per-thread basis.

1. To set the global default behavior as not caching anything, you can::

    import rom.util
    rom.util.use_null_session()

   From the point that ``rom.util.use_null_session()`` is called, no additional
   caching will be performed. You must explicitly ``.save()`` any newly created
   entities, and ``session.commit()`` will only save those objects that had been
   cached prior to the ``rom.util.use_null_session()`` call.

   You can switch back to the standard ``rom`` behavior by calling
   ``rom.util.use_rom_session()``.

2. To override behavior on a per-thread basis, you can set the attribute
   ``null_session`` on the ``session`` object (which is available as
   ``rom.session``, ``rom.columns.session``, or ``rom.util.session``), which
   will set the thread's behavior to be uncached (``session.null_session =
   True``), cached (``session.null_session = False``), or the global default
   (``del session.null_session``).

'''

from __future__ import print_function
from datetime import datetime, date, time as dtime
from hashlib import sha1
from itertools import chain
import string
import threading
import time
import weakref
try:
    import msgpack
    USE_MSGPACK = True
    dumps = msgpack.dumps
except ImportError:
    import json
    USE_MSGPACK = False
    dumps = json.dumps

_lua_encoder = 'cmsgpack.pack' if USE_MSGPACK else 'cjson.encode'
_lua_decoder = 'cmsgpack.unpack' if USE_MSGPACK else 'cjson.decode'

import redis
from redis.client import BasePipeline
import six

from .exceptions import ORMError

__all__ = '''
    get_connection Session refresh_indices set_connection_settings
    clean_old_index show_progress use_null_session use_rom_session dumps _lua_encoder _lua_decoder'''.split()

CONNECTION = redis.Redis()
USE_LUA = True

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

def _encode_unique_constraint(data):
    cleaned = []
    for col in data:
        col = (col or b'')
        if not isinstance(col, (six.text_type, six.binary_type)):
            col = six.text_type(col)
        if isinstance(col, six.text_type):
            col = col.encode('utf-8')
        cleaned.append(b'\0\0' + col)

    ret = b'\0'.join(cleaned)
    return ret if six.PY2 else ret.decode('latin-1')

NULL_SESSION = False

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

    @property
    def null_session(self):
        return getattr(self, '_null_session', NULL_SESSION)

    @null_session.setter
    def null_session(self, value):
        self._null_session = value

    @null_session.deleter
    def null_session(self):
        self._null_session = None
        del self._null_session

    def add(self, obj):
        '''
        Adds an entity to the session.
        '''
        if self.null_session:
            return
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
        This method is an alternate API for saving many entities (possibly not
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

def use_null_session():
    '''
    If you call ``use_null_session()``, you will change the default session for
    all threads to *not cache*. You can override the default on a per-thread
    basis by manipulating ``session.null_session`` (set to ``True``, ``False``,
    or delete the attribute to not cache, cache, or use the global default,
    respectively).
    '''
    global NULL_SESSION
    NULL_SESSION = True

def use_rom_session():
    '''
    If you call ``use_rom_session()``, you will change the default session for
    all threads to *cache*. You can override the default on a per-thread basis
    by manipulating ``session.null_session`` (set to ``True``, ``False``, or
    delete the attribute to not cache, cache, or use the global default,
    respectively).
    '''
    global NULL_SESSION
    NULL_SESSION = False

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

def clean_old_index(model, block_size=100, **kwargs):
    '''
    This utility function will clean out old index data that was accidentally
    left during item deletion in rom versions <= 0.27.0 . You should run this
    after you have upgraded all of your clients to version 0.28.0 or later.

    Arguments:

        * *model* - the model whose entities you want to reindex
        * *block_size* - the maximum number of items to check at a time
          defaulting to 100

    This function will yield its progression through re-checking all of the
    data that could be left over.

    Example use::

        for progress, total in clean_old_index(MyModel, block_size=200):
            print "%s of %s"%(progress, total)
    '''

    if not USE_LUA:
        raise Exception("Lua must be enabled to clean out old indexes")

    conn = _connect(model)
    version = list(map(int, conn.info('server')['redis_version'].split('.')[:2]))
    has_hscan = version >= [2, 8]
    pipe = conn.pipeline(True)
    prefix = '%s:'%model.__name__
    index = prefix + ':'
    block_size = max(block_size, 10)

    force_hscan = kwargs.get('force_hscan', False)
    if (has_hscan or force_hscan) and force_hscan is not None:
        max_id = conn.hlen(index)
        cursor = None
        scanned = 0
        while cursor != b'0':
            cursor, remove = _scan_index_lua(conn, [prefix], [cursor or '0', block_size])
            if remove:
                _clean_index_lua(conn, [model.__name__], remove)

            scanned += block_size
            if scanned > max_id:
                max_id = scanned + 1
            yield scanned, max_id

    else:
        max_id = int(conn.get('%s%s:'%(prefix, model._pkey)) or '0')
        for i in range(1, max_id+1, block_size):
            ids = list(range(i, min(i+block_size, max_id+1)))
            for id in ids:
                pipe.exists(prefix + str(id))
                pipe.hexists(index, id)

            result = iter(pipe.execute())
            remove = [id for id, ent, ind in zip(ids, result, result) if ind and not ent]
            if remove:
                _clean_index_lua(conn, [model.__name__], remove)

            yield min(i+block_size, max_id-1), max_id

    yield max_id, max_id


def show_progress(job):
    '''
    This utility function will print the progress of a passed iterator job as
    started by ``refresh_indices()`` and ``clean_old_index()``.

    Usage example::

        class RomTest(Model):
            pass

        for i in xrange(1000):
            RomTest().save()

        util.show_progress(util.clean_old_index(RomTest))
    '''
    start = time.time()
    last_print = 0
    last_line = 0
    for prog, total in chain(job, [(1, 1)]):
        # Only print a line when we start, finish, or every .1 seconds
        if (time.time() - last_print) > .1 or prog >= total:
            delta = (time.time() - start) or .0001
            line = "%.1f%% complete, %.1f seconds elapsed, %.1f seconds remaining"%(
                100. * prog / (total or 1), delta, total * delta / (prog or 1) - delta)
            length = len(line)
            # pad the line out with spaces just in case our line got shorter
            line += max(last_line - length, 0) * ' '
            print(line, end="\r")
            last_line = length
            last_print = time.time()
    print()

def _script_load(script):
    '''
    Borrowed/modified from my book, Redis in Action:
    https://github.com/josiahcarlson/redis-in-action/blob/master/python/ch11_listing_source.py

    Used for Lua scripting support when writing against Redis 2.6+ to allow
    for multiple unique columns per model.
    '''
    script = script.encode('utf-8') if isinstance(script, six.text_type) else script
    sha = [None, sha1(script).hexdigest()]
    def call(conn, keys=[], args=[], force_eval=False):
        keys = tuple(keys)
        args = tuple(args)
        if not force_eval:
            if not sha[0]:
                try:
                    # executing the script implicitly loads it
                    return conn.execute_command(
                        'EVAL', script, len(keys), *(keys + args))
                finally:
                    # thread safe by re-using the GIL ;)
                    del sha[:-1]

            try:
                return conn.execute_command(
                    "EVALSHA", sha[0], len(keys), *(keys+args))

            except redis.exceptions.ResponseError as msg:
                if not msg.args[0].startswith("NOSCRIPT"):
                    raise

        return conn.execute_command(
            "EVAL", script, len(keys), *(keys+args))

    return call

_scan_index_lua = _script_load('''
local page = redis.call('HSCAN', KEYS[1] .. ':', ARGV[1], 'COUNT', ARGV[2] or 100)
local clear = {}
for i=1, #page[2], 2 do
    if redis.call('EXISTS', KEYS[1] .. page[2][i]) == 0 then
        table.insert(clear, page[2][i])
    end
end

return {page[1], clear}
''')

_clean_index_lua = _script_load('''
-- remove old index data
local namespace = KEYS[1]
local cleaned = 0
for _, id in ipairs(ARGV) do
    local idata = redis.call('HGET', namespace .. '::', id)
    if idata then
        cleaned = cleaned + 1
        idata = {0}(idata)
        if #idata == 2 then
            idata[3] = {{}}
            idata[4] = {{}}
        end
        for i, key in ipairs(idata[1]) do
            redis.call('SREM', string.format('%s:%s:idx', namespace, key), id)
        end
        for i, key in ipairs(idata[2]) do
            redis.call('ZREM', string.format('%s:%s:idx', namespace, key), id)
        end
        for i, data in ipairs(idata[3]) do
            local key = string.format('%s:%s:pre', namespace, data[1])
            local mem = string.format('%s\0%s', data[2], id)
            redis.call('ZREM', key, mem)
        end
        for i, data in ipairs(idata[4]) do
            local key = string.format('%s:%s:suf', namespace, data[1])
            local mem = string.format('%s\0%s', data[2], id)
            redis.call('ZREM', key, mem)
        end
        redis.call('HDEL', namespace .. '::', id)
    end
end
return cleaned
'''.format(_lua_decoder))
