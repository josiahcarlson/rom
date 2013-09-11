from ..sharedvars import ( NULL, MODELS, _NUMERIC)

__all__ = ['Column', 'ManyToOne']

from ..exceptions import (ORMError, InvalidOperation, ColumnError, MissingColumn, InvalidColumnValue)
from ..util import( _many_to_one_keygen, _numeric_keygen, _string_keygen, _boolean_keygen )
from ..session import session
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
