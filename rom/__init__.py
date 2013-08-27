'''
Rom - the Redis object mapper for Python

Copyright 2013 Josiah Carlson

Released under the LGPL license version 2.1 and version 3 (you can choose
which you'd like to be bound under).


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

* Strings, ints, floats, decimals, booleans
* datetime.datetime, datetime.date, datetime.time
* Json columns (for nested structures)
* OneToMany and ManyToOne columns (for model references)

Indexes:

* Numeric range fetches, searches, and ordering
* Full-word text search (find me entries with col X having words A and B)

Other features:

* Per-thread entity cache (to minimize round-trips, easy saving of all
  entities)

Getting started
===============

1. Make sure you have Python 2.6 or 2.7 installed
2. Make sure that you have Andy McCurdy's Redis library installed:
   https://github.com/andymccurdy/redis-py/ or
   https://pypi.python.org/pypi/redis
3. (optional) Make sure that you have the hiredis library installed for Python
4. Make sure that you have a Redis server installed and available remotely
5. Update the Redis connection settings for ``rom`` via
   ``rom.util.set_connection_settings()`` (other connection update options,
   including per-model connections, can be read about in the ``rom.util``
   documentation)::

    import redis
    from rom import util

    util.set_connection_settings(host='myhost', db=7)

.. warning:: If you forget to update the connection function, rom will attempt
 to connect to localhost:6379 .

6. Create a model::

    class User(Model):
        email_address = String(required=True, unique=True)
        salt = String()
        hash = String()
        created_at = Float(default=time.time)

7. Create an instance of the model and save it::

    PASSES = 32768
    def gen_hash(password, salt=None):
        salt = salt or os.urandom(16)
        comp = salt + password
        out = sha256(comp).digest()
        for i in xrange(PASSES-1):
            out = sha256(out + comp).digest()
        return salt, out

    user = User(email_address='user@host.com')
    user.salt, user.hash = gen_hash(password)
    user.save()
    # session.commit() or session.flush() works too

8. Load and use the object later::

    user = User.get_by(email_address='user@host.com')

'''

from datetime import datetime, date, time as dtime
from decimal import Decimal as _Decimal
import json

import redis

from .exceptions import (ORMError, UniqueKeyViolation, InvalidOperation,
    QueryError, ColumnError, MissingColumn, InvalidColumnValue)
from .index import GeneralIndex
from .util import (_numeric_keygen, _string_keygen, ClassProperty, _connect,
    session, _many_to_one_keygen, _boolean_keygen, dt2ts, ts2dt, t2ts, ts2t)

VERSION = '0.19'

NULL = object()
MODELS = {}

__all__ = '''
    Model Column Integer Float Decimal String Text Json PrimaryKey ManyToOne
    ForeignModel OneToMany Query session Boolean DateTime Date Time'''.split()

_NUMERIC = (0, 0.0, _Decimal('0'), datetime(1970, 1, 1), date(1970, 1, 1), dtime(0, 0, 0))

