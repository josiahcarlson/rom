

'''
Rom - the Redis object mapper for Python

Copyright 2013-2016 Josiah Carlson

Released under the LGPL license version 2.1 and version 3 (you can choose
which you'd like to be bound under).
'''

from collections import defaultdict
import json
import warnings

from redis import client
import six

from .columns import (Column, Text, PrimaryKey, ManyToOne, OneToOne, OneToMany,
    MODELS, MODELS_REFERENCED, _on_delete, SKIP_ON_DELETE)
from .exceptions import (ORMError, UniqueKeyViolation, InvalidOperation,
    QueryError, ColumnError, InvalidColumnValue, DataRaceError,
    EntityDeletedError)
from .index import GeneralIndex, GeoIndex
from .query import Query, NUMERIC_TYPES
from .util import (ClassProperty, _connect, session,
    _prefix_score, _script_load, _encode_unique_constraint,
    STRING_SORT_KEYGENS)

_skip = None
_skip = set(globals()) - set(['__doc__'])

_STRING_SORT_KEYGENS = [ss.__name__ for ss in STRING_SORT_KEYGENS]

class _ModelMetaclass(type):
    def __new__(cls, name, bases, dict):
        ns = dict.pop('_namespace', None)
        if ns and not isinstance(ns, six.string_types):
            raise ORMError("The _namespace attribute must be a string, not %s"%type(ns))
        dict['_namespace'] = ns or name
        if name in MODELS or dict['_namespace'] in MODELS:
            raise ORMError("Cannot have two models with the same name (%s) or namespace (%s)"%(name, dict['_namespace']))
        dict['_required'] = required = set()
        dict['_index'] = index = set()
        dict['_unique'] = unique = set()
        dict['_cunique'] = cunique = set()
        dict['_prefix'] = prefix = set()
        dict['_suffix'] = suffix = set()
        dict['_geo'] = geo = {}

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
                raise ColumnError("Cannot have non-primary key named 'id' when no explicit PrimaryKey() is defined")
            dict['id'] = PrimaryKey()

        composite_unique = []
        many_to_one = defaultdict(list)

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
                    prefix.add(attr)
                if col._suffix:
                    suffix.add(attr)
                if col._unique:
                    unique.add(attr)

            if isinstance(col, PrimaryKey):
                if pkey:
                    raise ColumnError("Only one primary key column allowed, you have: %s %s"%(
                        pkey, attr)
                    )
                pkey = attr

            if isinstance(col, OneToMany) and not col._column and col._ftable in MODELS:
                # Check to make sure that the foreign ManyToOne/OneToMany table
                # doesn't have multiple references to this table to require an
                # explicit foreign column.
                refs = []
                for _a, _c in MODELS[col._ftable]._columns.items():
                    if isinstance(_c, (ManyToOne, OneToOne)) and _c._ftable == name:
                        refs.append(_a)
                if len(refs) > 1:
                    raise ColumnError("Missing required column argument to OneToMany definition on column %s"%(attr,))

            if isinstance(col, (ManyToOne, OneToOne)):
                many_to_one[col._ftable].append((attr, col))
                MODELS_REFERENCED.setdefault(col._ftable, []).append((dict['_namespace'], attr, col._on_delete))

            if attr == 'unique_together':
                composite_unique = col

            if attr == 'geo_index':
                if not isinstance(col, list) or not all(isinstance(v, GeoIndex) for v in col):
                    raise ORMError("geo_index attribute must be a list of Geoindex() definitions if present")
                for g in col:
                    if g.name in geo:
                        raise ColumnError("Geo index named %r already defined"%(g.name,))
                    geo[g.name] = g

        # verify reverse OneToMany attributes for these ManyToOne/OneToOne
        # attributes if created after referenced models
        for t, cols in many_to_one.items():
            if len(cols) == 1:
                continue
            if t not in MODELS:
                continue
            for _a, _c in MODELS[t]._columns.items():
                if isinstance(_c, OneToMany) and _c._ftable == name and not _c._column:
                    raise ColumnError("Foreign model OneToMany attribute %s.%s missing column argument"%(t, _a))

        # handle multi-column uniqueness constraints
        if composite_unique and isinstance(composite_unique[0], six.string_types):
            composite_unique = [composite_unique]

        seen = {}
        for comp in composite_unique:
            key = tuple(sorted(set(comp)))
            if len(key) == 1:
                raise ColumnError("Single-column unique constraint: %r should be defined via 'unique=True' on the %r column"%(
                    comp, key[0]))
            if key in seen:
                raise ColumnError("Multi-column unique constraint: %r not different than earlier constrant: %r"%(
                    comp, seen[key]))
            for col in key:
                if col not in columns:
                    raise ColumnError("Multi-column unique index %r references non-existant column %r"%(
                        comp, col))
            seen[key] = comp
            cunique.add(key)

        dict['_pkey'] = pkey
        dict['_gindex'] = GeneralIndex(dict['_namespace'])

        MODELS[dict['_namespace']] = MODELS[name] = model = type.__new__(cls, name, bases, dict)
        return model

