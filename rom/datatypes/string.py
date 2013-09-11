from .column import *

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

