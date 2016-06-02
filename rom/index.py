
'''
Rom - the Redis object mapper for Python

Copyright 2013-2016 Josiah Carlson

Released under the LGPL license version 2.1 and version 3 (you can choose
which you'd like to be bound under).
'''

from collections import namedtuple
import json
import re
import uuid

import six

from .exceptions import QueryError
from .util import _prefix_score, _script_load, _to_score

_skip = None
_skip = set(globals()) - set(['__doc__'])

Prefix = namedtuple('Prefix', 'attr prefix')
Suffix = namedtuple('Suffix', 'attr suffix')
Pattern = namedtuple('Pattern', 'attr pattern')
Geofilter = namedtuple('Geo', 'name lon lat radius measure count')

GeoIndex = namedtuple('GeoIndex', 'name callback')

SPECIAL = re.compile('([-().%[^$])')
def _pattern_to_lua_pattern(pat):
    if isinstance(pat, six.string_types) and not isinstance(pat, str):
        # XXX: Decode only py2k unicode. Why can't we run the unicode
        # pattern through the re? -JM
        pat = pat.encode('utf-8')
    # We use '-' instead of '*' to get the shortest matches possible, which is
    # usually the desired case for pattern matching.
    return SPECIAL.sub('%\1', pat) \
        .replace('?', '.?') \
        .replace('*', '.-') \
        .replace('+', '.+') \
        .replace('!', '.')

def _find_prefix(pat):
    pat = SPECIAL.sub('%\1', pat)
    if isinstance(pat, six.string_types) and not isinstance(pat, str):
        # XXX: Decode only py2k unicode. Why can't we run the unicode
        # pattern through the re? -JM
        pat = pat.encode('utf-8')
    x = []
    for i in pat:
        if i in '?*+!':
            break
        x.append(i)
    return ''.join(x[:7])

MAX_PREFIX_SCORE = _prefix_score(7*'\xff', True)
def _start_end(prefix):
    return _prefix_score(prefix), (_prefix_score(prefix, True) if prefix else MAX_PREFIX_SCORE)

