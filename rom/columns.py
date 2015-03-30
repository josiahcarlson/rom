
from datetime import datetime, date, time as dtime
from decimal import Decimal as _Decimal
import json
import warnings

import six

from .exceptions import (ORMError, InvalidOperation, ColumnError,
    MissingColumn, InvalidColumnValue, RestrictError)
from .util import (_numeric_keygen, _string_keygen, _many_to_one_keygen,
    _boolean_keygen, dt2ts, ts2dt, t2ts, ts2t, session, _connect,
    FULL_TEXT, CASE_INSENSITIVE, SIMPLE)

NULL = object()
MODELS = {}
_NUMERIC = (0, 0.0, _Decimal('0'), datetime(1970, 1, 1), date(1970, 1, 1), dtime(0, 0, 0))
USE_LUA = True
NO_ACTION_DEFAULT = object()
SKIP_ON_DELETE = object()
ON_DELETE = ('no action', 'restrict', 'cascade')

def _restrict(entity, attr, refs):
    name = entity.__class__.__name__
    raise RestrictError(
        "Cannot delete entity %s with pk %s, %i foreign references from %s.%s exist"%(
            name, getattr(entity, entity._pkey), len(refs), name, attr))

def _on_delete(ent):
    '''
    This function handles all on_delete semantics defined on OneToMany columns.

    This function only exists because 'cascade' is *very* hard to get right.
    '''
    seen = set([ent._pk])
    to_delete = [ent]
    for self in to_delete:
        for attr, col in self._columns.items():
            if not isinstance(col, OneToMany):
                continue

            if col._on_delete == 'no action':
                continue

            # get the references
            refs = getattr(self, attr)
            if not refs:
                continue

            if col._on_delete == 'restrict':
                # restrict will raise an exception
                _restrict(self, attr, refs)

            # otherwise col._on_delete == 'cascade'
            for ent in refs:
                if ent._pk not in seen:
                    seen.add(ent._pk)
                    to_delete.append(ent)

    # If we got here, then to_delete includes all items to delete. Let's delete
    # them!
    for self in to_delete:
        self.delete(skip_on_delete_i_really_mean_it=SKIP_ON_DELETE)

