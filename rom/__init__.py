'''
Rom - the Redis object mapper for Python

Copyright 2013-2014 Josiah Carlson

Released under the LGPL license version 2.1 and version 3 (you can choose
which you'd like to be bound under).

Documentation
=============

Updated documentation can be found: http://pythonhosted.org/rom/

What
====

Rom is a package whose purpose is to offer active-record style data modeling
within Redis from Python, similar to the semantics of Django ORM, SQLAlchemy +
Elixir, Google's Appengine datastore, and others.

Why
===

I was building a personal project, wanted to use Redis to store some of my
data, but didn't want to hack it poorly. I looked at the existing Redis object
mappers available in Python, but didn't like the features and functionality
offered.

What is available
=================

Data types:

* Strings (2.x: str/unicode, 3.3+: str), ints, floats, decimals, booleans
* datetime.datetime, datetime.date, datetime.time
* Json columns (for nested structures)
* OneToMany and ManyToOne columns (for model references)
* Non-rom ForeignModel reference support

Indexes:

* Numeric range fetches, searches, and ordering
* Full-word text search (find me entries with col X having words A and B)
* Prefix matching (can be used for prefix-based autocomplete)
* Suffix matching (can be used for suffix-based autocomplete)
* Pattern matching on string-based columns

Other features:

* Per-thread entity cache (to minimize round-trips, easy saving of all
  entities)
* The ability to cache query results and get the key for any other use (see:
  ``Query.cached_result()``)

Getting started
===============

1. Make sure you have Python 2.6, 2.7, or 3.3+ installed
2. Make sure that you have Andy McCurdy's Redis library installed:
   https://github.com/andymccurdy/redis-py/ or
   https://pypi.python.org/pypi/redis
3. Make sure that you have the Python 2 and 3 compatibility library, 'six'
   installed: https://pypi.python.org/pypi/six
4. (optional) Make sure that you have the hiredis library installed for Python
5. Make sure that you have a Redis server installed and available remotely
6. Update the Redis connection settings for ``rom`` via
   ``rom.util.set_connection_settings()`` (other connection update options,
   including per-model connections, can be read about in the ``rom.util``
   documentation)::

    import redis
    from rom import util

    util.set_connection_settings(host='myhost', db=7)

.. warning:: If you forget to update the connection function, rom will attempt
 to connect to localhost:6379 .

7. Create a model::

    import rom

    # All models to be handled by rom must derived from rom.Model
    class User(rom.Model):
        email = rom.String(required=True, unique=True, suffix=True)
        salt = rom.String()
        hash = rom.String()
        created_at = rom.Float(default=time.time)

8. Create an instance of the model and save it::

    PASSES = 32768
    def gen_hash(password, salt=None):
        salt = salt or os.urandom(16)
        comp = salt + password
        out = sha256(comp).digest()
        for i in xrange(PASSES-1):
            out = sha256(out + comp).digest()
        return salt, out

    user = User(email='user@host.com')
    user.salt, user.hash = gen_hash(password)
    user.save()
    # session.commit() or session.flush() works too

9. Load and use the object later::

    user = User.get_by(email='user@host.com')
    at_gmail = User.query.endswith(email='@gmail.com').all()

Lua support
===========

From version 0.25.0 and on, rom assumes that you are using Redis version 2.6
or later, which supports server-side Lua scripting. This allows for the
support of multiple unique columns without potentially nasty race conditions
and retries. This also allows for the support of prefix, suffix, and pattern
matching on certain column types.

If you are using a version of Redis prior to 2.6, you should upgrade Redis. If
you are unable or unwilling to upgrade Redis, but you still wish to use rom,
you should call ``rom._disable_lua_writes()``, which will prevent you from
using features that require Lua scripting support.
'''

from datetime import datetime, date, time as dtime
from decimal import Decimal as _Decimal
import json

import redis
import six

from .columns import (Column, Integer, Boolean, Float, Decimal, DateTime,
    Date, Time, Text, Json, PrimaryKey, ManyToOne, ForeignModel, OneToMany,
    MODELS)
from .exceptions import (ORMError, UniqueKeyViolation, InvalidOperation,
    QueryError, ColumnError, MissingColumn, InvalidColumnValue)
from .index import GeneralIndex, Pattern, Prefix, Suffix
from .util import (ClassProperty, _connect, session, dt2ts, t2ts,
    _prefix_score, _script_load)

