import threading
import weakref
from .exceptions import ORMError

__all__ = ['session']



class Session(threading.local):
    '''
    This is a very dumb session. All it tries to do is to keep a cache of
    loaded entities, offering the ability to call ``.save()`` on modified (or
    all) entities with ``.flush()`` or ``.commit()``.

    This is exposed via the ``session`` global variable, which is available
    when you ``import rom`` as ``rom.session``.

    .. note: calling ``.flush()`` or ``.commit()`` doesn't cause all objects
        to be written simultanously. They are written one-by-one, with any
        error causing the call to fail.
    '''
    def _init(self):
        try:
            self.known
        except AttributeError:
            self.known = {}
            self.wknown = weakref.WeakValueDictionary()

    def add(self, obj):
        '''
        Adds an entity to the session.
        '''
        self._init()
        self.known[obj._pk] = obj
        self.wknown[obj._pk] = obj

    def forget(self, obj):
        '''
        Forgets about an entity (automatically called when an entity is
        deleted). Call this to ensure that an entity that you've modified is
        not automatically saved on ``session.commit()`` .
        '''
        self._init()
        self.known.pop(obj._pk, None)
        self.wknown.pop(obj._pk, None)

    def get(self, pk):
        '''
        Fetches an entity from the session based on primary key.
        '''
        self._init()
        return self.known.get(pk) or self.wknown.get(pk)

    def rollback(self):
        '''
        Forget about all entities in the session (``.commit()`` will do
        nothing).
        '''
        self.known = {}
        self.wknown = weakref.WeakValueDictionary()

    def flush(self, full=False, all=False):
        '''
        Call ``.save()`` on all modified entities in the session. Use when you
        want to flush changes to Redis, but don't want to lose your local
        session cache.

        See the ``.commit()`` method for arguments and their meanings.
        '''
        self._init()
        changes = 0
        for value in self.known.values():
            if not value._deleted and (all or value._modified):
                changes += value.save(full)
        return changes

    def commit(self, full=False, all=False):
        '''
        Call ``.save()`` on all modified entities in the session. Also forgets
        all known entities in the session, so this should only be called at
        the end of a request.

        Arguments:

            * *full* - pass ``True`` to force save full entities, not only
              changes
            * *all* - pass ``True`` to save all entities known, not only those
              entities that have been modified.
        '''
        changes = self.flush(full, all)
        self.known = {}
        return changes

    def save(self, *objects, **kwargs):
        '''
        This method an alternate API for saving many entities (possibly not
        tracked by the session). You can call::

            session.save(obj)
            session.save(obj1, obj2, ...)
            session.save([obj1, obj2, ...])

        And the entities will be flushed to Redis.

        You can pass the keyword arguments ``full`` and ``all`` with the same
        meaning and semantics as the ``.commit()`` method.
        '''
        from rom import Model
        full = kwargs.get('full')
        all = kwargs.get('all')
        changes = 0
        for o in objects:
            if isinstance(o, (list, tuple)):
                changes += self.save(*o, full=full, all=all)
            elif isinstance(o, Model):
                if not o._deleted and (all or o._modified):
                    changes += o.save(full)
            else:
                raise ORMError(
                    "Cannot save an object that is not an instance of a Model (you provided %r)"%(
                        o,))
        return changes

session = Session()