_missing_keygen_warning = '''You have not specified a keygen for generating keys
to index your String() or Text() column. By default, rom has been using its
FULL_TEXT index on these columns in the past, but now requests/requires the
specification of a keygen function. Provide an explicit keygen argument of
rom.FULL_TEXT, rom.SIMPLE, rom.CASE_INSENSITIVE or some other keygen that
matches the rom index API to remove this warning. This warning will become an
exception in rom >= 0.31.0.'''.replace('\n', ' ')

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
        * *unique* - can be enabled on string, unicode, and integer columns, and
          allows for required distinct column values (like an email address on
          a User model)
        * *index* - can be enabled on numeric, string, and unicode columns.
          Will create a ZSET-based numeric index for numeric columns and a
          "full word"-based search for string/unicode columns. If enabled
          for other (or custom) columns, remember to provide the
          ``keygen`` argument
        * *keygen* - pass a function that takes your column's value and
          returns the data that you want to index (see the keygen docs for
          what kinds of data to return)
        * *prefix* - can be enabled on any column that generates a list of
          strings as a result of the default or passed *keygen* function, and
          will allow the searching of prefix matches (autocomplete) over your
          data
        * *suffix* - can be enabled in the same contexts as *prefix* and
          enables suffix matching over your data. Any individual string in the
          returned data will be reversed (you need to make sure this makes
          conceptual sense with your data) before being stored or used.

    .. warning: Enabling prefix or suffix matching on a column only makes
       sense for columns defining a non-numeric *keygen* function.

    Notes:

        * If you have disabled Lua support, you can only have at most one
          unique column on each model
        * *Unique* and *index* are not mutually exclusive
        * The *keygen* argument determines how index values are generated
          from column data (with a reasonably sensible default for numeric
          columns, and 2 convenient options for string/text columns)
        * If you set *required* to True, then you must have the column set
          during object construction: ``MyModel(col=val)``
        * If *index* and *prefix*, or *index* and *suffix* are set, the same
          keygen will be used for both the regular *index* as well as the
          *prefix* and/or *suffix* searches
        * If *prefix* is set, you can perform pattern matches over your data.
          See documention on ``Query.like()`` for details.
        * Pattern matching over data is only guaranteed to be valid or correct
          for ANSI strings that do not include nulls, though we make an effort
          to support unicode strings and strings with embedded nulls
        * Prefix, suffix, and pattern matching are performed within a Lua
          script, so may have substantial execution time if there are a large
          number of matching prefix or suffix entries
        * Whenever possible, pattern matching will attempt to use any
          non-wildcard prefixes on the pattern to limit the items to be
          scanned. A pattern starting with ``?``, ``*``, ``+``, or ``!`` will
          not be able to use any prefix, so will scan the entire index for
          matches (aka: expensive)
    '''
    _allowed = ()
    _default_ = None

    __slots__ = '_required _default _init _unique _index _model _attr _keygen _prefix _suffix'.split()

    def __init__(self, required=False, default=NULL, unique=False, index=False, keygen=None, prefix=False, suffix=False):
        self._required = required
        self._default = default
        self._unique = unique
        self._index = index
        self._prefix = prefix
        self._suffix = suffix
        self._init = False
        self._model = None
        self._attr = None
        self._keygen = None

        if not self._allowed and not hasattr(self, '_fmodel') and not hasattr(self, '_ftable'):
            raise ColumnError("Missing valid _allowed attribute")

        allowed = (self._allowed,) if isinstance(self._allowed, type) else self._allowed
        is_string = all(issubclass(x, six.string_types) for x in allowed)
        is_integer = all(issubclass(x, six.integer_types) for x in allowed)
        if unique:
            if not (is_string or is_integer):
                raise ColumnError("Unique columns can only be strings or integers")

        numeric = True
        if index and not isinstance(self, ManyToOne):
            if not any(isinstance(i, allowed) for i in _NUMERIC):
                numeric = False
                if issubclass(bool, allowed):
                    keygen = keygen or _boolean_keygen
                if not is_string and not keygen:
                    raise ColumnError("Non-numeric/string indexed columns must provide keygen argument on creation")

        if (index or prefix or suffix) and is_string and keygen is None:
            import sys
            if sys.modules[__name__.rpartition('.')[0]].VERSION >= '0.31.0':
                raise ColumnError("Indexed string column missing explicit keygen argument, try rom.FULL_TEXT, rom.SIMPLE, or rom.CASE_INSENSITIVE")

            warnings.warn(_missing_keygen_warning, FutureWarning, stacklevel=2)

        if index:
            self._keygen = keygen if keygen else (
                _numeric_keygen if numeric else _string_keygen)
        elif prefix or suffix:
            self._keygen = keygen if keygen else _string_keygen

    def _from_redis(self, value):
        convert = self._allowed[0] if isinstance(self._allowed, (tuple, list)) else self._allowed
        return convert(value)

    def _to_redis(self, value):
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
            if value is None:
                try:
                    return self.__delete__(obj)
                except AttributeError:
                    # We can safely suppress this, the column was already set
                    # to None or deleted
                    return
            if not isinstance(value, self._allowed):
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
    _allowed = six.integer_types
    def _to_redis(self, value):
        return str(value)

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
    _allowed = (float,) + six.integer_types

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

if six.PY2:
    class String(Column):
        '''
        .. note:: this column type is only available in Python 2.x

        A plain string column. Trying to save unicode strings will probably result
        in an error, if not bad data.

        All standard arguments supported.

        This column can be indexed, which will allow for searching for words
        contained in the column, extracted via::

            filter(None, [s.lower().strip(string.punctuation) for s in val.split()])

        Used via::

            class MyModel(Model):
                col = String()
        '''
        _allowed = str
        def _to_redis(self, value):
            return value

class Text(Column):
    '''
    A unicode string column. All standard arguments supported. Behavior is
    more or less identical to the String column type, except that unicode is
    supported (unicode in 2.x, str in 3.x). UTF-8 is used by default as the
    encoding to bytes on the wire.

    Used via::

        class MyModel(Model):
            col = Text()
    '''
    _allowed = six.text_type
    def _to_redis(self, value):
        return value.encode('utf-8') if six.PY2 or not USE_LUA else value
    def _from_redis(self, value):
        if isinstance(value, six.binary_type):
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
    _allowed = six.integer_types

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
        if isinstance(value, six.integer_types):
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
        if isinstance(value, six.string_types) and value.isdigit():
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
        if isinstance(value, six.integer_types):
            return str(value)
        return str(value.id)

_on_delete_warning = '''You have not specified an on_delete behavior. Rom will
default to 'no action' to be consistent with behavior prior to 0.27.0. Provide
an explicit behavior to remove this warning. This will become an exception in
rom >= 0.28.0.'''.replace('\n', ' ')

class OneToMany(Column):
    '''
    OneToMany columns do not actually store any information. They rely on a
    properly defined reverse ManyToOne column on the referenced model in order
    to be able to fetch a list of referring entities.

    Only three arguments are supported:

        * *ftable* - the name of the other model
        * *on_delete* - how to handle foreign key references on delete,
            supported options include: 'no action', 'restrict', and 'cascade'
        * *column* - the attribute on the other model with the reference to
            this column, required if the foreign model has multiple columns
            referencing this model

    Used via::

        class MyModel(Model):
            col = OneToMany('OtherModelName', on_delete='restrict')
            ocol = OneToMany('ModelName', on_delete='no action')

    As of rom 0.27.0, OneToMany columns have an optional ``on_delete``
    argument(which will become required in 0.28.0) , which defines how
    referring entities should be handled. The two available options are
    ``'no action'``, which is the only behavior available for rom versions
    prior to 0.27.0, and ``'restrict'``, which aborts the delete if there
    there are any entities with a reference to the entity being deleted.

    '''
    __slots__ = '_model _attr _ftable _required _unique _index _prefix _suffix _keygen _on_delete _column'.split()
    def __init__(self, ftable, on_delete=NO_ACTION_DEFAULT, column=None):
        if on_delete is NO_ACTION_DEFAULT:
            import sys
            if sys.modules[__name__.rpartition('.')[0]].VERSION >= '0.28.0':
                raise ColumnError("No on_delete action specified")

            warnings.warn(_on_delete_warning, FutureWarning, stacklevel=2)
            on_delete = 'no action'
        if on_delete not in ON_DELETE:
            raise ColumnError("on_delete argument must be one of %r, you provided %r"%(
                list(ON_DELETE), on_delete))
        self._ftable = ftable
        self._required = self._unique = self._index = self._prefix = self._suffix = False
        self._model = self._attr = self._keygen = None
        self._on_delete = on_delete
        self._column = column

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

        if self._column:
            return model.get_by(**{self._column: getattr(obj, obj._pkey)})

        for attr, col in model._columns.items():
            if isinstance(col, ManyToOne) and col._ftable == self._model:
                return model.get_by(**{attr: getattr(obj, obj._pkey)})

        raise ORMError("Reverse ManyToOne relationship not found for %s.%s -> %s"%(self._model, self._attr, self._ftable))

    def __delete__(self, obj):
        raise InvalidOperation("Cannot delete OneToMany relationships")
