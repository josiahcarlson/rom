from .column import (Column, ManyToOne)
from ..exceptions import (ORMError, InvalidOperation)
from ..sharedvars import (NULL, MODELS)

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
