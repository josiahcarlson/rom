
'''
Rom - the Redis object mapper for Python

Copyright 2013-2016 Josiah Carlson

Released under the LGPL license version 2.1 and version 3 (you can choose
which you'd like to be bound under).
'''

from datetime import datetime, date, time as dtime
from decimal import Decimal as _Decimal
from functools import wraps
from itertools import product
import json

import six

from .exceptions import (ORMError, InvalidOperation, ColumnError,
    MissingColumn, InvalidColumnValue, RestrictError)
from .util import (_numeric_keygen, _string_keygen, _many_to_one_keygen,
    _boolean_keygen, dt2ts, ts2dt, t2ts, ts2t, session, _connect,
    STRING_INDEX_KEYGENS_STR)


NULL = object()
MODELS = {}
MODELS_REFERENCED = {}
_NUMERIC = (0, 0.0, _Decimal('0'), datetime(1970, 1, 1), date(1970, 1, 1), dtime(0, 0, 0))
NO_ACTION_DEFAULT = object()
SKIP_ON_DELETE = object()
ON_DELETE = ('no action', 'restrict', 'cascade', 'set null', 'set default')
six.string_types_ex = six.string_types
if six.PY3:
    six.string_types_ex += (bytes,)

def is_numeric(allowed):
    return any(isinstance(i, allowed) for i in _NUMERIC)

def is_string(allowed):
    allowed = (allowed,) if isinstance(allowed, type) else allowed
    return any(issubclass(a, i) for a,i in product(allowed, six.string_types_ex))

def _restrict(entity, attr, refs):
    name = entity._namespace
    name2 = refs[0]._namespace
    return RestrictError(
        "Cannot delete entity %s with pk %s, %i foreign references from %s.%s exist"%(
            name, getattr(entity, entity._pkey), len(refs), name2, attr))

def _on_delete(ent):
    '''
    This function handles all on_delete semantics defined on OneToMany columns.

    This function only exists because 'cascade' is *very* hard to get right.
    '''
    seen_d = set([ent._pk])
    to_delete = [ent]
    seen_s = set()
    to_save = []

    def _set_default(ent, attr, de=NULL):
        pk = ent._pk
        if pk in seen_d:
            # going to be deleted, don't need to modify
            return

        col = ent.__class__._columns[attr]
        de = de if de is not NULL else col._default
        if de in (None, NULL):
            setattr(ent, attr, None)
        elif callable(col._default):
            setattr(ent, attr, col._default())
        else:
            setattr(ent, attr, col._default)

        if pk not in seen_s:
            seen_s.add(pk)
            to_save.append(ent)

    for self in to_delete:
        for tbl, attr, action in MODELS_REFERENCED.get(self._namespace, ()):
            if action == 'no action':
                continue

            refs = MODELS[tbl].get_by(**{attr: self.id})
            if not refs:
                continue

            if action == 'restrict':
                # raise the exception here for a better traceback
                raise _restrict(self, attr, refs)
            elif action == 'set null':
                for ref in refs:
                    _set_default(ref, attr, None)
                continue
            elif action == 'set default':
                for ref in refs:
                    _set_default(ref, attr)
                continue

            # otherwise col._on_delete == 'cascade'
            for ent in (refs if isinstance(refs, list) else [refs]):
                if ent._pk not in seen_d:
                    seen_d.add(ent._pk)
                    to_delete.append(ent)

    # If we got here, then to_delete includes all items to delete. Let's delete
    # them!
    for self in to_delete:
        self.delete(skip_on_delete_i_really_mean_it=SKIP_ON_DELETE)
    for self in to_save:
        # Careful not to resurrect deleted entities
        if self._pk not in seen_d:
            self.save()

def _check_on_delete(on_delete, required, default):
    if on_delete is NO_ACTION_DEFAULT:
        return ColumnError("No on_delete action specified")
    elif on_delete not in ON_DELETE:
        return ColumnError("on_delete argument must be one of %r, you provided %r"%(
            list(ON_DELETE), on_delete))
    elif required:
        if on_delete == 'set null':
            return ColumnError("on_delete action is 'set null', but this column is required")
        elif on_delete == 'set default' and default in (NULL, None):
            return ColumnError("on_delete action is 'set default', but this required column has no default")