class Column(object):
    '''
    Column objects handle data conversion to/from strings, store metadata
    about indices, etc. Note that these are "heavy" columns, in that whenever
    data is read/written, it must go through descriptor processing. This is
    primarily so that (for example) if you try to write a Decimal to a Float
    column, you get an error the moment you try to do it, not some time later
    when you try to save the object (though saving can still cause an error
    during the conversion process).

    Standard Arguments:

        * *required* - determines whether this column is required on
          creation
        * *default* - a default value (either a callable or a simple value)
          when this column is not provided
        * *unique* - can only be enabled on ``String`` columns, allows for
          required distinct column values (like an email address on a User
          model)
        * *index* - can be enabled on numeric, string, and unicode columns.
          Will create a ZSET-based numeric index for numeric columns and a
          "full word"-based search for string/unicode columns. If enabled
          for other (or custom) columns, remember to provide the
          ``keygen`` argument
        * *keygen* - pass a function that takes your column's value and
          returns the data that you want to index (see the keygen docs for
          what kinds of data to return)

    Notes:

        * Columns with 'unique' set to True can only be string columns
        * You can only have one unique column on any model
        * Unique and index are not mutually exclusive
        * The keygen argument determines how index values are generated
          from column data (with reasonably sensible defaults for numeric
          and string columns)
        * If you set required to True, then you must have the column set
          during object construction: ``MyModel(col=val)``
    '''
    _allowed = ()
    _default_ = None

    __slots__ = '_required _default _init _unique _index _model _attr _keygen'.split()

    def __init__(self, required=False, default=NULL, unique=False, index=False, keygen=None):
        self._required = required
        self._default = default
        self._unique = unique
        self._index = index
        self._init = False
        self._model = None
        self._attr = None
        self._keygen = None

        if unique:
            if self._allowed != str and self._allowed != unicode:
                raise ColumnError("Unique columns can only be strings")

        numeric = True
        if index and not isinstance(self, ManyToOne):
            if not any(isinstance(i, self._allowed) for i in _NUMERIC):
                numeric = False
                if isinstance(True, self._allowed):
                    keygen = keygen or _boolean_keygen
                if self._allowed not in (str, unicode) and not keygen:
                    raise ColumnError("Non-numeric/string indexed columns must provide keygen argument on creation")

        if index:
            self._keygen = keygen if keygen else (
                _numeric_keygen if numeric else _string_keygen)

    def _from_redis(self, value):
        convert = self._allowed[0] if isinstance(self._allowed, (tuple, list)) else self._allowed
        return convert(value)

    def _to_redis(self, value):
        if isinstance(value, long):
            return str(value)
        return repr(value)

    def _validate(self, value):
        if value is not None:
            if isinstance(value, self._allowed):
                return
        elif not self._required:
            return
        raise InvalidColumnValue("%s.%s has type %r but must be of type %r"%(
            self._model, self._attr, type(value), self._allowed))

    def _init_(self, obj, model, attr, value, loading):
        # You shouldn't be calling this directly, but this is what sets up all
        # of the necessary pieces when creating an entity from scratch, or
        # loading the entity from Redis
        self._model = model
        self._attr = attr

        if value is None:
            if self._default is NULL:
                if self._required:
                    raise MissingColumn("%s.%s cannot be missing"%(self._model, self._attr))
            elif callable(self._default):
                value = self._default()
            else:
                value = self._default
        elif not isinstance(value, self._allowed):
            try:
                value = self._from_redis(value)
            except (ValueError, TypeError) as e:
                raise InvalidColumnValue(*e.args)

        if not loading:
            self._validate(value)
        obj._data[attr] = value

    def __set__(self, obj, value):
        if not obj._init:
            self._init_(obj, *value)
            return
        try:
            value = self._from_redis(value)
        except (ValueError, TypeError):
            raise InvalidColumnValue("Cannot convert %r into type %s"%(value, self._allowed))
        self._validate(value)
        obj._data[self._attr] = value
        obj._modified = True
        session.add(obj)

    def __get__(self, obj, objtype):
        try:
            return obj._data[self._attr]
        except KeyError:
            AttributeError("%s.%s does not exist"%(self._model, self._attr))

    def __delete__(self, obj):
        if self._required:
            raise InvalidOperation("%s.%s cannot be null"%(self._model, self._attr))
        try:
            obj._data.pop(self._attr)
        except KeyError:
            raise AttributeError("%s.%s does not exist"%(self._model, self._attr))
        obj._modified = True
        session.add(obj)

class Integer(Column):
    '''
    Used for integer numeric columns.

    All standard arguments supported.

    Used via::

        class MyModel(Model):
            col = Integer()
    '''
    _allowed = (int, long)

class Boolean(Column):
    '''
    Used for boolean columns.

    All standard arguments supported.

    All values passed in on creation are casted via bool(), with the exception
    of None (which behaves as though the value was missing), and any existing
    data in Redis is considered ``False`` if empty, and ``True`` otherwise.

    Used via::

        class MyModel(Model):
            col = Boolean()

    Queries via ``MyModel.get_by(...)`` and ``MyModel.query.filter(...)`` work
    as expected when passed ``True`` or ``False``.

    .. note: these columns are not sortable by default.
    '''
    _allowed = bool
    def _to_redis(self, obj):
        return '1' if obj else ''
    def _from_redis(self, obj):
        return bool(obj)

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