class AttrDict(dict):
    def __getattr__(self, attr):
        return self.get(attr)
    def __setattr__(self, attr, value):
        self[attr] = value
    def __delattr__(self, attr):
        self.pop(attr, None)

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

    .. note:: You can perform single or chained queries against any/all columns
      that were defined with ``index=True``.

    **Composite/multi-column unique constraints**

    As of version 0.28.0 and later, rom supports the ability for you to have a
    unique constraint involving multiple columns. Individual columns can be
    defined unique by passing the 'unique=True' specifier during column
    definition as always.

    The attribute ``unique_together`` defines those groups of columns that when
    taken together must be unique for ``.save()`` to complete successfully.
    This will work almost exactly the same as Django's ``unique_together``, and
    is comparable to SQLAlchemy's ``UniqueConstraint()``.

    Usage::

        class UniquePosition(Model):
            x = Integer()
            y = Integer()

            unique_together = [
                ('x', 'y'),
            ]

    .. note:: If one or more of the column values on an entity that is part of a
        unique constrant is None in Python, the unique constraint won't apply.
        This is the typical behavior of nulls in unique constraints inside both
        MySQL and Postgres.
    '''
    def __init__(self, **kwargs):
        self._new = not kwargs.pop('_loading', False)
        model = self._namespace
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

    def _before_insert(self):
        "Called before a new entity is saved to Redis"

    def _before_update(self):
        "Called before an entity is saved to Redis"

    def _before_delete(self):
        "Called before an entity is deleted"

    def _after_insert(self):
        "Called after a new entity has been saved to Redis"

    def _after_update(self):
        "Called after a previously-saved entity has been saved to Redis"

    def _after_delete(self):
        "Called after a previously-deleted entity has been deleted from Redis"

    @ClassProperty
    def _connection(cls):
        return _connect(cls)

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
        return '%s:%s'%(self._namespace, getattr(self, self._pkey))

    @classmethod
    def _apply_changes(cls, old, new, full=False, delete=False, is_new=False):
        conn = _connect(cls)
        pk = old.get(cls._pkey) or new.get(cls._pkey)
        if not pk:
            raise ColumnError("Missing primary key value")

        model = cls._namespace
        columns = cls._columns
        changes = 0
        keys = set()
        scores = {}
        data = {}
        unique = {}
        deleted = []
        udeleted = {}
        prefix = []
        suffix = []
        geo = []
        redis_data = {}

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
            if rnval is not None:
                redis_data[attr] = rnval

            # Add/update standard index
            if ca._keygen and not delete and nval is not None and (ca._index or ca._prefix or ca._suffix):
                generated = ca._keygen(attr, new)
                if not generated:
                    # No index entries, we'll clean out old ones as necessary
                    continue

                elif isinstance(generated, (list, tuple, set)):
                    if ca._index:
                        for k in generated:
                            keys.add('%s:%s'%(attr, k))

                    if ca._prefix:
                        for k in generated:
                            prefix.append([attr, k])

                    if ca._suffix:
                        for k in generated:
                            if six.PY2 and isinstance(k, str) and isinstance(ca, Text):
                                try:
                                    suffix.append([attr, k.decode('utf-8')[::-1].encode('utf-8')])
                                except UnicodeDecodeError:
                                    suffix.append([attr, k[::-1]])
                            else:
                                suffix.append([attr, k[::-1]])

                elif isinstance(generated, dict):
                    if ca._index:
                        for k, v in generated.items():
                            if not k:
                                scores[attr] = v
                            elif v in (None, ''):
                                # mixed index type support
                                keys.add('%s:%s'%(attr, k))
                            else:
                                scores['%s:%s'%(attr, k)] = v

                    if ca._prefix:
                        if ca._keygen.__name__ not in _STRING_SORT_KEYGENS:
                            warnings.warn("Prefix indexes are currently not enabled for non-standard keygen functions", stacklevel=2)
                        else:
                            prefix.append([attr, nval if ca._keygen.__name__ in ('SIMPLE', 'IDENTITY') else nval.lower()])

                    if ca._suffix:
                        if ca._keygen.__name__ not in _STRING_SORT_KEYGENS:
                            warnings.warn("Prefix indexes are currently not enabled for non-standard keygen functions", stacklevel=2)
                        else:
                            ex = (lambda x:x) if ca._keygen.__name__ in ('SIMPLE', 'IDENTITY') else (lambda x:x.lower())
                            if six.PY2 and isinstance(nval, str) and isinstance(ca, Text):
                                try:
                                    suffix.append([attr, ex(nval.decode('utf-8')[::-1]).encode('utf-8')])
                                except UnicodeDecodeError:
                                    suffix.append([attr, ex(nval[::-1])])
                            else:
                                suffix.append([attr, ex(nval[::-1])])

                else:
                    raise ColumnError("Don't know how to turn %r into a sequence of keys"%(generated,))

            if nval == oval and not full:
                continue

            changes += 1

            # Delete removed columns
            if nval is None and oval is not None:
                deleted.append(attr)
                if ikey:
                    udeleted[attr] = roval
                continue

            # Add/update column value
            if nval is not None:
                data[attr] = rnval

            # Add/update unique index
            if ikey:
                if six.PY2 and not isinstance(roval, str):
                    roval = columns[attr]._to_redis(roval)
                if oval is not None and roval != rnval:
                    udeleted[attr] = oval
                if rnval is not None:
                    unique[attr] = rnval

        # Add/update multi-column unique constraint
        for uniq in cls._cunique:
            attr = ':'.join(uniq)

            odata = [old.get(c) for c in uniq]
            ndata = [new.get(c) for c in uniq]
            ndata = [columns[c]._to_redis(nv) if nv is not None else None for c, nv in zip(uniq, ndata)]

            if odata != ndata and None not in odata:
                udeleted[attr] = _encode_unique_constraint(odata)

            if None not in ndata:
                unique[attr] = _encode_unique_constraint(ndata)

        for name in cls._geo:
            idx = cls._geo[name]
            val = idx.callback(AttrDict(new))
            if isinstance(val, dict):
                val = [val]
            for v in val:
                if 'lon' in v and 'lat' in v:
                    geo.append((name, v['lon'], v['lat']))
                else:
                    raise ORMError("Lon/Lat pair for geo index is not a dictionary of {'lon': ..., 'lat': ...}")

        id_only = str(pk)
        old_data = [] if is_new else ([(cls._pkey, str(pk))] + [(k, old.get(k)) for k in data if k in old])
        redis_writer_lua(conn, cls._pkey, model, id_only, unique, udeleted,
            deleted, data, list(keys), scores, prefix, suffix, geo, old_data,
            delete)

        return changes, redis_data

    def to_dict(self):
        '''
        Returns a copy of all data assigned to columns in this entity. Useful
        for returning items to JSON-enabled APIs. If you want to copy an
        entity, you should look at the ``.copy()`` method.
        '''
        return dict(self._data)

    def save(self, full=False, force=False):
        '''
        Saves the current entity to Redis. Will only save changed data by
        default, but you can force a full save by passing ``full=True``.

        If the underlying entity was deleted and you want to re-save the entity,
        you can pass ``force=True`` to force a full re-save of the entity.
        '''
        # handle the pre-commit hooks
        was_new = self._new
        if was_new:
            self._before_insert()
        else:
            self._before_update()

        new = self.to_dict()
        ret, data = self._apply_changes(
            self._last, new, full or self._new or force, is_new=self._new or force)
        self._last = data
        self._new = False
        self._modified = False
        self._deleted = False
        # handle the post-commit hooks
        if was_new:
            self._after_insert()
        else:
            self._after_update()
        return ret

    def delete(self, **kwargs):
        '''
        Deletes the entity immediately. Also performs any on_delete operations
        specified as part of column definitions.
        '''
        if kwargs.get('skip_on_delete_i_really_mean_it') is not SKIP_ON_DELETE:
            # handle the pre-commit hook
            self._before_delete()
            # handle any foreign key references + cascade options
            _on_delete(self)

        session.forget(self)
        self._apply_changes(self._last, {}, delete=True)
        self._modified = True
        self._deleted = True
        # handle the post-commit hooks
        if kwargs.get('skip_on_delete_i_really_mean_it') is not SKIP_ON_DELETE:
            self._after_delete()

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
        single = not isinstance(ids, (list, tuple, set, frozenset))
        if single:
            ids = [ids]
        pks = ['%s:%s'%(cls._namespace, id) for id in map(int, ids)]
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

        Optional keyword-only arguments:

            * *_limit* - A 2-tuple of (offset, count) that can be used to
              paginate or otherwise limit results returned by a numeric range
              query
            * *_numeric* - An optional boolean defaulting to False that forces
              the use of a numeric index for ``.get_by(col=val)`` queries even
              when ``col`` has an existing unique index

        If you would like to make queries against multiple columns or with
        multiple criteria, look into the Model.query class property.

        .. note:: rom will attempt to use a unique index first, then a numeric
            index if there was no unique index. You can explicitly tell rom to
            only use the numeric index by using ``.get_by(..., _numeric=True)``.
        .. note:: Ranged queries with `get_by(col=(start, end))` will only work
            with columns that use a numeric index.
        '''
        conn = _connect(cls)
        model = cls._namespace
        # handle limits and query requirements
        _limit = kwargs.pop('_limit', ())
        if _limit and len(_limit) != 2:
            raise QueryError("Limit must include both 'offset' and 'count' parameters")
        elif _limit and not all(isinstance(x, six.integer_types) for x in _limit):
            raise QueryError("Limit arguments must both be integers")
        if len(kwargs) != 1:
            raise QueryError("We can only fetch object(s) by exactly one attribute, you provided %s"%(len(kwargs),))

        _numeric = bool(kwargs.pop('_numeric', None))

        for attr, value in kwargs.items():
            plain_attr = attr.partition(':')[0]
            if isinstance(value, tuple) and len(value) != 2:
                raise QueryError("Range queries must include exactly two endpoints")

            # handle unique index lookups
            if attr in cls._unique and (plain_attr not in cls._index or not _numeric):
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
local row_key = string.format('%s:%s', namespace, id)
local is_delete = cjson.decode(ARGV[12])

