from __future__ import print_function
from datetime import datetime, timedelta
from decimal import Decimal as _Decimal
import time
import unittest

import redis
import six

from rom import util

util.CONNECTION = redis.Redis(db=15)
connect = util._connect

from rom import *
from rom import _disable_lua_writes, _enable_lua_writes
from rom.exceptions import *

def global_setup():
    c = connect(None)
    keys = c.keys('RomTest*')
    if keys:
        c.delete(*keys)
    from rom.columns import MODELS
    Model = MODELS['Model']
    for k, v in MODELS.copy().items():
        if v is not Model:
            del MODELS[k]

def get_state():
    c = connect(None)
    data = []
    for k in c.keys('*'):
        k = k.decode() if six.PY3 else k
        t = c.type(k)
        t = t.decode() if six.PY3 else t
        if t == 'string':
            data.append((k, c.get(k)))
        elif t == 'list':
            data.append((k, c.lrange(k, 0, -1)))
        elif t == 'set':
            data.append((k, c.smembers(k)))
        elif t == 'hash':
            data.append((k, c.hgetall(k)))
        else:
            data.append((k, c.zrange(k, 0, -1, withscores=True)))
    data.sort()
    return data

_now = datetime.utcnow()
_now_time = time.time()

def _default_time():
    return _now_time