class GeneralIndex(object):
    '''
    This class implements general indexing and search for the ``rom`` package.

    .. warning:: You probably don't want to be calling this directly. Instead,
      you should rely on the ``Query`` object returned from ``Model.query`` to
      handle all of your query pre-processing.

    Generally speaking, numeric indices use ZSETs, and text indices use SETs
    built using an 'inverted index'.

    Say that we have words ``hello world`` in a column ``c`` on a model with
    primary key ``MyModel:1``. The member ``1`` will be added to SETs with
    keys::

        MyModel:c:hello
        MyModel:c:world

    Text searching performs a sequence of intersections of SETs for the words
    to be searched for.

    Numeric range searching performs a sequence of intersections of ZSETs,
    removing items outside the requested range after each intersection.

    Searches will pre-sort intersections from smallest to largest SET/ZSET
    prior to performing the search to improve performance.

    Prefix, suffix, and pattern matching change this operation. Given a key
    generated of ``hello`` on a column ``c`` on a model with primary key
    ``MyModel:1``, the member ``hello\\01`` with score 0 will be added to a
    ZSET with the key name ``MyModel:c:pre`` for the prefix/pattern index.
    On a suffix index, the member ``olleh\\01`` with score 0 will be added to
    a ZSET with the key name ``MyModel:c:suf``.

    Prefix and suffix matches are excuted in Lua with a variant of the
    autocomplete method described in Redis in Action. These methods ensure a
    runtime proportional to the number of matched entries.

    Pattern matching also uses a Lua script to scan over data in the prefix
    index, exploiting prefixes in patterns if they exist.

    '''
    def __init__(self, namespace):
        self.namespace = namespace

    def _prepare(self, conn, filters):
        temp_id = "%s:%s"%(self.namespace, uuid.uuid4())
        pipe = conn.pipeline(True)
        sfilters = filters
        sizes = [(None, 0)]
        if filters:
            # reorder filters based on the size of the underlying set/zset
            for fltr in filters:
                if isinstance(fltr, six.string_types):
                    estimate_work_lua(pipe, '%s:%s:idx'%(self.namespace, fltr), None)
                elif isinstance(fltr, Prefix):
                    estimate_work_lua(pipe, '%s:%s:pre'%(self.namespace, fltr.attr), fltr.prefix)
                elif isinstance(fltr, Suffix):
                    estimate_work_lua(pipe, '%s:%s:suf'%(self.namespace, fltr.attr), fltr.suffix)
                elif isinstance(fltr, Pattern):
                    estimate_work_lua(pipe, '%s:%s:pre'%(self.namespace, fltr.attr), _find_prefix(fltr.pattern))
                elif isinstance(fltr, list):
                    estimate_work_lua(pipe, '%s:%s:idx'%(self.namespace, fltr[0]), None)
                elif isinstance(fltr, Geofilter):
                    estimate_work_lua(pipe, '%s:%s:geo'%(self.namespace, fltr.name), fltr.count)
                elif isinstance(fltr, tuple):
                    estimate_work_lua(pipe, '%s:%s:idx'%(self.namespace, fltr[0]), fltr[1:3])
                else:
                    raise QueryError("Don't know how to handle a filter of: %r"%(fltr,))
            sizes = list(enumerate(pipe.execute()))
            sizes.sort(key=lambda x:abs(x[1]))
            sfilters = [filters[x[0]] for x in sizes]

        # the first "intersection" is actually a union to get us started, unless
        # we can explicitly create a sub-range in Lua for a fast start to
        # intersection
        intersect = pipe.zunionstore
        first = True
        for ii, fltr in enumerate(sfilters):
            if isinstance(fltr, list):
                # or string string/tag search
                if len(fltr) == 1:
                    # only 1? Use the simple version.
                    fltr = fltr[0]
                elif not fltr:
                    continue
                else:
                    temp_id2 = str(uuid.uuid4())
                    pipe.zunionstore(temp_id2, dict(
                        ('%s:%s:idx'%(self.namespace, fi), 0) for fi in fltr))
                    intersect(temp_id, {temp_id: 0, temp_id2: 0})
                    pipe.delete(temp_id2)
            if isinstance(fltr, six.string_types):
                # simple string/tag search
                intersect(temp_id, {temp_id:0, '%s:%s:idx'%(self.namespace, fltr):0})
            elif isinstance(fltr, Prefix):
                redis_prefix_lua(pipe, temp_id, '%s:%s:pre'%(self.namespace, fltr.attr), fltr.prefix, first)
            elif isinstance(fltr, Suffix):
                redis_prefix_lua(pipe, temp_id, '%s:%s:suf'%(self.namespace, fltr.attr), fltr.suffix, first)
            elif isinstance(fltr, Pattern):
                redis_prefix_lua(pipe, temp_id,
                    '%s:%s:pre'%(self.namespace, fltr.attr),
                    _find_prefix(fltr.pattern),
                    first, '^' + _pattern_to_lua_pattern(fltr.pattern),
                )
            elif isinstance(fltr, Geofilter):
                # Prep the georadius command
                args = [
                    'georadius', '%s:%s:geo'%(self.namespace, fltr.name),
                    repr(fltr.lon), repr(fltr.lat), fltr.radius, fltr.measure
                ]
                if fltr.count and fltr.count >= 0:
                    args.append('COUNT')
                    args.append(fltr.count)
                args.append('STOREDIST')
                first = intersect == pipe.zunionstore
                args.append(temp_id if first else str(uuid.uuid4()))

                pipe.pipeline_execute_command(*args)
                if not first:
                    intersect(temp_id, {temp_id: 0, args[-1]: 1})
                    pipe.delete(args[-1])

            elif isinstance(fltr, tuple):
                # zset range search
                if len(fltr) != 3:
                    raise QueryError("Cannot filter range of data without 2 endpoints (%s given)"%(len(fltr)-1,))
                fltr, mi, ma = fltr
                if not ii and sizes[0][1] < 0:
                    # We've got a special case where we want to explicitly extract
                    # a subrange instead of starting from a larger index, because
                    # it turns out that this is going to be faster :P
                    lua_subrange(pipe, [temp_id, '%s:%s:idx'%(self.namespace, fltr)],
                        ['-inf' if mi is None else _to_score(mi), 'inf' if ma is None else _to_score(ma)]
                    )

                else:
                    intersect(temp_id, {temp_id:0, '%s:%s:idx'%(self.namespace, fltr):1})
                    if mi is not None:
                        pipe.zremrangebyscore(temp_id, '-inf', _to_score(mi, True))
                    if ma is not None:
                        pipe.zremrangebyscore(temp_id, _to_score(ma, True), 'inf')
            first = False
            intersect = pipe.zinterstore
        return pipe, intersect, temp_id

    def search(self, conn, filters, order_by, offset=None, count=None, timeout=None):
        '''
        Search for model ids that match the provided filters.

        Arguments:

            * *filters* - A list of filters that apply to the search of one of
              the following two forms:

                1. ``'column:string'`` - a plain string will match a word in a
                   text search on the column

                .. note:: Read the documentation about the ``Query`` object
                  for what is actually passed during text search

                2. ``('column', min, max)`` - a numeric column range search,
                   between min and max (inclusive by default)

                .. note:: Read the documentation about the ``Query`` object
                  for information about open-ended ranges

                3. ``['column:string1', 'column:string2']`` - will match any
                   of the provided words in a text search on the column

                4. ``Prefix('column', 'prefix')`` - will match prefixes of
                   words in a text search on the column

                5. ``Suffix('column', 'suffix')`` - will match suffixes of
                   words in a text search on the column

                6. ``Pattern('column', 'pattern')`` - will match patterns over
                   words in a text search on the column

            * *order_by* - A string that names the numeric column by which to
              sort the results by. Prefixing with '-' will return results in
              descending order

            .. note:: While you can technically pass a non-numeric index as an
              *order_by* clause, the results will basically be to order the
              results by string comparison of the ids (10 will come before 2).

            .. note:: If you omit the ``order_by`` argument, results will be
              ordered by the last filter. If the last filter was a text
              filter, see the previous note. If the last filter was numeric,
              then results will be ordered by that result.

            * *offset* - A numeric starting offset for results
            * *count* - The maximum number of results to return from the query
        '''
        # prepare the filters
        pipe, intersect, temp_id = self._prepare(conn, filters)

        # handle ordering
        if order_by:
            reverse = order_by and order_by.startswith('-')
            order_clause = '%s:%s:idx'%(self.namespace, order_by.lstrip('-'))
            intersect(temp_id, {temp_id:0, order_clause: -1 if reverse else 1})

        # handle returning the temporary result key
        if timeout is not None:
            pipe.expire(temp_id, timeout)
            pipe.execute()
            return temp_id

        offset = offset if offset is not None else 0
        end = (offset + count - 1) if count and count > 0 else -1
        pipe.zrange(temp_id, offset, end)
        pipe.delete(temp_id)
        return pipe.execute()[-2]

    def count(self, conn, filters):
        '''
        Returns the count of the items that match the provided filters.

        For the meaning of what the ``filters`` argument means, see the
        ``.search()`` method docs.
        '''
        pipe, intersect, temp_id = self._prepare(conn, filters)
        pipe.zcard(temp_id)
        pipe.delete(temp_id)
        return pipe.execute()[-2]