-- [1] string.format("%s", d) will truncate d to the first null value, so we
--     can't rely on string.format() where we can reasonably expect nulls.

if not is_delete then
    -- check to make sure we don't have a data race condition
    local updated = {}
    for i, pair in ipairs(cjson.decode(ARGV[13])) do
        local odata = redis.call('HGET', row_key, pair[1])
        if odata ~= pair[2] then
            table.insert(updated, pair[1])
        end
    end
    if #updated > 0 then
        return cjson.encode({race=updated})
    end
end

-- check and update unique column constraints
for i, write in ipairs({false, true}) do
    for col, value in pairs(cjson.decode(ARGV[3])) do
        local key = string.format('%s:%s:uidx', namespace, col)
        if write then
            redis.call('HSET', key, value, id)
        else
            local known = redis.call('HGET', key, value)
            if known ~= id and known ~= false then
                return cjson.encode({unique=col})
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
    redis.call('HMSET', row_key, unpack(data))
end

-- remove old index data, update util.clean_index_lua when changed
local idata = redis.call('HGET', namespace .. '::', id)
local _changes = 0
if idata then
    idata = cjson.decode(idata)
    while #idata < 5 do
        idata[#idata + 1] = {}
    end
    for i, key in ipairs(idata[1]) do
        redis.call('SREM', string.format('%s:%s:idx', namespace, key), id)
        -- see note [1]
        redis.call('SREM', namespace .. ':' .. key .. ':idx', id)
        _changes = _changes + 1
    end
    for i, key in ipairs(idata[2]) do
        redis.call('ZREM', string.format('%s:%s:idx', namespace, key), id)
        -- see note [1]
        redis.call('ZREM', namespace .. ':' .. key .. ':idx', id)
        _changes = _changes + 1
    end
    for i, data in ipairs(idata[3]) do
        local key = string.format('%s:%s:pre', namespace, data[1])
        local mem = string.format('%s\0%s', data[2], id)
        redis.call('ZREM', key, mem)
        -- see note [1]
        local key = namespace .. ':' .. data[1] .. ':pre'
        local mem = data[2] .. '\0' .. id
        redis.call('ZREM', key, mem)
        _changes = _changes + 1
    end
    for i, data in ipairs(idata[4]) do
        if data[1] and data[2] then
            local key = string.format('%s:%s:suf', namespace, data[1])
            local mem = string.format('%s\0%s', data[2], id)
            redis.call('ZREM', key, mem)
            -- see note [1]
            local key = namespace .. ':' .. data[1] .. ':suf'
            local mem = data[2] .. '\0' .. id
            redis.call('ZREM', key, mem)
            _changes = _changes + 1
        end
    end
    for i, data in ipairs(idata[5]) do
        local key = namespace .. ':' .. data .. ':geo'
        redis.call('ZREM', key, id)
        _changes = _changes + 1
    end
end

if is_delete then
    redis.call('DEL', string.format('%s:%s', namespace, id))
    redis.call('HDEL', namespace .. '::', id)
    return cjson.encode({changes=_changes})
end

-- add new key index data
local nkeys = cjson.decode(ARGV[7])
for i, key in ipairs(nkeys) do
    redis.call('SADD', namespace .. ':' .. key .. ':idx', id)
end

-- add new scored index data
local nscored = {}
for key, score in pairs(cjson.decode(ARGV[8])) do
    redis.call('ZADD', namespace .. ':' .. key .. ':idx', score, id)
    nscored[#nscored + 1] = key
end

-- add new prefix data
local nprefix = {}
for i, data in ipairs(cjson.decode(ARGV[9])) do
    local key = namespace .. ':' .. data[1] .. ':pre'
    local mem = data[2] .. '\0' .. id
    redis.call('ZADD', key, data[3], mem)
    nprefix[#nprefix + 1] = {data[1], data[2]}
end

-- add new suffix data
local nsuffix = {}
for i, data in ipairs(cjson.decode(ARGV[10])) do
    local key = namespace .. ':' .. data[1] .. ':suf'
    local mem = data[2] .. '\0' .. id
    redis.call('ZADD', key, data[3], mem)
    nsuffix[#nsuffix + 1] = {data[1], data[2]}
end

-- add new geo data
local ngeo = {}
for i, data in ipairs(cjson.decode(ARGV[11])) do
    local key = namespace .. ':' .. data[1] .. ':geo'
    redis.call('GEOADD', key, data[2], data[3], id)
    nsuffix[#nsuffix + 1] = data[1]
end

if not is_delete then
    -- update known index data
    local encoded = cjson.encode({nkeys, nscored, nprefix, nsuffix, ngeo})
    redis.call('HSET', namespace .. '::', id, encoded)
end
return cjson.encode({changes=#nkeys + #nscored + #nprefix + #nsuffix + #ngeo + _changes})
''')

def _fix_bytes(d):
    if six.PY2:
        raise TypeError
    if isinstance(d, bytes):
        return d.decode('latin-1')
    raise TypeError

def redis_writer_lua(conn, pkey, namespace, id, unique, udelete, delete,
                     data, keys, scored, prefix, suffix, geo, old_data, is_delete):
    '''
    ... Actually write data to Redis. This is an internal detail. Please don't
    call me directly.
    '''
    ldata = []
    for pair in data.items():
        ldata.extend(pair)

    for item in prefix:
        item.append(_prefix_score(item[-1]))
    for item in suffix:
        item.append(_prefix_score(item[-1]))

    data = [json.dumps(x, default=_fix_bytes) for x in
            (unique, udelete, delete, ldata, keys, scored, prefix, suffix, geo, is_delete, old_data)]
    result = _redis_writer_lua(conn, [], [namespace, id] + data)

    if isinstance(result, client.BasePipeline):
        # we're in a pipelined write situation, don't parse the pipeline :P
        return

    if six.PY3:
        result = result.decode()

    result = json.loads(result)
    if 'unique' in result:
        result = result['unique']
        raise UniqueKeyViolation(
            "Value %r for %s:%s:uidx not distinct (failed for pk=%s)"%(
                unique[result], namespace, result, id),
            namespace, id)

    if 'race' in result:
        result = result['race']
        if pkey in result:
            raise EntityDeletedError(
                "Entity %s:%s deleted by another writer; use .save(force=True) to re-save"%(
                    namespace, id),
                namespace, id)

        raise DataRaceError(
            "%s:%s Column(s) %r updated by another writer, write aborted!"%(
                namespace, id, result),
            namespace, id)

__all__ = [k for k, v in globals().items() if getattr(v, '__doc__', None) and k not in _skip]
