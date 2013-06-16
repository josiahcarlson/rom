
import json
import uuid

from .exceptions import QueryError
from .util import _to_score

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
    '''
    def __init__(self, namespace):
        self.namespace = namespace

    def _unindex(self, conn, pipe, id):
        known = conn.hget(self.namespace + '::', id)
        if not known:
            return 0
        keys, scored = json.loads(known)

        for key in keys:
            pipe.srem('%s:%s:idx'%(self.namespace, key), id)
        for key in scored:
            pipe.zrem('%s:%s:idx'%(self.namespace, key), id)
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

    def index(self, conn, id, keys, scores, pipe=None):
        '''
        Will index the provided data atomically.

        Arguments:

            * *id* - the id of the entity that is being indexed
            * *keys* - an iterable sequence of keys of the form:
              ``column_name:key`` to index
            * *scores* - a dictionary mapping ``column_name`` to numeric
              scores and/or mapping ``column_name:key`` to numeric scores

        This will automatically unindex the provided id before
        indexing/re-indexing.

        Unindexing is possible because we keep a record of all keys and score
        keys that were provided.
        '''
        had_pipe = bool(pipe)
        pipe = pipe or conn.pipeline(True)
        self._unindex(conn, pipe, id)

        for key in keys:
            pipe.sadd('%s:%s:idx'%(self.namespace, key), id)
        for key, score in scores.iteritems():
            pipe.zadd('%s:%s:idx'%(self.namespace, key), id, _to_score(score))
        pipe.hset(self.namespace + '::', id, json.dumps([list(keys), list(scores)]))
        if not had_pipe:
            pipe.execute()
        return len(keys) + len(scores)

    def _prepare(self, conn, filters):
        temp_id = str(uuid.uuid4())
        pipe = conn.pipeline(True)
        # reorder filters based on the size of the underlying set/zset
        for fltr in filters:
            if isinstance(fltr, (str, unicode)):
                pipe.scard(fltr)
            elif isinstance(fltr, tuple):
                pipe.zcard(fltr[0])
            elif isinstance(fltr, list):
                pipe.zcard(fltr[0])
            else:
                raise QueryError("Don't know how to handle a filter of: %r"%(fltr,))
        sizes = list(enumerate(pipe.execute()))
        sizes.sort(key=lambda x:x[1])
        sfilters = [filters[x[0]] for x in sizes]

        # the first "intersection" is actually a union to get us started
        intersect = pipe.zunionstore
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
            if isinstance(fltr, (str, unicode)):
                # simple string/tag search
                intersect(temp_id, {temp_id:0, '%s:%s:idx'%(self.namespace, fltr):0})
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
            intersect = pipe.zinterstore
        return pipe, intersect, temp_id

    def search(self, conn, filters, order_by, offset=None, count=None):
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
                   of the words in a text search on the column

            * *order_by* - A string that names the numeric column by which to
              sort the results by. Prefixing with '-' will return results in
              descending order

            .. note: While you can technically pass a non-numeric index
              entry here, the results will basically be to order the results
              by string comparison of the ids (10 will come before 2).

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
            intersect(temp_id, {temp_id:0, '%s:%s:idx'%(self.namespace, order_by.lstrip('-')): -1 if reverse else 1})
        offset = offset if offset is not None else 0
        end = (offset + count - 1) if count > 0 else -1
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
