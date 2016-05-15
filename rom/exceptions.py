
__all__ = '''
    ORMError UniqueKeyViolation InvalidOperation
    QueryError ColumnError MissingColumn
    InvalidColumnValue RestrictError DataRaceError EntityDeletedError'''.split()

class ORMError(Exception):
    'Base class for all ORM-related errors'

class UniqueKeyViolation(ORMError):
    'Raised when trying to save an entity without a distinct column value'

class InvalidOperation(ORMError):
    'Raised when trying to delete or modify a column that cannot be deleted or modified'

class QueryError(InvalidOperation):
    'Raised when arguments to ``Model.get_by()`` or ``Query.filter`` are not valid'

class RestrictError(InvalidOperation):
    'Raised when deleting an object referenced by other objects'

class DataRaceError(InvalidOperation):
    'Raised when more than one writer tries to update the same columns on the same entity'

class EntityDeletedError(InvalidOperation):
    # This could be a sub-class of DataRaceError, but the DataRaceError has a
    # possibly different resolution path, and we don't want the order of except
    # clauses to mess up a user's ability to solve their problems.
    'Raised when another writer deleted the entity from Redis; use .save(force=True) to re-save'

class ColumnError(ORMError):
    'Raised when your column definitions are not correct'

class MissingColumn(ColumnError):
    'Raised when a model has a required column, but it is not provided on construction'

class InvalidColumnValue(ColumnError):
    'Raised when you attempt to pass a primary key on entity creation or when data assigned to a column is the wrong type'

class BulkError(ORMError):
    'Raised when using session.commit(fast=True) or equivalent, and there is at least one error.'