VERSION = '0.26.4'

COLUMN_TYPES = [Column, Integer, Boolean, Float, Decimal, DateTime, Date,
Time, Text, Json, PrimaryKey, ManyToOne, ForeignModel, OneToMany]

NUMERIC_TYPES = six.integer_types + (float, _Decimal, datetime, date, dtime)

MissingColumn, InvalidOperation # silence pyflakes

USE_LUA = True
def _enable_lua_writes():
    from . import columns
    global USE_LUA
    columns.USE_LUA = USE_LUA = True

def _disable_lua_writes():
    from . import columns
    global USE_LUA
    columns.USE_LUA = USE_LUA = False

__all__ = '''
    Model Column Integer Float Decimal Text Json PrimaryKey ManyToOne
    ForeignModel OneToMany Query session Boolean DateTime Date Time'''.split()

if six.PY2:
    from .columns import String
    COLUMN_TYPES.append(String)
    __all__.append('String')

class _ModelMetaclass(type):
    def __new__(cls, name, bases, dict):
        if name in MODELS:
            raise ORMError("Cannot have two models with the same name %s"%name)
        dict['_required'] = required = set()
        dict['_index'] = index = set()
        dict['_unique'] = unique = set()
        dict['_prefix'] = prefix = set()
        dict['_suffix'] = suffix = set()

        dict['_columns'] = columns = {}
        pkey = None

        # load all columns from any base classes to allow for validation
        odict = {}
        for ocls in reversed(bases):
            if hasattr(ocls, '_columns'):
                odict.update(ocls._columns)
        odict.update(dict)
        dict = odict

        if not any(isinstance(col, PrimaryKey) for col in dict.values()):
            if 'id' in dict:
                raise ColumnError("Cannot have non-primary key named 'id'")
            dict['id'] = PrimaryKey()

        # validate all of our columns to ensure that they fulfill our
        # expectations
        for attr, col in dict.items():
            if isinstance(col, Column):
                columns[attr] = col
                if col._required:
                    required.add(attr)
                if col._index:
                    index.add(attr)
                if col._prefix:
                    if not USE_LUA:
                        raise ColumnError("Lua scripting must be enabled to support prefix indexes (%s.%s)"%(name, attr))
                    prefix.add(attr)
                if col._suffix:
                    if not USE_LUA:
                        raise ColumnError("Lua scripting must be enabled to support suffix indexes (%s.%s)"%(name, attr))
                    suffix.add(attr)
                if col._unique:
                    # We only allow one for performance when USE_LUA is False
                    if unique and not USE_LUA:
                        raise ColumnError(
                            "Only one unique column allowed, you have: %s %s"%(
                            attr, unique)
                        )
                    unique.add(attr)
            if isinstance(col, PrimaryKey):
                if pkey:
                    raise ColumnError("Only one primary key column allowed, you have: %s %s"%(
                        pkey, attr)
                    )
                pkey = attr

        dict['_pkey'] = pkey
        dict['_gindex'] = GeneralIndex(name)

        MODELS[name] = model = type.__new__(cls, name, bases, dict)
        return model

