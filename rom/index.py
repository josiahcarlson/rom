
from collections import namedtuple
import json
import re
import uuid

import six

from .exceptions import QueryError
from .util import _prefix_score, _script_load, _to_score

Prefix = namedtuple('Prefix', 'attr prefix')
Suffix = namedtuple('Suffix', 'attr suffix')
Pattern = namedtuple('Pattern', 'attr pattern')

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

    .. warning: You probably don't want to be calling this directly. Instead,
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

    def _unindex(self, conn, pipe, id):
        known = conn.hget(self.namespace + '::', id)
        if not known:
            return 0
        old = json.loads(known.decode())
        keys, scored = old[:2]
        pre, suf = ([],[]) if len(old) == 2 else old[2:]

        for key in keys:
            pipe.srem('%s:%s:idx'%(self.namespace, key), id)
        for key in scored:
            pipe.zrem('%s:%s:idx'%(self.namespace, key), id)
        for attr, key in pre:
            pipe.zrem('%s:%s:pre'%(self.namespace, attr), '%s\0%s'%(key, id))
        for attr, key in suf:
            pipe.zrem('%s:%s:suf'%(self.namespace, attr), '%s\0%s'%(key, id))

        pipe.hdel(self.namespace + '::', id)
        return len(keys) + len(scored)

    def unindex(self, conn, id):
        '''
        Will unindex an entity atomically.

        Arguments:

            * *id* - the id of the entity to remove from the index
        '''
        pipe = conn.pipeline(True)
        ret = self._unindex(conn, pipe, id)
        pipe.execute()
        return ret

    def index(self, conn, id, keys, scores, prefix, suffix, pipe=None):
        '''
        Will index the provided data atomically.

        Arguments:

            * *id* - the id of the entity that is being indexed
            * *keys* - an iterable sequence of keys of the form:
              ``column_name:key`` to index
            * *scores* - a dictionary mapping ``column_name`` to numeric
              scores and/or mapping ``column_name:key`` to numeric scores
            * *prefix* - an iterable sequence of strings that will be used for
              prefix matching
            * *suffix* - an iterable sequence of strings that will be used for
              suffix matching (each string is likely to be a reversed version
              of *pre*, but this is not required)

        This will automatically unindex the provided id before
        indexing/re-indexing.

        Unindexing is possible because we keep a record of all keys, score
        keys, pre, and suf lists that were provided.
        '''
        had_pipe = bool(pipe)
        pipe = pipe or conn.pipeline(True)
        self._unindex(conn, pipe, id)

        for key in keys:
            pipe.sadd('%s:%s:idx'%(self.namespace, key), id)
        for key, score in scores.items():
            pipe.zadd('%s:%s:idx'%(self.namespace, key), id, _to_score(score))
        for attr, key in prefix:
            pipe.zadd('%s:%s:pre'%(self.namespace, attr), '%s\0%s'%(key, id), _prefix_score(key))
        for attr, key in suffix:
            pipe.zadd('%s:%s:suf'%(self.namespace, attr), '%s\0%s'%(key, id), _prefix_score(key))
        pipe.hset(self.namespace + '::', id, json.dumps([list(keys), list(scores), list(prefix), list(suffix)]))
        if not had_pipe:
            pipe.execute()
        return len(keys) + len(scores) + len(prefix) + len(suffix)

    def _prepare(self, conn, filters):
        temp_id = "%s:%s"%(self.namespace, uuid.uuid4())
        pipe = conn.pipeline(True)
        sfilters = filters
        if len(filters) > 1:
            # reorder filters based on the size of the underlying set/zset
            for fltr in filters:
                if isinstance(fltr, six.string_types):
                    pipe.scard('%s:%s:idx'%(self.namespace, fltr))
                elif isinstance(fltr, Prefix):
                    estimate_work_lua(pipe, '%s:%s:pre'%(self.namespace, fltr.attr), fltr.prefix)
                elif isinstance(fltr, Suffix):
                    estimate_work_lua(pipe, '%s:%s:suf'%(self.namespace, fltr.attr), fltr.suffix)
                elif isinstance(fltr, Pattern):
                    estimate_work_lua(pipe, '%s:%s:pre'%(self.namespace, fltr.attr), _find_prefix(fltr.pattern))
                elif isinstance(fltr, (tuple, list)):
                    pipe.zcard('%s:%s:idx'%(self.namespace, fltr[0]))
                else:
                    raise QueryError("Don't know how to handle a filter of: %r"%(fltr,))
            sizes = list(enumerate(pipe.execute()))
            sizes.sort(key=lambda x:x[1])
            sfilters = [filters[x[0]] for x in sizes]

        # the first "intersection" is actually a union to get us started
        intersect = pipe.zunionstore
        first = True
        for fltr in sfilters:
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
                    intersect(temp_id, {temp_id:0, temp_id2:0})
                    pipe.delete(temp_id2)
            if isinstance(fltr, six.string_types):
                # simple string/tag search
                intersect(temp_id, {temp_id:0, '%s:%s:idx'%(self.namespace, fltr):0})
            elif isinstance(fltr, Prefix):
                redis_prefix_lua(pipe, temp_id, '%s:%s:pre'%(self.namespace, fltr.attr), fltr.prefix, first)
            elif isinstance(fltr, Suffix):
                redis_prefix_lua(pipe, temp_id, '%s:%s:suf'%(self.namespace, fltr.attr), fltr.suffix, first)
            elif isinstance(fltr, Pattern):
                redis_prefix_lua(conn, temp_id,
                    '%s:%s:pre'%(self.namespace, fltr.attr),
                    _find_prefix(fltr.pattern),
                    first, '^' + _pattern_to_lua_pattern(fltr.pattern),
                )
            elif isinstance(fltr, tuple):
                # zset range search
                if len(fltr) != 3:
                    raise QueryError("Cannot filter range of data without 2 endpoints (%s given)"%(len(fltr)-1,))
                fltr, mi, ma = fltr
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

                .. note: Read the documentation about the ``Query`` object
                  for what is actually passed during text search

                2. ``('column', min, max)`` - a numeric column range search,
                   between min and max (inclusive by default)

                .. note: Read the documentation about the ``Query`` object
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

            .. note: While you can technically pass a non-numeric index as an
              *order_by* clause, the results will basically be to order the
              results by string comparison of the ids (10 will come before 2).

            .. note: If you omit the ``order_by`` argument, results will be
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
    if has_prefix and string.sub(last, 1, psize) > prefix then
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

_estimate_work_lua = _script_load('''
-- We could use the ZADD/ZREM stuff from our prefix searching if we have a
-- prefix, but this is only an estimate, and it doesn't make sense to modify
-- an index just to get a better estimate.
local idx = KEYS[1]

local start_score = ARGV[1]
local end_score = ARGV[2]

local start_member = redis.call('ZRANGEBYSCORE', idx, start_score, 'inf', 'limit', 0, 1)
local start_index = 0
if #start_member == 1 then
    start_index = tonumber(redis.call('ZRANK', idx, start_member[1]))
end

local end_member = redis.call('ZREVRANGEBYSCORE', idx, '('..end_score, '-inf', 'limit', 0, 1)
local end_index = -1
if #end_member == 1 then
    end_index = tonumber(redis.call('ZRANK', idx, end_member[1]))
end

return math.max(0, end_index - start_index + 1)
''')

def estimate_work_lua(conn, index, prefix):
    '''
    Estimates the total work necessary to calculate the prefix match over the
    given index with the provided prefix.
    '''
    start, end = _start_end(prefix)
    return _estimate_work_lua(conn, [index], [start, end], force_eval=True)
