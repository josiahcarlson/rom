
from functools import wraps
import string
import threading

import redis

from .exceptions import ORMError

__all__ = '''
    get_connection Session refresh_indices'''.split()

CONNECTION = redis.Redis()

def get_connection():
    '''
    Replace me with a function that takes no arguments in order to change the
    way I connect to Redis. Alternatively, replace the global ``CONNECTION``
    object in this module for similar results
    '''
    return CONNECTION

def connect(f):
    @wraps(f)
    def call(sc, *args, **kwargs):
        conn = kwargs.pop('conn', None) or get_connection()
        return f(sc, conn, *args, **kwargs)
    return call

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

def _model(obj):
    if type(obj) is type:
        return obj.__name__
    return obj.__class__.__name__

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
    return {'': repr(val) if isinstance(val, float) else str(val)}

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
    return {'': val._data[val._pkey]}

def _to_score(v, s=False):
    v = repr(v) if isinstance(v, float) else str(v)
    if s:
        if v[:1] != '(':
            return '(' + v
    return v.lstrip('(')

class Session(threading.local):
    '''
    This is a very dumb session. All it tries to do is to keep a cache of
    loaded entities, offering the ability to call ``.save()`` on modified (or
    all) entities with ``.flush()`` or ``.commit()``.

    This is exposed via the ``session`` global variable, which is available
    when you ``import rom`` as ``rom.session``.
    '''
    def add(self, obj):
        '''
        Adds an entity to the session.
        '''
        try:
            self.known
        except AttributeError:
            self.known = {}
        self.known[obj._pk] = obj

    def forget(self, obj):
        '''
        Forgets about an entity (automatically called when an entity is
        deleted). Call this to ensure that an entity that you've modified is
        not automatically saved on ``session.commit()`` .
        '''
        try:
            self.known
        except AttributeError:
            self.known = {}
        self.known.pop(obj._pk, None)

    def get(self, pk):
        '''
        Fetches an entity from the session based on primary key.
        '''
        try:
            return self.known.get(pk)
        except AttributeError:
            self.known = {}

    def rollback(self):
        '''
        Forget about all entities in the session (``.commit()`` will do
        nothing).
        '''
        self.known = {}

    def flush(self, full=False, all=False):
        '''
        Call ``.save()`` on all modified entities in the session. Use when you
        want to flush changes to Redis, but don't want to lose your local
        session cache.

        See the ``.commit()`` method for arguments and their meanings.
        '''
        try:
            self.known
        except AttributeError:
            self.known = {}
        changes = 0
        for value in self.known.values():
            if all or value._modified:
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
                if all or o._modified:
                    changes += o.save(full)
            else:
                raise ORMError(
                    "Cannot save an object that is not an instance of a Model (you provided %r)"%(
                        o,))
        return changes

session = Session()

@connect
def refresh_indices(model, conn, block_size=100):
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
    max_id = int(conn.get('%s:%s:'%(_model(model), model._pkey)) or '0')
    block_size = max(block_size, 10)
    for i in xrange(1, max_id+1, block_size):
        # fetches entities, keeping a record in the session
        models = model.get(range(i, i+block_size))
        models # for pyflakes
        # re-save un-modified data, resulting in index-only updates
        session.commit(all=True)
        yield min(i+block_size, max_id), max_id