class Model(six.with_metaclass(_ModelMetaclass, object)):
    '''
    This is the base class for all models. You subclass from this base Model
    in order to create a model with columns. As an example::

        class User(Model):
            email_address = String(required=True, unique=True)
            salt = String(default='')
            hash = String(default='')
            created_at = Float(default=time.time, index=True)

    Which can then be used like::

        user = User(email_addrss='user@domain.com')
        user.save() # session.commit() or session.flush() works too
        user = User.get_by(email_address='user@domain.com')
        user = User.get(5)
        users = User.get([2, 6, 1, 7])

    To perform arbitrary queries on entities involving the indices that you
    defined (by passing ``index=True`` on column creation), you access the
    ``.query`` class property on the model::

        query = User.query
        query = query.filter(created_at=(time.time()-86400, time.time()))
        users = query.execute()

    .. note: You can perform single or chained queries against any/all columns
      that were defined with ``index=True``.

    '''
    def __init__(self, **kwargs):
        self._new = not kwargs.pop('_loading', False)
        model = self.__class__.__name__
        self._data = {}
        self._last = {}
        self._modified = False
        self._deleted = False
        self._init = False
        for attr in self._columns:
            cval = kwargs.get(attr, None)
            data = (model, attr, cval, not self._new)
            if self._new and attr == self._pkey and cval:
                raise InvalidColumnValue("Cannot pass primary key on object creation")
            setattr(self, attr, data)
            if cval != None:
                if not isinstance(cval, six.string_types):
                    cval = self._columns[attr]._to_redis(cval)
                self._last[attr] = cval
        self._init = True
        session.add(self)

    def refresh(self, force=False):
        if self._deleted:
            return
        if self._modified and not force:
            raise InvalidOperation("Cannot refresh a modified entity without passing force=True to override modified data")
        if self._new:
            raise InvalidOperation("Cannot refresh a new entity")

        conn = _connect(self)
        data = conn.hgetall(self._pk)
        if six.PY3:
            data = dict((k.decode(), v.decode()) for k, v in data.items())
        self.__init__(_loading=True, **data)

    @property
    def _pk(self):
        return '%s:%s'%(self.__class__.__name__, getattr(self, self._pkey))

    @classmethod
    def _apply_changes(cls, old, new, full=False, delete=False):
        use_lua = USE_LUA
        conn = _connect(cls)
        pk = old.get(cls._pkey) or new.get(cls._pkey)
        if not pk:
            raise ColumnError("Missing primary key value")

        model = cls.__name__
        key = '%s:%s'%(model, pk)
        pipe = conn.pipeline(True)

        columns = cls._columns
        while 1:
            changes = 0
            keys = set()
            scores = {}
            data = {}
            unique = {}
            deleted = []
            udeleted = {}
            prefix = []
            suffix = []

            # check for unique keys
            if len(cls._unique) > 1 and not use_lua:
                raise ColumnError(
                    "Only one unique column allowed, you have: %s"%(unique,))

            if not use_lua:
                for col in cls._unique:
                    ouval = old.get(col)
                    nuval = new.get(col)
                    nuvale = columns[col]._to_redis(nuval) if nuval is not None else None

                    if six.PY2 and not isinstance(ouval, str):
                        ouval = columns[col]._to_redis(ouval)
                    if not (nuval and (ouval != nuvale or full)):
                        # no changes to unique columns
                        continue

                    ikey = "%s:%s:uidx"%(model, col)
                    pipe.watch(ikey)
                    ival = pipe.hget(ikey, nuvale)
                    ival = ival if isinstance(ival, str) or ival is None else ival.decode()
                    if not ival or ival == str(pk):
                        pipe.multi()
                    else:
                        pipe.unwatch()
                        raise UniqueKeyViolation("Value %r for %s is not distinct"%(nuval, ikey))

            # update individual columns
            for attr in cls._columns:
                ikey = None
                if attr in cls._unique:
                    ikey = "%s:%s:uidx"%(model, attr)

                ca = columns[attr]
                roval = old.get(attr)
                oval = ca._from_redis(roval) if roval is not None else None

                nval = new.get(attr)
                rnval = ca._to_redis(nval) if nval is not None else None

                # Add/update standard index
                if ca._keygen and not delete and nval is not None and (ca._index or ca._prefix or ca._suffix):
                    generated = ca._keygen(nval)
                    if isinstance(generated, (list, tuple, set)):
                        if ca._index:
                            for k in generated:
                                keys.add('%s:%s'%(attr, k))
                        if ca._prefix:
                            for k in generated:
                                prefix.append([attr, k])
                        if ca._suffix:
                            for k in generated:
                                if six.PY2 and isinstance(k, str) and isinstance(cls._columns[attr], Text):
                                    try:
                                        suffix.append([attr, k.decode('utf-8')[::-1].encode('utf-8')])
                                    except UnicodeDecodeError:
                                        suffix.append([attr, k[::-1]])
                                else:
                                    suffix.append([attr, k[::-1]])
                    elif isinstance(generated, dict):
                        for k, v in generated.items():
                            if not k:
                                scores[attr] = v
                            else:
                                scores['%s:%s'%(attr, k)] = v
                    elif not generated:
                        pass
                    else:
                        raise ColumnError("Don't know how to turn %r into a sequence of keys"%(generated,))

                if nval == oval and not full:
                    continue

                changes += 1

                # Delete removed columns
                if nval is None and oval is not None:
                    if use_lua:
                        deleted.append(attr)
                        if ikey:
                            udeleted[attr] = roval
                    else:
                        pipe.hdel(key, attr)
                        if ikey:
                            pipe.hdel(ikey, roval)
                        # Index removal will occur by virtue of no index entry
                        # for this column.
                    continue

                # Add/update column value
                if nval is not None:
                    data[attr] = rnval

                # Add/update unique index
                if ikey:
                    if six.PY2 and not isinstance(roval, str):
                        roval = columns[attr]._to_redis(roval)
                    if use_lua:
                        if oval is not None and roval != rnval:
                            udeleted[attr] = oval
                        unique[attr] = rnval
                    else:
                        if oval is not None:
                            pipe.hdel(ikey, roval)
                        pipe.hset(ikey, rnval, pk)

            id_only = str(pk)
            if use_lua:
                redis_writer_lua(conn, model, id_only, unique, udeleted,
                    deleted, data, list(keys), scores, prefix, suffix, delete)
                return changes
            elif delete:
                changes += 1
                cls._gindex._unindex(conn, pipe, id_only)
                pipe.delete(key)
            else:
                if data:
                    pipe.hmset(key, data)
                cls._gindex.index(conn, id_only, keys, scores, prefix, suffix, pipe=pipe)

            try:
                pipe.execute()
            except redis.exceptions.WatchError:
                continue
            else:
                return changes

    def to_dict(self):
        '''
        Returns a copy of all data assigned to columns in this entity. Useful
        for returning items to JSON-enabled APIs. If you want to copy an
        entity, you should look at the ``.copy()`` method.
        '''
        return dict(self._data)

    def save(self, full=False):
        '''
        Saves the current entity to Redis. Will only save changed data by
        default, but you can force a full save by passing ``full=True``.
        '''
        new = self.to_dict()
        ret = self._apply_changes(self._last, new, full or self._new)
        self._new = False
        self._last = new
        self._modified = False
        self._deleted = False
        return ret

    def delete(self):
        '''
        Deletes the entity immediately.
        '''
        session.forget(self)
        self._apply_changes(self._last, {}, delete=True)
        self._modified = True
        self._deleted = True

    def copy(self):
        '''
        Creates a shallow copy of the given entity (any entities that can be
        retrieved from a OneToMany relationship will not be copied).
        '''
        x = self.to_dict()
        x.pop(self._pkey)
        return self.__class__(**x)

    @classmethod
    def get(cls, ids):
        '''
        Will fetch one or more entities of this type from the session or
        Redis.

        Used like::

            MyModel.get(5)
            MyModel.get([1, 6, 2, 4])

        Passing a list or a tuple will return multiple entities, in the same
        order that the ids were passed.
        '''
        conn = _connect(cls)
        # prepare the ids
        single = not isinstance(ids, (list, tuple))
        if single:
            ids = [ids]
        pks = ['%s:%s'%(cls.__name__, id) for id in map(int, ids)]
        # get from the session, if possible
        out = list(map(session.get, pks))
        # if we couldn't get an instance from the session, load from Redis
        if None in out:
            pipe = conn.pipeline(True)
            idxs = []
            # Fetch missing data
            for i, data in enumerate(out):
                if data is None:
                    idxs.append(i)
                    pipe.hgetall(pks[i])
            # Update output list
            for i, data in zip(idxs, pipe.execute()):
                if data:
                    if six.PY3:
                        data = dict((k.decode(), v.decode()) for k, v in data.items())
                    out[i] = cls(_loading=True, **data)
            # Get rid of missing models
            out = [x for x in out if x]
        if single:
            return out[0] if out else None
        return out

    @classmethod
    def get_by(cls, **kwargs):
        '''
        This method offers a simple query method for fetching entities of this
        type via attribute numeric ranges (such columns must be ``indexed``),
        or via ``unique`` columns.

        Some examples::

            user = User.get_by(email_address='user@domain.com')
            # gets up to 25 users created in the last 24 hours
            users = User.get_by(
                created_at=(time.time()-86400, time.time()),
                _limit=(0, 25))

        If you would like to make queries against multiple columns or with
        multiple criteria, look into the Model.query class property.
        '''
        conn = _connect(cls)
        model = cls.__name__
        # handle limits and query requirements
        _limit = kwargs.pop('_limit', ())
        if _limit and len(_limit) != 2:
            raise QueryError("Limit must include both 'offset' and 'count' parameters")
        elif _limit and not all(isinstance(x, six.integer_types) for x in _limit):
            raise QueryError("Limit arguments must both be integers")
        if len(kwargs) != 1:
            raise QueryError("We can only fetch object(s) by exactly one attribute, you provided %s"%(len(kwargs),))

        for attr, value in kwargs.items():
            plain_attr = attr.partition(':')[0]
            if isinstance(value, tuple) and len(value) != 2:
                raise QueryError("Range queries must include exactly two endpoints")

            # handle unique index lookups
            if attr in cls._unique:
                if isinstance(value, tuple):
                    raise QueryError("Cannot query a unique index with a range of values")
                single = not isinstance(value, list)
                if single:
                    value = [value]
                qvalues = list(map(cls._columns[attr]._to_redis, value))
                ids = [x for x in conn.hmget('%s:%s:uidx'%(model, attr), qvalues) if x]
                if not ids:
                    return None if single else []
                return cls.get(ids[0] if single else ids)

            if plain_attr not in cls._index:
                raise QueryError("Cannot query on a column without an index")

            if isinstance(value, NUMERIC_TYPES) and not isinstance(value, bool):
                value = (value, value)

            if isinstance(value, tuple):
                # this is a numeric range query, we'll just pull it directly
                args = list(value)
                for i, a in enumerate(args):
                    # Handle the ranges where None is -inf on the left and inf
                    # on the right when used in the context of a range tuple.
                    args[i] = ('-inf', 'inf')[i] if a is None else cls._columns[attr]._to_redis(a)
                if _limit:
                    args.extend(_limit)
                ids = conn.zrangebyscore('%s:%s:idx'%(model, attr), *args)
                if not ids:
                    return []
                return cls.get(ids)

            # defer other index lookups to the query object
            query = cls.query.filter(**{attr: value})
            if _limit:
                query = query.limit(*_limit)
            return query.all()

    @ClassProperty
    def query(cls):
        '''
        Returns a ``Query`` object that refers to this model to handle
        subsequent filtering.
        '''
        return Query(cls)