class TestORM(unittest.TestCase):
    def setUp(self):
        session.rollback()

    # for Python 2.6
    def assertIsNone(self, arg):
        assert arg is None
    def assertIs(self, arg1, arg2):
        assert arg1 is arg2

    def test_basic_model(self):
        class RomTestBasicModel(Model):
            val = Integer()
            oval = Integer(default=7)
            created_at = Float(default=_default_time)
            req = Text(required=True)

        self.assertRaises(ColumnError, RomTestBasicModel)
        self.assertRaises(InvalidColumnValue, lambda: RomTestBasicModel(oval='t', req='X'))
        self.assertRaises(MissingColumn, lambda: RomTestBasicModel(created_at=7))

        # try object saving/loading
        x = RomTestBasicModel(val=1, req="hello")
        x.save()
        id = x.id
        x = x.to_dict()

        y = RomTestBasicModel.get(id)
        yd = y.to_dict()
        ## cax = x.pop('created_at'); cay = yd.pop('created_at')
        self.assertEqual(x, yd)
        ## self.assertTrue(abs(cax - cay) < .005, cax-cay)

        # try object copying
        zd = y.copy().to_dict()
        ## caz = zd.pop('created_at')
        self.assertNotEqual(yd, zd)
        zd.pop('id')
        yd.pop('id')
        self.assertEqual(yd, zd)
        ## self.assertTrue(abs(cay-caz) < .005, cay-caz)

    def test_unique_index(self):
        def foo2():
            class RomTestBadIndexModel2(Model):
                bad = Integer(unique=True)
        self.assertRaises(ColumnError, foo2)

        class RomTestIndexModel(Model):
            key = Text(required=True, unique=True)

        self.assertRaises(MissingColumn, RomTestIndexModel)
        item = RomTestIndexModel(key="hello")
        item.save()

        m = RomTestIndexModel.get_by(key="hello")
        self.assertTrue(m)
        self.assertEqual(m.id, item.id)
        self.assertTrue(m is item)

    def test_foreign_key(self):
        def foo():
            class RomTestBFkey1(Model):
                bad = ManyToOne("RomTestBad")
            RomTestBFkey1()
        self.assertRaises(ORMError, foo)

        def foo2():
            class RomTestBFkey2(Model):
                bad = OneToMany("RomTestBad")
            RomTestBFkey2()
        self.assertRaises(ORMError, foo2)

        class RomTestFkey1(Model):
            fkey2 = ManyToOne("RomTestFkey2")
        class RomTestFkey2(Model):
            fkey1 = OneToMany("RomTestFkey1")

        x = RomTestFkey2()
        y = RomTestFkey1(fkey2=x) # implicitly saves x
        y.save()

        xid = x.id
        yid = y.id
        x = y = None
        y = RomTestFkey1.get(yid)
        self.assertEqual(y.fkey2.id, xid)
        fk1 = y.fkey2.fkey1

        self.assertEqual(len(fk1), 1)
        self.assertEqual(fk1[0].id, y.id)

    def test_unique(self):
        class RomTestUnique(Model):
            attr = Text(unique=True)

        a = RomTestUnique(attr='hello')
        b = RomTestUnique(attr='hello2')
        a.save()
        b.save()
        b.attr = 'hello'
        self.assertRaises(UniqueKeyViolation, b.save)

        c = RomTestUnique(attr='hello')
        self.assertRaises(UniqueKeyViolation, c.save)

        a.delete()
        b.save()

    def test_saving(self):
        class RomTestNormal(Model):
            attr = Text()

        self.assertTrue(RomTestNormal().save())
        self.assertTrue(RomTestNormal(attr='hello').save())
        x = RomTestNormal()
        self.assertTrue(x.save())
        self.assertFalse(x.save())
        session.commit()

        self.assertTrue(x is RomTestNormal.get(x.id))

    def test_index(self):
        class RomTestIndexedModel(Model):
            attr = Text(index=True)
            attr2 = Text(index=True)
            attr3 = Integer(index=True)
            attr4 = Float(index=True)
            attr5 = Decimal(index=True)

        x = RomTestIndexedModel(
            attr='hello world',
            attr2='how are you doing?',
            attr3=7,
            attr4=4.5,
            attr5=_Decimal('2.643'),
        )
        x.save()
        RomTestIndexedModel(
            attr='world',
            attr3=100,
            attr4=-1000,
            attr5=_Decimal('2.643'),
        ).save()

        self.assertEqual(RomTestIndexedModel.query.filter(attr='hello').count(), 1)
        self.assertEqual(RomTestIndexedModel.query.filter(attr2='how').filter(attr2='are').count(), 1)
        self.assertEqual(RomTestIndexedModel.query.filter(attr='hello').filter(attr2='how').filter(attr2='are').count(), 1)
        self.assertEqual(RomTestIndexedModel.query.filter(attr='hello', noattr='bad').filter(attr2='how').filter(attr2='are').count(), 0)
        self.assertEqual(RomTestIndexedModel.query.filter(attr='hello', attr3=(None, None)).count(), 1)
        self.assertEqual(RomTestIndexedModel.query.filter(attr='hello', attr3=(None, 10)).count(), 1)
        self.assertEqual(RomTestIndexedModel.query.filter(attr='hello', attr3=(None, 10)).execute()[0].id, 1)
        self.assertEqual(RomTestIndexedModel.query.filter(attr='hello', attr3=(5, None)).count(), 1)
        self.assertEqual(RomTestIndexedModel.query.filter(attr='hello', attr3=(5, 10), attr4=(4,5), attr5=(2.5, 2.7)).count(), 1)
        first = RomTestIndexedModel.query.filter(attr='hello', attr3=(5, 10), attr4=(4,5), attr5=(2.5, 2.7)).first()
        self.assertTrue(first)
        self.assertTrue(first is x)
        self.assertEqual(RomTestIndexedModel.query.filter(attr='hello', attr3=(10, 20), attr4=(4,5), attr5=(2.5, 2.7)).count(), 0)
        self.assertEqual(RomTestIndexedModel.query.filter(attr3=100).count(), 1)
        self.assertEqual(RomTestIndexedModel.query.filter(attr='world', attr5=_Decimal('2.643')).count(), 2)

        results = RomTestIndexedModel.query.filter(attr='world').order_by('attr4').execute()
        self.assertEqual([x.id for x in results], [2,1])

        for i in range(50):
            RomTestIndexedModel(attr3=i)
        session.commit()
        session.rollback()

        self.assertEqual(len(RomTestIndexedModel.get_by(attr3=(10, 25))), 16)
        self.assertEqual(len(RomTestIndexedModel.get_by(attr3=(10, 25), _limit=(0,5))), 5)

        key = RomTestIndexedModel.query.filter(attr='hello').filter(attr2='how').filter(attr2='are').cached_result(30)
        conn = connect(None)
        self.assertTrue(conn.ttl(key) <= 30)
        self.assertEqual(conn.zcard(key), 1)
        conn.delete(key)

    def test_alternate_models(self):
        ctr = [0]
        class RomTestAlternate(object):
            def __init__(self, id=None):
                if id is None:
                    id = ctr[0]
                    ctr[0] += 1
                self.id = id

            @classmethod
            def get(self, id):
                return RomTestAlternate(id)

        class RomTestFModel(Model):
            attr = ForeignModel(RomTestAlternate)

        a = RomTestAlternate()
        ai = a.id
        i = RomTestFModel(attr=a).id
        session.commit()   # two lines of magic to destroy session history
        session.rollback() #
        del a

        f = RomTestFModel.get(i)
        self.assertEqual(f.attr.id, ai)

    def test_model_connection(self):
        class RomTestFoo(Model):
            pass

        class RomTestBar(Model):
            _conn = redis.Redis(db=14)

        RomTestBar._conn.delete('RomTestBar:id:')

        RomTestFoo().save()
        RomTestBar().save()

        self.assertEqual(RomTestBar._conn.get('RomTestBar:id:').decode(), '1')
        self.assertEqual(util.CONNECTION.get('RomTestBar:id:'), None)
        RomTestBar.get(1).delete()
        RomTestBar._conn.delete('RomTestBar:id:')
        k = RomTestBar._conn.keys('RomTest*')
        if k:
            RomTestBar._conn.delete(*k)

    def test_entity_caching(self):
        class RomTestGoo(Model):
            pass

        f = RomTestGoo()
        i = f.id
        session.commit()

        for j in range(10):
            RomTestGoo()

        g = RomTestGoo.get(i)
        self.assertIs(f, g)

    def test_index_preservation(self):
        """ Edits to unrelated columns should not remove the index of other
        columns. Issue: https://github.com/josiahcarlson/rom/issues/2. """

        class RomTestM(Model):
            u = Text(unique=True)
            i = Integer(index=True)
            unrelated = Text()

        RomTestM(u='foo', i=11).save()

        m = RomTestM.get_by(u='foo')
        m.unrelated = 'foobar'
        self.assertEqual(len(RomTestM.get_by(i=11)), 1)
        m.save()
        self.assertEqual(len(RomTestM.get_by(i=11)), 1)
        self.assertEqual(len(RomTestM.get_by(i=(10, 12))), 1)

    def test_json_multisave(self):
        class RomTestJsonTest(Model):
            col = Json()

        d = {'hello': 'world'}
        x = RomTestJsonTest(col=d)
        x.save()
        del x
        for i in range(5):
            x = RomTestJsonTest.get(1)
            self.assertEqual(x.col, d)
            x.save(full=True)
            session.rollback()

    def test_boolean(self):
        class RomTestBooleanTest(Model):
            col = Boolean(index=True)

        RomTestBooleanTest(col=True).save()
        RomTestBooleanTest(col=1).save()
        RomTestBooleanTest(col=False).save()
        RomTestBooleanTest(col='').save()
        RomTestBooleanTest(col=None).save() # None is considered "not data", so is ignored
        y = RomTestBooleanTest()
        yid = y.id
        y.save()
        del y
        self.assertEqual(len(RomTestBooleanTest.get_by(col=True)), 2)
        self.assertEqual(len(RomTestBooleanTest.get_by(col=False)), 2)
        session.rollback()
        x = RomTestBooleanTest.get(1)
        x.col = False
        x.save()
        self.assertEqual(len(RomTestBooleanTest.get_by(col=True)), 1)
        self.assertEqual(len(RomTestBooleanTest.get_by(col=False)), 3)
        self.assertEqual(len(RomTestBooleanTest.get_by(col=True)), 1)
        self.assertEqual(len(RomTestBooleanTest.get_by(col=False)), 3)
        y = RomTestBooleanTest.get(yid)
        self.assertEqual(y.col, None)

    def test_datetimes(self):
        class RomTestDateTimesTest(Model):
            col1 = DateTime(index=True)
            col2 = Date(index=True)
            col3 = Time(index=True)

        dtt = RomTestDateTimesTest(col1=_now, col2=_now.date(), col3=_now.time())
        dtt.save()
        session.commit()
        del dtt
        self.assertEqual(len(RomTestDateTimesTest.get_by(col1=_now)), 1)
        self.assertEqual(len(RomTestDateTimesTest.get_by(col2=_now.date())), 1)
        self.assertEqual(len(RomTestDateTimesTest.get_by(col3=_now.time())), 1)

    def test_deletion(self):
        class RomTestDeletionTest(Model):
            col1 = Text(index=True)

        x = RomTestDeletionTest(col1="this is a test string that should be indexed")
        session.commit()
        self.assertEqual(len(RomTestDeletionTest.get_by(col1='this')), 1)

        x.delete()
        self.assertEqual(len(RomTestDeletionTest.get_by(col1='this')), 0)

        session.commit()
        self.assertEqual(len(RomTestDeletionTest.get_by(col1='this')), 0)

    def test_empty_query(self):
        class RomTestEmptyQueryTest(Model):
            col1 = Text()

        RomTestEmptyQueryTest().save()
        self.assertRaises(QueryError, RomTestEmptyQueryTest.query.all)
        self.assertRaises(QueryError, RomTestEmptyQueryTest.query.count)
        self.assertRaises(QueryError, RomTestEmptyQueryTest.query.limit(0, 10).count)

    def test_refresh(self):
        class RomTestRefresh(Model):
            col = Text()

        d = RomTestRefresh(col='hello')
        d.save()
        d.col = 'world'
        self.assertRaises(InvalidOperation, d.refresh)
        d.refresh(True)
        self.assertEqual(d.col, 'hello')
        d.col = 'world'
        session.refresh(d, force=True)
        self.assertEqual(d.col, 'hello')
        d.col = 'world'
        session.refresh_all(force=True)
        self.assertEqual(d.col, 'hello')
        self.assertRaises(InvalidOperation, RomTestRefresh(col='boo').refresh)

    def test_datetime(self):
        class RomTestDT(Model):
            created_at = DateTime(default=datetime.utcnow)
            event_datetime = DateTime(index=True)

        x = RomTestDT()
        x.event_datetime = datetime.utcnow()
        x.save()
        RomTestDT(event_datetime=datetime.utcnow()).save()
        session.rollback() # clearing the local cache

        self.assertEqual(RomTestDT.get_by(event_datetime=(datetime(2000, 1, 1), datetime(2000, 1, 1))), [])
        self.assertEqual(len(RomTestDT.get_by(event_datetime=(datetime(2000, 1, 1), datetime.utcnow()))), 2)

    def test_prefix_suffix_pattern(self):
        import rom
        if not rom.USE_LUA:
            return

        class RomTestPSP(Model):
            col = Text(prefix=True, suffix=True)

        x = RomTestPSP(col="hello world how are you doing, join us today")
        x.save()

        self.assertEqual(RomTestPSP.query.startswith(col='he').count(), 1)
        self.assertEqual(RomTestPSP.query.startswith(col='notthere').count(), 0)
        self.assertEqual(RomTestPSP.query.endswith(col='rld').count(), 1)
        self.assertEqual(RomTestPSP.query.endswith(col='bad').count(), 0)
        self.assertEqual(RomTestPSP.query.like(col='?oin?').count(), 1)
        self.assertEqual(RomTestPSP.query.like(col='*oin+').count(), 1)
        self.assertEqual(RomTestPSP.query.like(col='oin').count(), 0)
        self.assertEqual(RomTestPSP.query.like(col='+oin').like(col='wor!d').count(), 1)

    def test_unicode_text(self):
        import rom
        ch = unichr(0xfeff) if six.PY2 else chr(0xfeff)
        pre = ch + 'hello'
        suf = 'hello' + ch

        class RomTestUnicode1(Model):
            col = Text(index=True, unique=True)

        RomTestUnicode1(col=pre).save()
        RomTestUnicode1(col=suf).save()

        self.assertEqual(RomTestUnicode1.query.filter(col=pre).count(), 1)
        self.assertEqual(RomTestUnicode1.query.filter(col=suf).count(), 1)
        self.assertTrue(RomTestUnicode1.get_by(col=pre))
        self.assertTrue(RomTestUnicode1.get_by(col=suf))

        import rom
        if rom.USE_LUA:
            class RomTestUnicode2(Model):
                col = Text(prefix=True, suffix=True)

            RomTestUnicode2(col=pre).save()
            RomTestUnicode2(col=suf).save()

            self.assertEqual(RomTestUnicode2.query.startswith(col="h").count(), 1)
            self.assertEqual(RomTestUnicode2.query.startswith(col=ch).count(), 1)
            self.assertEqual(RomTestUnicode2.query.endswith(col="o").count(), 1)
            self.assertEqual(RomTestUnicode2.query.endswith(col=ch).count(), 1)

    def test_infinite_ranges(self):
        """ Infinite range lookups via None in tuple.
        The get_by method accepts None as an argument to range based
        lookups.  A range lookup is done by passing a tuple as the value
        to the kwarg representing the column in question such as,
        Model.get_by(some_column=(small, large)).  The left and right side
        are inclusive. """

        class RomTestInfRange(Model):
            dt = DateTime(index=True)
            num = Integer(index=True)

        start_dt = datetime(2000, 1, 1)
        for x in range(3):
            RomTestInfRange(dt=start_dt+timedelta(days=x), num=x)
        session.commit()
        ranges = (
            (dict(dt=(start_dt-timedelta(days=1), None)), 3),
            (dict(dt=(start_dt, None)), 3),
            (dict(dt=(start_dt+timedelta(days=0.5), None)), 2),
            (dict(dt=(start_dt+timedelta(days=1), None)), 2),
            (dict(dt=(start_dt+timedelta(days=1.5), None)), 1),
            (dict(dt=(start_dt+timedelta(days=2), None)), 1),
            (dict(dt=(start_dt+timedelta(days=2.5), None)), 0),

            (dict(dt=(None, start_dt+timedelta(days=2.5))), 3),
            (dict(dt=(None, start_dt+timedelta(days=2))), 3),
            (dict(dt=(None, start_dt+timedelta(days=1.5))), 2),
            (dict(dt=(None, start_dt+timedelta(days=1))), 2),
            (dict(dt=(None, start_dt+timedelta(days=0.5))), 1),
            (dict(dt=(None, start_dt)), 1),
            (dict(dt=(None, start_dt-timedelta(days=1))), 0),

            (dict(num=(-1, None)), 3),
            (dict(num=(0, None)), 3),
            (dict(num=(0.5, None)), 2),
            (dict(num=(1, None)), 2),
            (dict(num=(1.5, None)), 1),
            (dict(num=(2, None)), 1),
            (dict(num=(2.5, None)), 0),

            (dict(num=(None, 2.5)), 3),
            (dict(num=(None, 2)), 3),
            (dict(num=(None, 1.5)), 2),
            (dict(num=(None, 1)), 2),
            (dict(num=(None, 0.5)), 1),
            (dict(num=(None, 0)), 1),
            (dict(num=(None, -1)), 0),
        )
        for i, (kwargs, count) in enumerate(ranges):
            try:
                st = 1
                self.assertEqual(len(RomTestInfRange.get_by(**kwargs)), count)
                st = 2
                self.assertEqual(RomTestInfRange.query.filter(**kwargs).count(), count)
                st = 3
                self.assertEqual(len(RomTestInfRange.query.filter(**kwargs).execute()), count)
            except Exception:
                msg = 'test %d step %d: range %s, expect: %d' % (i, st, kwargs, count)
                print(msg)
                raise

    def test_big_int(self):
        """ Ensure integers that overflow py2k int work. """

        class RomTestBigInt(Model):
            num = Integer()

        numbers = [
            1,
            200,
            1<<15,
            1<<63,
            1<<128,
            1<<256,
        ]

        for i, num in enumerate(numbers):
            RomTestBigInt(num=num).save()
            session.commit()
            session.rollback()
            echo = RomTestBigInt.get(i+1).num
            self.assertEqual(num, echo)

    def test_cleanup(self):
        """ Ensure no side effects are left in the db after a delete. """
        redis = connect(None)

        class RomTestCleanupA(Model):
            foo = Text()
            blist = OneToMany('RomTestCleanupB')

        class RomTestCleanupB(Model):
            bar = Text()
            a = ManyToOne('RomTestCleanupA')

        a = RomTestCleanupA(foo='foo')
        a.save()
        b = RomTestCleanupB(bar='foo', a=a)
        b.save()
        b.delete()
        self.assertFalse(redis.hkeys('RomTestCleanupB:%d' % b.id))
        a.delete()
        self.assertFalse(redis.hkeys('RomTestCleanupA:%d' % a.id))

        # Test delete() where a column value does not change. This affects
        # the writer logic which checks for deltas as a means to determine
        # what keys should be removed from the redis hash bucket.
        a = RomTestCleanupA(foo='foo')
        a.save()
        b = RomTestCleanupB(bar='foo', a=a)
        b.save()
        a.delete()  # Nullify FK on b.
        self.assertFalse(redis.hkeys('RomTestCleanupA:%d' % a.id))
        session.rollback() # XXX purge session cache
        b = RomTestCleanupB.get(b.id)
        b.delete()  # Nullify FK on b.
        self.assertFalse(redis.hkeys('RomTestCleanupB:%d' % b.id))

    def test_delete_writethrough(self):
        """ Verify that a Model.delete() writes through backing and session. """

        class RomTestDelete(Model):
            pass

        # write-through backing
        a = RomTestDelete()
        a.save()
        a.delete()
        session.commit()
        session.rollback()
        self.assertIsNone(RomTestDelete.get(a.id))

        # write-through cache auto-commit (session)
        a = RomTestDelete()
        a.save()
        a.delete()
        self.assertIsNone(RomTestDelete.get(a.id))

        # write-through cache force-commit (session)
        a = RomTestDelete()
        a.save()
        a.delete()
        session.commit()
        self.assertIsNone(RomTestDelete.get(a.id))

    def test_prefix_suffix1(self):
        from rom import columns
        if not columns.USE_LUA:
            return
        class RomTestPerson(Model):
            name = Text(prefix=True, suffix=True, index=True)

        names = ['Acasaoi', 'Maria Williamson IV', 'Rodrigo Howe',
            'Mr. Willow Goldner', 'Melody Prohaska', 'Hulda Botsford',
            'Lester Swaniawski MD', 'Vilma Mohr Sr.', 'Pierre Moen',
            'Beau Streich', 'Mrs. Laron Morar III', 'bmliodasas',
            'Jewell Stroman', 'Garfield Stark', 'Dr. Ignatius Kuvalis PhD',
            'Nikita Okuneva', 'Daija Turcotte', 'Royce Halvorson',
            'Tess Schimmel', 'Ms. Monte Heathcote', 'Johann Glover',
            'Kade Lueilwitz', 'bsaasao', 'Casper Pouros',
            'Miss Griffin Corkery II', 'Cierra Volkman V', 'Sean McLaughlin',
            'Cmlio', 'Cdsdmlio', 'Dasao', 'Dioasu', 'Eioasu', 'Fasao',
            'Emilie Towne II', 'G h o', 'Jhorecgssd',
            'H Mrs. Newton Murazik Sr.  Zidfdfoaxaol dfgdfggf ',
            'Zidfdfoasaol dfgdfggf Mrs. Newton Murazik Sr.  ',
            'Perry Ankunding', 'Dusty Kessler', 'Jacinthe Bechtelar',
            'Dr. Jordan Hintz PhD', 'Miss Monty Kuvalis']
        for name in names:
            RomTestPerson(name=name)
        session.commit()

        self.assertEqual(RomTestPerson.query.like(name='*asao*').count(), 5)

    def test_prefix_suffix2(self):
        from rom import columns
        if not columns.USE_LUA:
            return
        string = String if six.PY2 else Text
        class RomTestPerson2(Model):
            idPerson = string(prefix=True, suffix=True, index=True)
            description = string(prefix=True, suffix=True, index=True)
        data = [
            ["8947589545872", "ayuntamientodeciudad"],
            ["8947589545872", "ayuntamientodeguipuzcoa"],
            ["8947589545872", "ayuntamientodepalencia"],
            ["8947589545872", "ayuntamientodeciudad"],
            ["8947589569872", "ayuntamientodeburgos"], #
            ["8947689545872", "ayuntamientodeburgos"],
            ["8947689545872", "ayuntamientodeburgos"],
            ["894789545872", "ayuntamientodeciudad"]
        ]
        cols = ['idPerson', 'description']
        for d in data:
            RomTestPerson2(**dict(zip(cols, d)))
        session.commit()

        self.assertEqual(RomTestPerson2.query.startswith(idPerson='89475').filter(description="ayuntamientodeburgos").count(),1)

def main():
    _disable_lua_writes()
    global_setup()
    print("Testing standard writing")
    try:
        unittest.main()
    except SystemExit:
        pass
    data = get_state()
    global_setup()
    _enable_lua_writes()
    print("Testing Lua writing")
    try:
        unittest.main()
    except SystemExit:
        pass
    lua_data = get_state()
    global_setup()

    ## if data != lua_data:
        ## print("WARNING: Regular/Lua data writing does not match!")
        ## import pprint
        ## pprint.pprint(data)
        ## pprint.pprint(lua_data)

if __name__ == '__main__':
    main()