def _keygen_wrapper(keygen):
    @wraps(keygen)
    def _wrapper(attr, dct):
        return keygen(dct.get(attr))
    return _wrapper

_missing_keygen_warning = '''You have not specified a keygen for generating keys
to index your String() or Text() column. By default, rom has been using its
FULL_TEXT index on these columns in the past, but now requests/requires the
specification of a keygen function. Provide an explicit keygen argument of
%s or some other keygen that
matches the rom index API to remove this warning. This warning will become an
exception in rom >= 0.31.0.'''.replace('\n', ' ')%STRING_INDEX_KEYGENS_STR

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
        * *keygen2* - pass a function that takes your column name and the dict
          representing the current entity's complete data - can be used for
          creating multi-column indexes

    String/Text arguments:

        * *prefix* - can be enabled on any column that generates a list of
          strings as a result of the default or passed *keygen* function, and
          will allow the searching of prefix matches (autocomplete) over your
          data. See ``Query.startswith()`` for details.
        * *suffix* - can be enabled in the same contexts as *prefix* and
          enables suffix matching over your data. Any individual string in the
          returned data will be reversed (you need to make sure this makes
          conceptual sense with your data) before being stored or used. See
          ``Query.endswith()`` for details.

    .. warning:: Enabling prefix or suffix matching on a column only makes
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

    There are 3 types of string indexes that rom currently supports:

        * *SIMPLE*/*SIMPLE_CI* - sorting only with ``query.order_by('x')`` -
          https://pythonhosted.org/rom/rom.html#rom.util.SIMPLE
        * *IDENTITY*/*IDENTITY_CI* - equality only with ``query.filter(x=...)`` -
          https://pythonhosted.org/rom/rom.html#rom.util.IDENTITY
        * *FULL_TEXT* - bag of words inverted index for ``query.filter(x=...)`` -
          https://pythonhosted.org/rom/rom.html#rom.util.FULL_TEXT

    To each of those 3 index types, you can further add a prefix/suffix index,
    whose semantics are as follows:

        * *prefix* - ``query.startswith(column=pfix)`` and ``query.like(column='stuff?*')``
        * *suffix* - ``query.endswith(column=sfix)``
        * *SIMPLE*/*SIMPLE_CI*/*IDENTITY*/*IDENTITY_CI* - *prefix*/*suffix* the
          whole string case sensitive or insensitive
        * *FULL_TEXT* - *prefix*/*suffix* on individual words parsed out of the
          full text

    '''
    _allowed = ()

    __slots__ = '_required _default _init _unique _index _model _attr _keygen _prefix _suffix'.split()

    def __init__(self, required=False, default=NULL, unique=False, index=False, keygen=None, prefix=False, suffix=False, keygen2=None):
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

        if (keygen or keygen2) and not (index or prefix or suffix):
            raise ColumnError("Explicit keygen provided, but no index type spcified (index, prefix, and suffix all False)")

        if not self._allowed and not hasattr(self, '_fmodel') and not hasattr(self, '_ftable'):
            raise ColumnError("Missing valid class-level _allowed attribute on %r"%(type(self),))

        allowed = (self._allowed,) if isinstance(self._allowed, type) else self._allowed
        is_integer = all(issubclass(x, six.integer_types) for x in allowed)
        if unique:
            if not (is_string or is_integer):
                raise ColumnError("Unique columns can only be strings or integers")

        if keygen and keygen2:
            raise ColumnError("Can only specify one of 'keygen' and 'keygen2' arguments at a time, you provided both")

        if keygen2:
            # new-style keygen is ready :D
            self._keygen = keygen2
            return

        numeric = True
        if index and not isinstance(self, (ManyToOne, OneToOne)):
            if not is_numeric(allowed):
                numeric = False
                if issubclass(bool, allowed):
                    keygen = keygen or _boolean_keygen
                if not is_string(allowed) and not keygen:
                    raise ColumnError("Non-numeric/string indexed columns must provide keygen argument on creation")

        if (index or prefix or suffix) and is_string(allowed) and keygen is None:
            raise ColumnError("Indexed string column missing explicit keygen argument, try one of: %s"%STRING_INDEX_KEYGENS_STR)

        if index:
            keygen = keygen if keygen else (
                _numeric_keygen if numeric else _string_keygen)
        elif prefix or suffix:
            keygen = keygen if keygen else _string_keygen

        if keygen:
            # old-style keygen needs a wrapper
            self._keygen = _keygen_wrapper(keygen)

    def _from_redis(self, value):
        convert = self._allowed if callable(self._allowed) else self._allowed[0]
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
            if self._default in (NULL, None):
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

    All standard arguments supported. See ``Column`` for details on supported
    arguments.

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

    All standard arguments supported. See ``Column`` for details on supported
    arguments.

    All values passed in on creation are casted via bool(), with the exception
    of None (which behaves as though the value was missing), and any existing
    data in Redis is considered ``False`` if empty, and ``True`` otherwise.

    Used via::

        class MyModel(Model):
            col = Boolean()

    Queries via ``MyModel.get_by(...)`` and ``MyModel.query.filter(...)`` work
    as expected when passed ``True`` or ``False``.

    .. note:: these columns are not sortable by default.
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

    All standard arguments supported. See ``Column`` for details on supported
    arguments.

    Used via::

        class MyModel(Model):
            col = Float()
    '''
    _allowed = (float,) + six.integer_types

class Decimal(Column):
    '''
    A Decimal-only numeric column (converts ints/longs into Decimals
    automatically). Attempts to assign Python float will fail.

    All standard arguments supported. See ``Column`` for details on supported
    arguments.

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

    All standard arguments supported. See ``Column`` for details on supported
    arguments.

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

    All standard arguments supported. See ``Column`` for details on supported
    arguments.

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

    All standard arguments supported. See ``Column`` for details on supported
    arguments.

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
    A plain string column (str in 2.x, bytes in 3.x). Trying to save unicode
    strings will probably result in an error, if not corrupted data.

    All standard arguments and String/Text arguments supported. See ``Column``
    for details on supported arguments.

    This column can be indexed in one of five ways - a sorted index on a 7
    byte prefix of the value (``keygen=rom.SIMPLE``), a sorted index on a
    lowercased 7 byte prefix of the value (``keygen=rom.SIMPLE_CI``),
    a case-insensitive full-text index (``keygen=rom.FULL_TEXT``),
    a case-sensitive identity index (``keygen=rom.IDENTITY``), and a
    case-insensitive identity index (``keygen=rom.IDENTITY_CI``).

    Used via::

        class MyModel(Model):
            col = String()
    '''
    _allowed = str if six.PY2 else bytes
    def _to_redis(self, value):
        return value.decode('latin-1').encode('utf-8')

    def _from_redis(self, value):
        return value

    def _init_(self, obj, model, attr, value, loading):
        if value != None:
            if not isinstance(value, self._allowed):
                value = value.encode('latin-1')
            if loading:
                value = value.decode('utf-8').encode('latin-1')
        return Column._init_(self, obj, model, attr, value, loading)

class Text(Column):
    '''
    A unicode string column. Behavior is more or less identical to the String
    column type, except that unicode is supported (unicode in 2.x, str in 3.x).
    UTF-8 is used by default as the encoding to bytes on the wire, which *will*
    affect ``rom.SIMPLE`` and ``rom.SIMPLE_CI`` indexes.

    All standard arguments supported. See ``Column`` for details on supported
    arguments.

    This column can be indexed in one of five ways - a sorted index on a 7
    byte prefix of the value (``keygen=rom.SIMPLE``), a sorted index on a
    lowercased 7 byte prefix of the value (``keygen=rom.SIMPLE_CI``),
    a case-insensitive full-text index (``keygen=rom.FULL_TEXT``),
    a case-sensitive identity index (``keygen=rom.IDENTITY``), and a
    case-insensitive identity index (``keygen=rom.IDENTITY_CI``).

    For the 7 byte prefix/suffixes on indexes using the ``rom.SIMPLE`` and
    ``rom.SIMPLE_CI`` keygen, because we use UTF-8 to encode text, a single
    character can turn into 1-3 bytes, so may not be useful in practice.

    Used via::

        class MyModel(Model):
            col = Text()
    '''
    _allowed = six.text_type
    def _to_redis(self, value):
        return value.encode('utf-8') if six.PY2 else value
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
        return json.dumps(value, sort_keys=True)
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
            if loading:
                raise InvalidColumnValue("Cannot set none primary key on object loading")
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

    Four arguments are supported:

        * *ftable* - the name of the other model (required argument)
        * *on_delete* - how to handle foreign key references on delete,
            supported options include: 'no action', 'restrict', 'cascade'
            'set default', and 'set null' (required argument)
        * *required* - determines whether this column is required on
          creation
        * *default* - a default value (either a callable or a simple value)
          when this column is not provided

    Used via::

        class MyModel(Model):
            col = ManyToOne('OtherModelName')

    .. note:: All ``ManyToOne`` columns are indexed numerically, which means
      that you can find entities referencing specific id ranges or even sort by
      referenced ids.

    '''
    __slots__ = Column.__slots__ + ['_ftable', '_on_delete']
    def __init__(self, ftable, on_delete=NO_ACTION_DEFAULT, required=False, default=NULL):
        exc = _check_on_delete(on_delete, required, default)
        if exc:
            raise exc

        self._ftable = ftable
        self._on_delete = on_delete
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

class OneToOne(ManyToOne):
    '''
    This OneToOne column allows for one model to reference another model.
    A OneToOne column does not require a reverse OneToOne column, and provides
    ``on_delete`` behavior.

    Five arguments are supported:

        * *ftable* - the name of the other model (required argument)
        * *on_delete* - how to handle foreign key references on delete,
            supported options include: 'no action', 'restrict', 'cascade'
            'set default', and 'set null' (required argument)
        * *required* - determines whether this column is required on
          creation
        * *default* - a default value (either a callable or a simple value)
          when this column is not provided
        * *unique* - whether or not the referenced entity must be a unique
            reference

    Used via::

        class MyModel(Model):
            col = OneToOne('OtherModelName', 'no action')

    .. note:: All ``OneToOne`` columns are indexed numerically, which means
      that you can find entities referencing specific id ranges or even sort by
      referenced ids.

    '''
    __slots__ = Column.__slots__ + ['_ftable', '_on_delete']
    def __init__(self, ftable, on_delete=NO_ACTION_DEFAULT, required=False, default=NULL, unique=False):
        exc = _check_on_delete(on_delete, required, default)
        if exc:
            raise exc

        self._on_delete = on_delete
        self._ftable = ftable
        Column.__init__(self, required, default, unique, index=True, keygen=_many_to_one_keygen)

class ForeignModel(Column):
    '''
    This column allows for ``rom`` models to reference an instance of another
    model from an unrelated ORM or otherwise.

    .. note:: In order for this mechanism to work, the foreign model *must*
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
        if isinstance(value, six.string_types_ex) and value.isdigit():
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

class OneToMany(Column):
    '''
    OneToMany columns do not actually store any information. They rely on a
    properly defined reverse ManyToOne column on the referenced model in order
    to be able to fetch a list of referring entities.

    Two arguments are supported:

        * *ftable* - the name of the other model
        * *column* - the attribute on the other model with the reference to
            this column, required if the foreign model has multiple columns
            referencing this model with OneToOne or ManyToOne columns

    Used via::

        class MyModel(Model):
            col = OneToMany('OtherModelName')
            ocol = OneToMany('ModelName')
    '''
    __slots__ = '_model _attr _ftable _required _unique _index _prefix _suffix _keygen _column'.split()
    def __init__(self, ftable, column=None):
        if column in ON_DELETE or column is NO_ACTION_DEFAULT:
            raise ColumnError("OneToMany lost its on_delete argument - pass it to the ManyToOne instead")
        self._ftable = ftable
        self._required = self._unique = self._index = self._prefix = self._suffix = False
        self._model = self._attr = self._keygen = None
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
            if isinstance(col, (ManyToOne, OneToOne)) and col._ftable == self._model:
                return model.get_by(**{attr: getattr(obj, obj._pkey)})

        raise ORMError("Reverse ManyToOne or OneToOne relationship not found for %s.%s -> %s"%(self._model, self._attr, self._ftable))

    def __delete__(self, obj):
        raise InvalidOperation("Cannot delete OneToMany relationships")

COLUMN_TYPES = [v for v in globals().values() if isinstance(v, type) and issubclass(v, Column)]
__all__ = [v.__name__ for v in COLUMN_TYPES] + 'MODELS MODELS_REFERENCED ON_DELETE'.split()