_redis_prefix_lua = _script_load('''
-- first unpack most of our passed variables
local dest = KEYS[1]
local tkey = KEYS[2]
local idx = KEYS[3]

local start_score = ARGV[1]
local end_score = ARGV[2]
local prefix = ARGV[3]
local psize = #prefix
local is_first = tonumber(ARGV[5])

-- find the start offset of our matching
local start_index = 0
if psize > 0 then
    -- All entries end with a null and the id, so we can go before all of them
    -- by just adding a null.
    local pfix = prefix .. '\\0'
    redis.call('ZADD', idx, start_score, pfix)
    start_index = tonumber(redis.call('ZRANK', idx, pfix))
    redis.call('ZREM', idx, pfix)
else
    local start_member = redis.call('ZRANGEBYSCORE', idx, start_score, 'inf', 'limit', 0, 1)
    if #start_member == 1 then
        start_index = tonumber(redis.call('ZRANK', idx, start_member[1]))
    end
end

-- Find the end offset of our matching. We don't bother with the prefix-based
-- ZADD/ZREM pair here because we do an endpoint check every 100 items or so,
--
local end_member = redis.call('ZREVRANGEBYSCORE', idx, '('..end_score, '-inf', 'limit', 0, 1)
local end_index = 0
if #end_member == 1 then
    end_index = tonumber(redis.call('ZRANK', idx, end_member[1]))
end

-- use functions to check instead of embedding an if inside the core loop
local check_match
if tonumber(ARGV[4]) > 0 then
    check_match = function(v, pattern) return string.match(v, pattern) end
else
    check_match = function(v, prefix) return string.sub(v, 1, psize) == prefix end
end

local matched = 0
local found_match = function(v)
    local endv = #v
    while string.sub(v, endv, endv) ~= '\\0' do
        endv = endv - 1
    end
    return redis.call('ZADD', tkey, 0, string.sub(v, endv+1, #v))
end

-- core matching loop
local has_prefix = psize > 0 and tonumber(ARGV[4]) == 0
for i=start_index,end_index,100 do
    local data = redis.call('ZRANGE', idx, i, i+99)
    local last
    for j, v in ipairs(data) do
        if check_match(v, prefix) then
            matched = matched + tonumber(found_match(v))
        end
        last = v
    end
    -- bail early if we've passed all of the shared prefixes
    if not last then
    elseif has_prefix and string.sub(last, 1, psize) > prefix then
        break
    end
end

if is_first > 0 then
    if matched > 0 then
        redis.call('RENAME', tkey, dest)
    end
else
    matched = redis.call('ZINTERSTORE', dest, 2, tkey, dest, 'WEIGHTS', 1, 0)
    redis.call('DEL', tkey)
end

return matched
''')

