
import redis

from .exceptions import (ORMError, UniqueKeyViolation, QueryError, ColumnError, InvalidColumnValue)
from .datatypes import (Column, PrimaryKey )
from .index import GeneralIndex
from .util import _connect
from .sharedvars import MODELS
from .query import Query
from .session import session
from .classproperty import ClassProperty

__all__ = ['Model']


class _ModelMetaclass(type):
    def __new__(cls, name, bases, dict):
        if name in MODELS:
            raise ORMError("Cannot have two models with the same name %s"%name)
        dict['_required'] = required = set()
        dict['_index'] = index = set()
        dict['_columns'] = columns = {}
        unique = None # up to *one* unique index per model
        pkey = None

        # load all columns from any base classes to allow for validation
        odict = {}
        for ocls in reversed(bases):
            if hasattr(ocls, '_columns'):
                odict.update(ocls._columns)
        odict.update(dict)
        dict = odict

        if not any(isinstance(col, PrimaryKey) for col in dict.itervalues()):
            if 'id' in dict:
                raise ColumnError("Cannot have non-primary key named 'id'")
            dict['id'] = PrimaryKey()

        # validate all of our columns to ensure that they fulfill our
        # expectations
        for attr, col in dict.iteritems():
            if isinstance(col, Column):
                columns[attr] = col
                if col._required:
                    required.add(attr)
                if col._index:
                    index.add(attr)
                if col._unique:
                    # We only allow one for performance, if only so that
                    # we don't need to watch a pile of keys in order to update
                    # unique constraints on save. This can be easily addressed
                    # with a Lua script in the future, but then all data and
                    # index updates need to be passed into a single Lua
                    # function in order to work properly... Yuck. Single
                    # unique column for now.
                    if unique:
                        raise ColumnError(
                            "Only one unique column allowed, you have %s and %s"%(
                            attr, unique)
                        )
                    unique = attr
            if isinstance(col, PrimaryKey):
                pkey = attr

        dict['_unique'] = unique
        dict['_pkey'] = pkey
        dict['_gindex'] = GeneralIndex(name)

        MODELS[name] = model = type.__new__(cls, name, bases, dict)
        return model


class Model(object):
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
    __metaclass__ = _ModelMetaclass
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
                if not isinstance(cval, str):
                    cval = self._columns[attr]._to_redis(cval)
                self._last[attr] = cval
        self._init = True
        session.add(self)

    @property
    def _pk(self):
        return '%s:%s'%(self.__class__.__name__, getattr(self, self._pkey))

    @classmethod
    def _apply_changes(cls, old, new, full=False, delete=False):
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

            # check for unique keys
            if cls._unique:
                ouval = old.get(cls._unique)
                nuval = new.get(cls._unique)
                nuvale = columns[cls._unique]._to_redis(nuval)

                if nuval and (ouval != nuvale or full):
                    ikey = "%s:%s:uidx"%(model, cls._unique)
                    pipe.watch(ikey)
                    ival = pipe.hget(ikey, nuvale)
                    if not ival or ival == str(pk):
                        pipe.multi()
                    else:
                        pipe.unwatch()
                        raise UniqueKeyViolation("Value %r for %s not distinct"%(nuval, ikey))

            # update individual columns
            for attr in cls._columns:
                ikey = None
                if attr == cls._unique:
                    ikey = "%s:%s:uidx"%(model, attr)

                ca = columns[attr]
                roval = old.get(attr)
                oval = ca._from_redis(roval) if roval is not None else None

                nval = new.get(attr)
                rnval = ca._to_redis(nval) if nval is not None else None

                # Add/update standard index
                if hasattr(ca, '_keygen') and ca._keygen and not delete and nval is not None:
                    generated = ca._keygen(nval)
                    if isinstance(generated, (list, tuple, set)):
                        for k in generated:
                            keys.add('%s:%s'%(attr, k))
                    elif isinstance(generated, dict):
                        for k, v in generated.iteritems():
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
                    if oval is not None:
                        pipe.hdel(ikey, roval)
                    pipe.hset(ikey, rnval, pk)

            id_only = str(pk)
            if delete:
                changes += 1
                cls._gindex._unindex(conn, pipe, id_only)
                pipe.delete(key)
            else:
                if data:
                    pipe.hmset(key, data)
                cls._gindex.index(conn, id_only, keys, scores, pipe=pipe)

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
        session.add(self)

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
        pks = ['%s:%s'%(cls.__name__, id) for id in ids]
        # get from the session, if possible
        out = map(session.get, pks)
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
                    out[i] = cls(_loading=True, **data)
            # Get rid of missing models
            out = filter(None, out)
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
        elif _limit and not all(isinstance(x, (int, long)) for x in _limit):
            raise QueryError("Limit arguments bust both be integers")
        if len(kwargs) != 1:
            raise QueryError("We can only fetch object(s) by exactly one attribute, you provided %s"%(len(kwargs),))

        for attr, value in kwargs.iteritems():
            plain_attr = attr.partition(':')[0]
            if isinstance(value, tuple) and len(value) != 2:
                raise QueryError("Range queries must include exactly two endpoints")

            # handle unique index lookups
            if attr == cls._unique:
                if isinstance(value, tuple):
                    raise QueryError("Cannot query a unique index with a range of values")
                single = not isinstance(value, list)
                if single:
                    value = [value]
                qvalues = map(cls._columns[attr]._to_redis, value)
                ids = filter(None, conn.hmget('%s:%s:uidx'%(model, attr), qvalues))
                if not ids:
                    return None if single else []
                return cls.get(ids[0] if single else ids)

            if plain_attr not in cls._index:
                raise QueryError("Cannot query on a column without an index")

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