_redis_writer_lua = _script_load('''
local namespace = ARGV[1]
local id = ARGV[2]
local is_delete = cjson.decode(ARGV[11])

-- check and update unique column constraints
for i, write in ipairs({false, true}) do
    for col, value in pairs(cjson.decode(ARGV[3])) do
        local key = string.format('%s:%s:uidx', namespace, col)
        if write then
            redis.call('HSET', key, value, id)
        else
            local known = redis.call('HGET', key, value)
            if known ~= id and known ~= false then
                return col
            end
        end
    end
end

-- remove deleted unique constraints
for col, value in pairs(cjson.decode(ARGV[4])) do
    local key = string.format('%s:%s:uidx', namespace, col)
    local known = redis.call('HGET', key, value)
    if known == id then
        redis.call('HDEL', key, value)
    end
end

-- remove deleted columns
local deleted = cjson.decode(ARGV[5])
if #deleted > 0 then
    redis.call('HDEL', string.format('%s:%s', namespace, id), unpack(deleted))
end

-- update changed/added columns
local data = cjson.decode(ARGV[6])
if #data > 0 then
    redis.call('HMSET', string.format('%s:%s', namespace, id), unpack(data))
end

-- remove old index data
local idata = redis.call('HGET', namespace .. '::', id)
if idata then
    idata = cjson.decode(idata)
    if #idata == 2 then
        idata[3] = {}
        idata[4] = {}
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
end

if is_delete then
    redis.call('DEL', string.format('%s:%s', namespace, id))
end

-- add new key index data
local nkeys = cjson.decode(ARGV[7])
for i, key in ipairs(nkeys) do
    redis.call('SADD', string.format('%s:%s:idx', namespace, key), id)
end

-- add new scored index data
local nscored = {}
for key, score in pairs(cjson.decode(ARGV[8])) do
    redis.call('ZADD', string.format('%s:%s:idx', namespace, key), score, id)
    nscored[#nscored + 1] = key
end

-- add new prefix data
local nprefix = {}
for i, data in ipairs(cjson.decode(ARGV[9])) do
    local key = string.format('%s:%s:pre', namespace, data[1])
    local mem = string.format("%s\0%s", data[2], id)
    redis.call('ZADD', key, data[3], mem)
    nprefix[#nprefix + 1] = {data[1], data[2]}
end

-- add new suffix data
local nsuffix = {}
for i, data in ipairs(cjson.decode(ARGV[10])) do
    local key = string.format('%s:%s:suf', namespace, data[1])
    local mem = string.format("%s\0%s", data[2], id)
    redis.call('ZADD', key, data[3], mem)
    nsuffix[#nsuffix + 1] = {data[1], data[2]}
end

-- update known index data
local encoded = cjson.encode({nkeys, nscored, nprefix, nsuffix})
redis.call('HSET', namespace .. '::', id, encoded)
return #nkeys + #nscored + #nprefix + #nsuffix
''')

