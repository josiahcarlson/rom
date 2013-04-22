
__all__ = '''
    ORMError UniqueKeyViolation InvalidOperation
    QueryError ColumnError MissingColumn
    InvalidColumnValue'''.split()

class ORMError(Exception):
    'Base class for all ORM-related errors'

class UniqueKeyViolation(ORMError):
    'Raised when trying to save an entity without a distinct column value'

class InvalidOperation(ORMError):
    'Raised when trying to delete or modify a column that cannot be deleted or modified'

class QueryError(InvalidOperation):
    'Raised when arguments to ``Model.get_by()`` or ``Query.filter`` are not valid'

class ColumnError(ORMError):
    'Raised when your column definitions are not kosher'

class MissingColumn(ColumnError):
    'Raised when a model has a required column, but it is not provided on construction'

class InvalidColumnValue(ColumnError):
    'Raised when you attempt to pass a primary key on entity creation or when data assigned to a column is the wrong type'