def redis_prefix_lua(conn, dest, index, prefix, is_first, pattern=None):
    '''
    Performs the actual prefix, suffix, and pattern match operations. 
    '''
    tkey = '%s:%s'%(index.partition(':')[0], uuid.uuid4())
    start, end = _start_end(prefix)
    return _redis_prefix_lua(conn,
        [dest, tkey, index],
        [start, end, pattern or prefix, int(pattern is not None), int(bool(is_first))]
    )

lua_subrange = _script_load('''
-- KEYS - {dest_key, source_key}
-- ARGV - {start_value, end_value}

local idx = KEYS[2]

local start_member = redis.call('ZRANGEBYSCORE', idx, ARGV[1], 'inf', 'limit', 0, 1)
local start_index = 0
if #start_member == 1 then
    start_index = tonumber(redis.call('ZRANK', idx, start_member[1]))
end

local end_member = redis.call('ZREVRANGEBYSCORE', idx, ARGV[2], '-inf', 'limit', 0, 1)
local end_index
if #end_member == 1 then
    end_index = tonumber(redis.call('ZRANK', idx, end_member[1]))
else
    end_index = tonumber(redis.call('ZCARD', idx))
end

for i=start_index, end_index, 100 do
    local members = redis.call('ZRANGE', idx, i, math.min(i+99, end_index), 'withscores')
    for j=1, #members, 2 do
        members[j], members[j+1] = members[j+1], members[j]
    end
    redis.call('ZADD', KEYS[1], unpack(members))
end
return math.max(0, end_index - start_index + 1)
''')

_estimate_work_lua = _script_load('''
-- These indexes will be on numbers, strings, and/or prefixes. We'll check set
-- and sorted set cardinality, as well as range size.
local idx = KEYS[1]

-- redis.call('type') returns a table {"ok":<type>} ... looks like a bug, so use
-- redis.pcall() instead.
local typ = redis.pcall('TYPE', idx).ok
if typ == 'set' then
    return tonumber(redis.call('scard', idx))
elseif typ == 'zset' then
    local size = tonumber(redis.call('zcard', idx))

    if string.sub(idx, -4) == ':geo' then
        if #ARGV > 0 then
            return math.min(size, tonumber(ARGV[1]))
        end
        return size

    elseif #ARGV == 2 then
        local start_member = redis.call('ZRANGEBYSCORE', idx, ARGV[1], 'inf', 'limit', 0, 1)
        local start_index = 0
        if #start_member == 1 then
            start_index = tonumber(redis.call('ZRANK', idx, start_member[1]))
        end

        local end_member = redis.call('ZREVRANGEBYSCORE', idx, ARGV[2], '-inf', 'limit', 0, 1)
        local end_index = -1
        if #end_member == 1 then
            end_index = tonumber(redis.call('ZRANK', idx, end_member[1]))
        end

        local range_size = math.max(0, end_index - start_index + 1)
        -- take into consideration deletions from the full copied zset
        if string.sub(idx, -4) == ':idx' then
            size = size + size - range_size
        end

        -- pulling ranges isn't free, call it 2x as expensive per operation as
        -- the union/delete
        range_size = range_size * 2

        -- If you do the (simple) algebra, this will pick the range variant if
        -- range <= 2/3 * size. We keep the long form here to explain the *why*,
        -- even if a couple multiplies and a compare might be faster.

        if range_size < size then
            return -range_size
        end
    end
    return size
end
return 0
''')

def estimate_work_lua(conn, index, prefix):
    '''
    Estimates the total work necessary to calculate the prefix match over the
    given index with the provided prefix.
    '''
    if index.endswith(':idx'):
        args = [] if not prefix else list(prefix)
        if args:
            args[0] = '-inf' if args[0] is None else repr(float(args[0]))
            args[1] = 'inf' if args[1] is None else repr(float(args[1]))
        return _estimate_work_lua(conn, [index], args, force_eval=True)
    elif index.endswith(':geo'):
        return _estimate_work_lua(conn, [index], filter(None, [prefix]), force_eval=True)

    start, end = _start_end(prefix)
    return _estimate_work_lua(conn, [index], [start, '(' + end], force_eval=True)

__all__ = [k for k, v in globals().items() if getattr(v, '__doc__', None) and k not in _skip]