def redis_writer_lua(conn, namespace, id, unique, udelete, delete, data, keys,
                     scored, prefix, suffix, is_delete):
    ldata = []
    for pair in data.items():
        ldata.extend(pair)

    for item in prefix:
        item.append(_prefix_score(item[-1]))
    for item in suffix:
        item.append(_prefix_score(item[-1]))

    result = _redis_writer_lua(conn, [], [namespace, id] + list(map(json.dumps, [
        unique, udelete, delete, ldata, keys, scored, prefix, suffix, is_delete])))
    if isinstance(result, six.binary_type):
        result = result.decode()
        raise UniqueKeyViolation("Value %r for %s:%s:uidx not distinct"%(unique[result], namespace, result))

class Query(object):
    '''
    This is a query object. It behaves a lot like other query objects. Every
    operation performed on Query objects returns a new Query object. The old
    Query object *does not* have any updated filters.
    '''
    __slots__ = '_model _filters _order_by _limit'.split()
    def __init__(self, model, filters=(), order_by=None, limit=None):
        self._model = model
        self._filters = filters
        self._order_by = order_by
        self._limit = limit

    def replace(self, **kwargs):
        '''
        Copy the Query object, optionally replacing the filters, order_by, or
        limit information on the copy.
        '''
        data = {
            'model': self._model,
            'filters': self._filters,
            'order_by': self._order_by,
            'limit': self._limit,
        }
        data.update(**kwargs)
        return Query(**data)

    def filter(self, **kwargs):
        '''
        Filters should be of the form::

            # for numeric ranges, use None for open-ended ranges
            attribute=(min, max)

            # you can also query for equality by passing a single number
            attribute=value

            # for string searches, passing a plain string will require that
            # string to be in the index as a literal
            attribute=string

            # to perform an 'or' query on strings, you can pass a list of
            # strings
            attribute=[string1, string2]

        As an example, the following will return entities that have both
        ``hello`` and ``world`` in the ``String`` column ``scol`` and has a
        ``Numeric`` column ``ncol`` with value between 2 and 10 (including the
        endpoints)::

            results = MyModel.query \\
                .filter(scol='hello') \\
                .filter(scol='world') \\
                .filter(ncol=(2, 10)) \\
                .all()

        If you only want to match a single value as part of your range query,
        you can pass an integer, float, or Decimal object by itself, similar
        to the ``Model.get_by()`` method::

            results = MyModel.query \\
                .filter(ncol=5) \\
                .execute()

        '''
        cur_filters = list(self._filters)
        for attr, value in kwargs.items():
            if isinstance(value, bool):
                value = str(bool(value))

            if isinstance(value, NUMERIC_TYPES):
                # for simple numeric equiality filters
                value = (value, value)

            if isinstance(value, six.string_types):
                cur_filters.append('%s:%s'%(attr, value))

            elif isinstance(value, tuple):
                if len(value) != 2:
                    raise QueryError("Numeric ranges require 2 endpoints, you provided %s with %r"%(len(value), value))

                tt = []
                for v in value:
                    if isinstance(v, date):
                        v = dt2ts(v)

                    if isinstance(v, dtime):
                        v = t2ts(v)
                    tt.append(v)

                value = tt

                cur_filters.append((attr, value[0], value[1]))

            elif isinstance(value, list) and value:
                cur_filters.append(['%s:%s'%(attr, v) for v in value])

            else:
                raise QueryError("Sorry, we don't know how to filter %r by %r"%(attr, value))
        return self.replace(filters=tuple(cur_filters))

    def startswith(self, **kwargs):
        '''
        When provided with keyword arguments of the form ``col=prefix``, this
        will limit the entities returned to those that have a word with the
        provided prefix in the specified column(s). This requires that the
        ``prefix=True`` option was provided during column definition.::

            User.query.startswith(email='user@').execute()

        '''
        new = []
        for k, v in kwargs.items():
            new.append(Prefix(k, v))
        return self.replace(filters=self._filters+tuple(new))

    def endswith(self, **kwargs):
        '''
        When provided with keyword arguments of the form ``col=suffix``, this
        will limit the entities returned to those that have a word with the
        provided suffix in the specified column(s). This requires that the
        ``suffix=True`` option was provided during column definition.::

            User.query.endswith(email='@gmail.com').execute()

        '''
        new = []
        for k, v in kwargs.items():
            new.append(Suffix(k, v[::-1]))
        return self.replace(filters=self._filters+tuple(new))

    def like(self, **kwargs):
        '''
        When provided with keyword arguments of the form ``col=pattern``, this
        will limit the entities returned to those that include the provided
        pattern. Note that 'like' queries require that the ``prefix=True``
        option must have been provided as part of the column definition.

        Patterns allow for 4 wildcard characters, whose semantics are as
        follows:

            * *?* - will match 0 or 1 of any character
            * *\** - will match 0 or more of any character
            * *+* - will match 1 or more of any character
            * *!* - will match exactly 1 of any character

        As an example, imagine that you have enabled the required prefix
        matching on your ``User.email`` column. And lets say that you want to
        find everyone with an email address that contains the name 'frank'
        before the ``@`` sign. You can use either of the following patterns
        to discover those users.

            * *\*frank\*@*
            * *\*frank\*@

        .. note: Like queries implicitly start at the beginning of strings
          checked, so if you want to match a pattern that doesn't start at
          the beginning of a string, you should prefix it with one of the
          wildcard characters (like ``*`` as we did with the 'frank' pattern).
        '''
        new = []
        for k, v in kwargs.items():
            new.append(Pattern(k, v))
        return self.replace(filters=self._filters+tuple(new))

    def order_by(self, column):
        '''
        When provided with a column name, will sort the results of your query::

            # returns all users, ordered by the created_at column in
            # descending order
            User.query.order_by('-created_at').execute()
        '''
        return self.replace(order_by=column)

    def limit(self, offset, count):
        '''
        Will limit the number of results returned from a query::

            # returns the most recent 25 users
            User.query.order_by('-created_at').limit(0, 25).execute()
        '''
        return self.replace(limit=(offset, count))

    def count(self):
        '''
        Will return the total count of the objects that match the specified
        filters. If no filters are provided, will return 0::

            # counts the number of users created in the last 24 hours
            User.query.filter(created_at=(time.time()-86400, time.time())).count()
        '''
        filters = self._filters
        if self._order_by:
            filters += (self._order_by.lstrip('-'),)
        if not filters:
            raise QueryError("You are missing filter or order criteria")
        return self._model._gindex.count(_connect(self._model), filters)

    def _search(self):
        if not (self._filters or self._order_by):
            raise QueryError("You are missing filter or order criteria")
        limit = () if not self._limit else self._limit
        return self._model._gindex.search(
            _connect(self._model), self._filters, self._order_by, *limit)

    def cached_result(self, timeout):
        '''
        This will execute the query, returning the key where a ZSET of your
        results will be stored for pagination, further operations, etc.

        The timeout must be a positive integer number of seconds for which to
        set the expiration time on the key (this is to ensure that any cached
        query results are eventually deleted, unless you make the explicit
        step to use the PERSIST command).

        .. note: Limit clauses are ignored and not passed.

        Usage::

            ukey = User.query.endswith(email='@gmail.com').cached_result(30)
            for i in xrange(0, conn.zcard(ukey), 100):
                # refresh the expiration
                conn.expire(ukey, 30)
                users = User.get(conn.zrange(ukey, i, i+99))
                ...
        '''
        if not (self._filters or self._order_by):
            raise QueryError("You are missing filter or order criteria")
        timeout = int(timeout)
        if timeout < 1:
            raise QueryError("You must specify a timeout >= 1, you gave %r"%timeout)
        return self._model._gindex.search(
            _connect(self._model), self._filters, self._order_by, timeout=timeout)

    def execute(self):
        '''
        Actually executes the query, returning any entities that match the
        filters, ordered by the specified ordering (if any), limited by any
        earlier limit calls.
        '''
        return self._model.get(self._search())

    def all(self):
        '''
        Alias for ``execute()``.
        '''
        return self.execute()

    def first(self):
        '''
        Returns only the first result from the query, if any.
        '''
        lim = [0, 1]
        if self._limit:
            lim[0] = self._limit[0]
        ids = self.limit(*lim)._search()
        if ids:
            return self._model.get(ids[0])
        return None
