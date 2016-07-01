
'''
Rom - the Redis object mapper for Python

Copyright 2013-2016 Josiah Carlson

Released under the LGPL license version 2.1 and version 3 (you can choose
which you'd like to be bound under).
'''

from datetime import datetime, date, time as dtime
from decimal import Decimal as _Decimal
import json
import warnings
import uuid

import six

from .exceptions import QueryError
from .index import Geofilter, Pattern, Prefix, Suffix
from .util import (_connect, session, dt2ts, t2ts, _script_load,
    STRING_SORT_KEYGENS, STRING_SORT_KEYGENS_STR)

_skip = None
_skip = set(globals()) - set(['__doc__'])

NUMERIC_TYPES = six.integer_types + (float, _Decimal, datetime, date, dtime)

NOT_NULL = (None, None)
_STRING_SORT_KEYGENS = [ss.__name__ for ss in STRING_SORT_KEYGENS]
ALLOWED_DIST = ('m', 'km', 'mi', 'ft')

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

    def _check(self, column, value=None, which='order_by'):
        column = column.strip('-').partition(':')[0]
        col = self._model._columns.get(column)
        if not col:
            raise QueryError("Cannot use '%s' clause on a non-existent column %r"%(which, column))

        if which == 'filter' and column not in self._model._index:
            raise QueryError("Cannot use 'filter' clause on a column defined with 'index=False'")

        if which == 'startswith' and column not in self._model._prefix:
            raise QueryError("Cannot use 'startswith' clause on a column defined with 'prefix=False'")

        if which == 'like' and column not in self._model._prefix:
            raise QueryError("Cannot use 'like' clause on a column defined with 'prefix=False'")

        if which == 'endswith' and column not in self._model._suffix:
            raise QueryError("Cannot use 'endswith' clause on a column defined with 'suffix=False'")

        if value is not None:
            if col._keygen.__name__ in ('FULL_TEXT', 'SIMPLE_CI', 'CASE_INSENSITIVE', 'IDENTITY_CI'):
                value = value.lower()
            return value
        return col

    def replace(self, **kwargs):
        '''
        Copy the Query object, optionally replacing the filters, order_by, or
        limit information on the copy.
        '''
        data = {
            'model': self._model,
            'filters': self._filters,
            'order_by': self._order_by,
            'limit': self._limit,
        }
        data.update(**kwargs)
        return Query(**data)

    def filter(self, **kwargs):
        '''
        Only columns/attributes that have been specified as having an index with
        the ``index=True`` option on the column definition can be filtered with
        this method. Prefix, suffix, and pattern match filters must be provided
        using the ``.startswith()``, ``.endswith()``, and the ``.like()``
        methods on the query object, respectively.

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
                .all()

        If you only want to match a single value as part of your range query,
        you can pass an integer, float, or Decimal object by itself, similar
        to the ``Model.get_by()`` method::

            results = MyModel.query \\
                .filter(ncol=5) \\
                .execute()

        .. note:: Trying to use a range query `attribute=(min, max)` on indexed
            string columns won't return any results.
        .. note:: This method only filters columns that have been defined with
            ``index=True``.

        '''
        cur_filters = list(self._filters)
        for attr, value in kwargs.items():
            self._check(attr, which='filter')
            if isinstance(value, bool):
                value = str(bool(value))

            if isinstance(value, NUMERIC_TYPES):
                # for simple numeric equiality filters
                value = (value, value)

            if isinstance(value, six.string_types):
                cur_filters.append('%s:%s'%(attr, value))

            elif six.PY3 and isinstance(value, bytes):
                cur_filters.append('%s:%s'%(attr, value.decode('latin-1')))

            elif isinstance(value, tuple):
                if value is NOT_NULL:
                    from .columns import OneToOne, ManyToOne
                    ctype = type(self._model._columns[attr])
                    if not issubclass(ctype, (OneToOne, ManyToOne)):
                        raise QueryError("Can only query for non-null column values " \
                            "on OneToOne or ManyToOne columns, %r is of type %r"%(attr, ctype))

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
        return self.replace(filters=tuple(cur_filters))

    def startswith(self, **kwargs):
        '''
        When provided with keyword arguments of the form ``col=prefix``, this
        will limit the entities returned to those that have a word with the
        provided prefix in the specified column(s). This requires that the
        ``prefix=True`` option was provided during column definition.

        Usage::

            User.query.startswith(email='user@').execute()

        '''
        new = []
        for k, v in kwargs.items():
            v = self._check(k, v, 'startswith')
            new.append(Prefix(k, v))
        return self.replace(filters=self._filters+tuple(new))

    def endswith(self, **kwargs):
        '''
        When provided with keyword arguments of the form ``col=suffix``, this
        will limit the entities returned to those that have a word with the
        provided suffix in the specified column(s). This requires that the
        ``suffix=True`` option was provided during column definition.

        Usage::

            User.query.endswith(email='@gmail.com').execute()

        '''
        new = []
        for k, v in kwargs.items():
            v = self._check(k, v, 'endswith')
            new.append(Suffix(k, v[::-1]))
        return self.replace(filters=self._filters+tuple(new))

    def like(self, **kwargs):
        '''
        When provided with keyword arguments of the form ``col=pattern``, this
        will limit the entities returned to those that include the provided
        pattern. Note that 'like' queries require that the ``prefix=True``
        option must have been provided as part of the column definition.

        Patterns allow for 4 wildcard characters, whose semantics are as
        follows:

            * *?* - will match 0 or 1 of any character
            * *\** - will match 0 or more of any character
            * *+* - will match 1 or more of any character
            * *!* - will match exactly 1 of any character

        As an example, imagine that you have enabled the required prefix
        matching on your ``User.email`` column. And lets say that you want to
        find everyone with an email address that contains the name 'frank'
        before the ``@`` sign. You can use either of the following patterns
        to discover those users.

            * *\*frank\*@*
            * *\*frank\*@*

        .. note:: Like queries implicitly start at the beginning of strings
          checked, so if you want to match a pattern that doesn't start at
          the beginning of a string, you should prefix it with one of the
          wildcard characters (like ``*`` as we did with the 'frank' pattern).
        '''
        new = []
        for k, v in kwargs.items():
            v = self._check(k, v, 'like')
            new.append(Pattern(k, v))
        return self.replace(filters=self._filters+tuple(new))

    def near(self, name, lon, lat, distance, measure, count=None):
        if name not in self._model._geo:
            raise ValueError("provided index name must be defined as a geo index")

        measure = measure.lower()
        if measure not in ALLOWED_DIST:
            raise ValueError("distance measure must be one of %r"%(ALLOWED_DIST,))

        return self.replace(filters=self._filters + (Geofilter(name, lon, lat, distance, measure, count),))

    def order_by(self, column):
        '''
        When provided with a column name, will sort the results of your query::

            # returns all users, ordered by the created_at column in
            # descending order
            User.query.order_by('-created_at').execute()
        '''
        cname = column.lstrip('-')
        col = self._check(cname)
        if type(col).__name__ in ('String', 'Text', 'Json') and col._keygen.__name__ not in _STRING_SORT_KEYGENS:
            warnings.warn("You are trying to order by a non-numeric column %r. "
                          "Unless you have provided your own keygen or are using "
                          "one of the sortable keygens: (%s), this probably won't "
                          "work the way you expect it to."%(cname, STRING_SORT_KEYGENS_STR),
                          stacklevel=2)

        return self.replace(order_by=column)

    def limit(self, offset, count):
        '''
        Will limit the number of results returned from a query::

            # returns the most recent 25 users
            User.query.order_by('-created_at').limit(0, 25).execute()
        '''
        return self.replace(limit=(offset, count))

    def count(self):
        '''
        Will return the total count of the objects that match the specified
        filters.::

            # counts the number of users created in the last 24 hours
            User.query.filter(created_at=(time.time()-86400, time.time())).count()
        '''
        filters = self._filters
        if self._order_by:
            filters += (self._order_by.lstrip('-'),)
        if not filters:
            # We can actually count entities here...
            size = _connect(self._model).hlen(self._model._namespace + '::')
            limit = self._limit or (0, 2**64)
            size = max(size - max(limit[0], 0), 0)
            return min(size, limit[1])

        return self._model._gindex.count(_connect(self._model), filters)

    def _search(self):
        if not (self._filters or self._order_by):
            raise QueryError("You are missing filter or order criteria")
        limit = () if not self._limit else self._limit
        return self._model._gindex.search(
            _connect(self._model), self._filters, self._order_by, *limit)

    def iter_result(self, timeout=30, pagesize=100, no_hscan=False):
        '''
        Iterate over the results of your query instead of getting them all with
        `.all()`. Will only perform a single query. If you expect that your
        processing will take more than 30 seconds to process 100 items, you
        should pass `timeout` and `pagesize` to reflect an appropriate timeout
        and page size to fetch at once.

        Usage::

            for user in User.query.endswith(email='@gmail.com').iter_result():
                # do something with user
                ...

        '''

        if not self._filters and not self._order_by:
            if self._model._columns[self._model._pkey]._index:
                return self._iter_all_pkey()
            conn = _connect(self._model)
            version = list(map(int, conn.info()['redis_version'].split('.')[:2]))
            if version >= [2,8] and not no_hscan:
                return self._iter_all_hscan()
            return self._iter_all()
        return self._iter_results(timeout, pagesize)

    def _iter_results(self, timeout=30, pagesize=100):
        conn = _connect(self._model)
        limit = self._limit or (0, 2**64)
        start = max(limit[0], 0)
        key = self.cached_result(timeout)

        remaining = limit[1]
        ids = [None]
        i = start
        while ids and remaining > 0:
            # refresh the key
            conn.expire(key, timeout)
            ids = conn.zrange(key, i, i+min(remaining, pagesize)-1)
            i += len(ids)
            # No need to fill up memory with paginated items hanging around the
            # session. Remove all entities from the session that are not
            # already modified (were already in the session and modified).
            for ent in self._model.get(ids):
                if not ent._modified:
                    session.forget(ent)
                yield ent
                remaining -= 1

    def _iter_all(self):
        conn = _connect(self._model)
        limit = self._limit or (0, 2**64)
        start = max(limit[0], 0)
        prefix = '%s:'%self._model._namespace
        max_id = int(conn.get('%s%s:'%(prefix, self._model._pkey)) or '0')

        # We could use HSCAN here, except that we may get duplicates
        # as we are iterating. That's not good or expected behavior :/
        remaining = max(limit[1], 0)
        ids = [None]
        i = 1
        while ids and i <= max_id and remaining > 0:
            ids = list(range(i, i + 100))
            i += 100
            for ent in self._model.get(ids):
                # Same session comment as from _iter_results()
                if not ent._modified:
                    session.forget(ent)
                if start:
                    start -= 1
                elif remaining > 0:
                    remaining -= 1
                    yield ent

    def _iter_all_hscan(self):
        conn = _connect(self._model)
        limit = self._limit or (0, 2**64)
        start = max(limit[0], 0)
        ns = self._model._namespace + ':'
        tkey = ns + str(uuid.uuid4())
        pkey = self._model._pkey

        remaining = max(limit[1], 0)
        cursor = 0
        ids = ''
        while cursor != '0' and remaining > 0:
            result = _scan_fetch_index_hash(conn, [ns, tkey], [cursor, json.dumps(ids or '')])
            if isinstance(result, six.binary_type):
                result = result.decode('utf-8')
            cursor, data = json.loads(result)

            ids = []
            for mdata in data:
                # Turn the flattened data into a dict so we can instantiate the
                # result and/or fetch the object from the session.
                mdata = iter(mdata)
                mdata = dict(zip(mdata, mdata))
                id = mdata[pkey]
                ids.append(int(id))

                # Try to get the shared entity from the session, even though
                # we just fetched the data from Redis.
                ent = session.get(ns + id)
                if not ent:
                    ent = self._model(_loading=True, **mdata)

                # Same session comment as from _iter_results()
                if not ent._modified:
                    session.forget(ent)
                if start:
                    start -= 1
                elif remaining > 0:
                    remaining -= 1
                    yield ent

    def _iter_all_pkey(self):
        conn = _connect(self._model)
        limit = self._limit or (0, 2**64)
        start = max(limit[0], 0)
        remaining = limit[1]
        index = '%s:%s:idx'%(self._model._namespace, self._model._pkey)
        max_id = int((conn.zrevrange(index, 0, 0) or (0,))[0])
        i = 1
        if start:
            # skip over the offset at the beginning
            i = int((conn.zrange(index, start, start) or (0,))[0])

        while i <= max_id and remaining > 0:
            ids = conn.zrangebyscore(index, i, i+99)
            i += 100
            for ent in self._model.get(ids):
                # Same session comment as from _iter_results()
                if not ent._modified:
                    session.forget(ent)
                if remaining > 0:
                    remaining -= 1
                    yield ent

    def __iter__(self):
        return self.iter_result()

    def cached_result(self, timeout):
        '''
        This will execute the query, returning the key where a ZSET of your
        results will be stored for pagination, further operations, etc.

        The timeout must be a positive integer number of seconds for which to
        set the expiration time on the key (this is to ensure that any cached
        query results are eventually deleted, unless you make the explicit
        step to use the PERSIST command).

        .. note:: Limit clauses are ignored and not passed.

        Usage::

            ukey = User.query.endswith(email='@gmail.com').cached_result(30)
            for i in xrange(0, conn.zcard(ukey), 100):
                # refresh the expiration
                conn.expire(ukey, 30)
                users = User.get(conn.zrange(ukey, i, i+99))
                ...
        '''
        if not (self._filters or self._order_by):
            raise QueryError("You are missing filter or order criteria")
        timeout = int(timeout)
        if timeout < 1:
            raise QueryError("You must specify a timeout >= 1, you gave %r"%timeout)
        return self._model._gindex.search(
            _connect(self._model), self._filters, self._order_by, timeout=timeout)

    def execute(self):
        '''
        Actually executes the query, returning any entities that match the
        filters, ordered by the specified ordering (if any), limited by any
        earlier limit calls.
        '''
        if not self._filters and not self._order_by:
            return list(self)
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
        if not self._filters and not self._order_by:
            for ent in self:
                return ent
            return None
        ids = self.limit(*lim)._search()
        if ids:
            return self._model.get(ids[0])
        return None

_scan_fetch_index_hash = _script_load('''
local namespace = KEYS[1]
local hkey = namespace .. ':'
local tkey = KEYS[2]

-- Keep track of ids we've already returned. We need to write here because
-- we can't write after the HSCAN call below.
if #ARGV[2] > 2 then
    redis.call('SADD', KEYS[2], unpack(cjson.decode(ARGV[2])))
end
-- Make sure the temporary set goes away.
redis.call('EXPIRE', KEYS[2], 30)

local results = {}
local pair = redis.call('HSCAN', hkey, ARGV[1])
local cursor = pair[1]
local contents = pair[2]
while #contents > 0 do
    -- ignore the index contents
    table.remove(contents)
    -- get the id
    local id = table.remove(contents)

    if redis.call('SISMEMBER', tkey, id) == 0 then
        local result = redis.call('HGETALL', namespace .. id)
        if #result > 0 then
            table.insert(results, result)
        end
    end
end
return cjson.encode({cursor, results})
''')

__all__ = [k for k, v in globals().items() if getattr(v, '__doc__', None) and k not in _skip]