class String(Column):
    '''
    A plain string column. Trying to save unicode strings will probably result
    in an error, if not bad data. This is the only type of column that can
    have a unique index.

    All standard arguments supported.

    This column can be indexed, which will allow for searching for words
    contained in the column, extracted via::

        filter(None, [s.lower().strip(string.punctuation) for s in val.split()])

    .. note:: only one column in any given model can be unique.

    Used via::

        class MyModel(Model):
            col = String()
    '''
    _allowed = str
    def _to_redis(self, value):
        return value

class Text(Column):
    '''
    A unicode string column.

    All standard arguments supported, except for ``unique``.

    Aside from not supporting ``unique`` indices, will generally have the same
    behavior as a ``String`` column, only supporting unicode strings. Data is
    encoded via utf-8 before writing to Redis. If you would like to create
    your own column to encode/decode differently, examine the source find out
    how to do it.

    Used via::

        class MyModel(Model):
            col = Text()
    '''
    _allowed = unicode
    def _to_redis(self, value):
        return value.encode('utf-8')
    def _from_redis(self, value):
        if isinstance(value, str):
            return value.decode('utf-8')
        return value

class Json(Column):
    '''
    Allows for more complicated nested structures as attributes.

    All standard arguments supported. The ``keygen`` argument must be provided
    if ``index`` is ``True``.

    Used via::

        class MyModel(Model):
            col = Json()
    '''
    _allowed = (dict, list, tuple)
    def _to_redis(self, value):
        return json.dumps(value)
    def _from_redis(self, value):
        if isinstance(value, self._allowed):
            return value
        return json.loads(value)

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

class ManyToOne(Column):
    '''
    This ManyToOne column allows for one model to reference another model.
    While a ManyToOne column does not require a reverse OneToMany column
    (which will return a list of models that reference it via a ManyToOne), it
    is generally seen as being useful to have both sides of the relationship
    defined.

    Aside from the name of the other model, only the ``required`` and
    ``default`` arguments are accepted.

    Used via::

        class MyModel(Model):
            col = ManyToOne('OtherModelName')

    .. note: Technically, all ``ManyToOne`` columns are indexed numerically,
      which means that you can find entities with specific id ranges or even
      sort by the ids referenced.

    '''
    __slots__ = Column.__slots__ + ['_ftable']
    def __init__(self, ftable, required=False, default=NULL):
        self._ftable = ftable
        Column.__init__(self, required, default, index=True, keygen=_many_to_one_keygen)

    def _from_redis(self, value):
        try:
            model = MODELS[self._ftable]
        except KeyError:
            raise ORMError("Missing foreign table %r referenced by %s.%s"%(self._ftable, self._model, self._attr))
        if isinstance(value, model):
            return value
        return model.get(value)

    def _validate(self, value):
        try:
            model = MODELS[self._ftable]
        except KeyError:
            raise ORMError("Missing foreign table %r referenced by %s.%s"%(self._ftable, self._model, self._attr))
        if not self._required and value is None:
            return
        if not isinstance(value, model):
            raise InvalidColumnValue("%s.%s has type %r but must be of type %r"%(
                self._model, self._attr, type(value), model))

    def _to_redis(self, value):
        if not value:
            return None
        if isinstance(value, (int, long)):
            return str(value)
        if value._new:
            # should spew a warning here
            value.save()
        v = str(getattr(value, value._pkey))
        return v

