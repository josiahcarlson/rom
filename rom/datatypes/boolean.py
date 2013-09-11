from .column import *
from ..util import (_boolean_keygen)
__all__ = ['Boolean']
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