class ForeignModel(Column):
    '''
    This column allows for ``rom`` models to reference an instance of another
    model from an unrelated ORM or otherwise.

    .. note: In order for this mechanism to work, the foreign model *must*
      have an ``id`` attribute or property represents its primary key, and
      *must* have a classmethod or staticmethod named ``get()`` that returns
      the proper database entity.

    Used via::

        class MyModel(Model):
            col = ForeignModel(DjangoModel)

        dm = DjangoModel(col1='foo')
        django.db.transaction.commit()

        x = MyModel(col=dm)
        x.save()
    '''
    __slots__ = Column.__slots__ + ['_fmodel']
    def __init__(self, fmodel, required=False, default=NULL):
        self._fmodel = fmodel
        Column.__init__(self, required, default, index=True, keygen=_many_to_one_keygen)

    def _from_redis(self, value):
        if isinstance(value, self._fmodel):
            return value
        if isinstance(value, str) and value.isdigit():
            value = int(value, 10)
        return self._fmodel.get(value)

    def _validate(self, value):
        if not self._required and value is None:
            return
        if not isinstance(value, self._fmodel):
            raise InvalidColumnValue("%s.%s has type %r but must be of type %r"%(
                self._model, self._attr, type(value), self._fmodel))

    def _to_redis(self, value):
        if not value:
            return None
        if isinstance(value, (int, long, str)):
            return str(value)
        return str(value.id)

class OneToMany(Column):
    '''
    OneToMany columns do not actually store any information. They rely on a
    properly defined reverse ManyToOne column on the referenced model in order
    to be able to fetch a list of referring entities.

    Only the name of the other model can be passed.

    Used via::

        class MyModel(Model):
            col = OneToMany('OtherModelName')
    '''
    __slots__ = '_model _attr _ftable _required _unique _index'.split()
    def __init__(self, ftable):
        self._ftable = ftable
        self._required = self._unique = self._index = False
        self._model = self._attr = None

    def _to_redis(self, value):
        return ''

    def __set__(self, obj, value):
        if not obj._init:
            self._model, self._attr = value[:2]
            try:
                MODELS[self._ftable]
            except KeyError:
                raise ORMError("Missing foreign table %r referenced by %s.%s"%(self._ftable, self._model, self._attr))
            return
        raise InvalidOperation("Cannot assign to OneToMany relationships")

    def __get__(self, obj, objtype):
        try:
            model = MODELS[self._ftable]
        except KeyError:
            raise ORMError("Missing foreign table %r referenced by %s.%s"%(self._ftable, self._model, self._attr))

        for attr, col in model._columns.iteritems():
            if isinstance(col, ManyToOne) and col._ftable == self._model:
                return model.get_by(**{attr: getattr(obj, obj._pkey)})

        raise ORMError("Reverse ManyToOne relationship not found for %s.%s -> %s"%(self._model, self._attr, self._ftable))

    def __delete__(self, obj):
        raise InvalidOperation("Cannot delete OneToMany relationships")

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

def is_numeric(value):
    try:
        value + 0
        return True
    except Exception:
        return False

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
                .execute()

        If you only want to match a single value as part of your range query,
        you can pass an integer, float, or Decimal object by itself, similar
        to the ``Model.get_by()`` method::

            results = MyModel.query \\
                .filter(ncol=5) \\
                .execute()

        '''
        cur_filters = list(self._filters)
        for attr, value in kwargs.iteritems():
            if isinstance(value, bool):
                value = str(bool(value))

            if isinstance(value, (int, long, float, _Decimal, datetime, date, dtime)):
                # for simple numeric equiality filters
                value = (value, value)

            if isinstance(value, (str, unicode)):
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
        return Query(self._model, tuple(cur_filters), self._order_by, self._limit)

    def order_by(self, column):
        '''
        When provided with a column name, will sort the results of your query::

            # returns all users, ordered by the created_at column in
            # descending order
            User.query.order_by('-created_at').execute()
        '''
        return Query(self._model, self._filters, column, self._limit)

    def limit(self, offset, count):
        '''
        Will limit the number of results returned from a query::

            # returns the most recent 25 users
            User.query.order_by('-created_at').limit(0, 25).execute()
        '''
        return Query(self._model, self._filters, self._order_by, (offset, count))

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
        return self._model._gindex.count(_connect(self._model), filters)

    def _search(self):
        limit = () if not self._limit else self._limit
        return self._model._gindex.search(
            _connect(self._model), self._filters, self._order_by, *limit)

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
