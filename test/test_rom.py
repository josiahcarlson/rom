
from datetime import datetime
from decimal import Decimal as _Decimal
import time
import unittest

import redis

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
    for k,v in MODELS.items():
        if v is not Model:
            del MODELS[k]

def get_state():
    c = connect(None)
    data = []
    for k in c.keys('*'):
        t = c.type(k)
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

    def test_basic_model(self):
        class RomTestBasicModel(Model):
            val = Integer()
            oval = Integer(default=7)
            created_at = Float(default=_default_time)
            req = String(required=True)

        self.assertRaises(ColumnError, RomTestBasicModel)
        self.assertRaises(InvalidColumnValue, lambda: RomTestBasicModel(oval='t'))
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
            key = String(required=True, unique=True)

        self.assertRaises(MissingColumn, RomTestIndexModel)
        item = RomTestIndexModel(key="hello")
        item.save()

        m = RomTestIndexModel.get_by(key="hello")
        self.assertTrue(m)
        self.assertEquals(m.id, item.id)
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
        self.assertEquals(y.fkey2.id, xid)
        fk1 = y.fkey2.fkey1

        self.assertEquals(len(fk1), 1)
        self.assertEquals(fk1[0].id, y.id)

    def test_unique(self):
        class RomTestUnique(Model):
            attr = String(unique=True)

        a = RomTestUnique(attr='hello')
        b = RomTestUnique(attr='hello2')
        a.save()
        b.save()
        b.attr = 'hello'
        self.assertRaises(UniqueKeyViolation, b.save)

        c = RomTestUnique(attr='hello')
        self.assertRaises(UniqueKeyViolation, c.save)

    def test_saving(self):
        class RomTestNormal(Model):
            attr = String()

        self.assertTrue(RomTestNormal().save())
        self.assertTrue(RomTestNormal(attr='hello').save())
        x = RomTestNormal()
        self.assertTrue(x.save())
        self.assertFalse(x.save())
        session.commit()

        self.assertTrue(x is RomTestNormal.get(x.id))

    def test_index(self):
        class RomTestIndexedModel(Model):
            attr = String(index=True)
            attr2 = String(index=True)
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

        self.assertEquals(RomTestIndexedModel.query.filter(attr='hello').count(), 1)
        self.assertEquals(RomTestIndexedModel.query.filter(attr2='how').filter(attr2='are').count(), 1)
        self.assertEquals(RomTestIndexedModel.query.filter(attr='hello').filter(attr2='how').filter(attr2='are').count(), 1)
        self.assertEquals(RomTestIndexedModel.query.filter(attr='hello', noattr='bad').filter(attr2='how').filter(attr2='are').count(), 0)
        self.assertEquals(RomTestIndexedModel.query.filter(attr='hello', attr3=(None, None)).count(), 1)
        self.assertEquals(RomTestIndexedModel.query.filter(attr='hello', attr3=(None, 10)).count(), 1)
        self.assertEquals(RomTestIndexedModel.query.filter(attr='hello', attr3=(None, 10)).execute()[0].id, 1)
        self.assertEquals(RomTestIndexedModel.query.filter(attr='hello', attr3=(5, None)).count(), 1)
        self.assertEquals(RomTestIndexedModel.query.filter(attr='hello', attr3=(5, 10), attr4=(4,5), attr5=(2.5, 2.7)).count(), 1)
        first = RomTestIndexedModel.query.filter(attr='hello', attr3=(5, 10), attr4=(4,5), attr5=(2.5, 2.7)).first()
        self.assertTrue(first)
        self.assertTrue(first is x)
        self.assertEquals(RomTestIndexedModel.query.filter(attr='hello', attr3=(10, 20), attr4=(4,5), attr5=(2.5, 2.7)).count(), 0)
        self.assertEquals(RomTestIndexedModel.query.filter(attr3=100).count(), 1)
        self.assertEquals(RomTestIndexedModel.query.filter(attr='world', attr5=_Decimal('2.643')).count(), 2)

        results = RomTestIndexedModel.query.filter(attr='world').order_by('attr4').execute()
        self.assertEquals([x.id for x in results], [2,1])

        for i in xrange(50):
            RomTestIndexedModel(attr3=i)
        session.commit()
        session.rollback()

        self.assertEquals(len(RomTestIndexedModel.get_by(attr3=(10, 25))), 16)
        self.assertEquals(len(RomTestIndexedModel.get_by(attr3=(10, 25), _limit=(0,5))), 5)

        key = RomTestIndexedModel.query.filter(attr='hello').filter(attr2='how').filter(attr2='are').cached_result(30)
        conn = connect(None)
        self.assertTrue(conn.ttl(key) <= 30)
        self.assertEquals(conn.zcard(key), 1)
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
        self.assertEquals(f.attr.id, ai)

    def test_model_connection(self):
        class RomTestFoo(Model):
            pass

        class RomTestBar(Model):
            _conn = redis.Redis(db=14)

        RomTestBar._conn.delete('RomTestBar:id:')

        RomTestFoo().save()
        RomTestBar().save()

        self.assertEquals(RomTestBar._conn.get('RomTestBar:id:'), '1')
        self.assertEquals(util.CONNECTION.get('RomTestBar:id:'), None)
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

        for j in xrange(10):
            RomTestGoo()

        g = RomTestGoo.get(i)
        self.assertTrue(f is g)

    def test_index_preservation(self):
        """ Edits to unrelated columns should not remove the index of other
        columns. Issue: https://github.com/josiahcarlson/rom/issues/2. """

        class RomTestM(Model):
            u = String(unique=True)
            i = Integer(index=True)
            unrelated = String()

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
        for i in xrange(5):
            x = RomTestJsonTest.get(1)
            self.assertEquals(x.col, d)
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
        self.assertEquals(len(RomTestBooleanTest.get_by(col=True)), 2)
        self.assertEquals(len(RomTestBooleanTest.get_by(col=False)), 2)
        session.rollback()
        x = RomTestBooleanTest.get(1)
        x.col = False
        x.save()
        self.assertEquals(len(RomTestBooleanTest.get_by(col=True)), 1)
        self.assertEquals(len(RomTestBooleanTest.get_by(col=False)), 3)
        self.assertEquals(len(RomTestBooleanTest.get_by(col=True)), 1)
        self.assertEquals(len(RomTestBooleanTest.get_by(col=False)), 3)
        y = RomTestBooleanTest.get(yid)
        self.assertEquals(y.col, None)

    def test_datetimes(self):
        class RomTestDateTimesTest(Model):
            col1 = DateTime(index=True)
            col2 = Date(index=True)
            col3 = Time(index=True)

        dtt = RomTestDateTimesTest(col1=_now, col2=_now.date(), col3=_now.time())
        dtt.save()
        session.commit()
        del dtt
        self.assertEquals(len(RomTestDateTimesTest.get_by(col1=_now)), 1)
        self.assertEquals(len(RomTestDateTimesTest.get_by(col2=_now.date())), 1)
        self.assertEquals(len(RomTestDateTimesTest.get_by(col3=_now.time())), 1)

    def test_deletion(self):
        class RomTestDeletionTest(Model):
            col1 = String(index=True)

        x = RomTestDeletionTest(col1="this is a test string that should be indexed")
        session.commit()
        self.assertEquals(len(RomTestDeletionTest.get_by(col1='this')), 1)

        x.delete()
        self.assertEquals(len(RomTestDeletionTest.get_by(col1='this')), 0)

        session.commit()
        self.assertEquals(len(RomTestDeletionTest.get_by(col1='this')), 0)

    def test_empty_query(self):
        class RomTestEmptyQueryTest(Model):
            col1 = String()

        RomTestEmptyQueryTest().save()
        self.assertRaises(QueryError, RomTestEmptyQueryTest.query.all)
        self.assertRaises(QueryError, RomTestEmptyQueryTest.query.count)
        self.assertRaises(QueryError, RomTestEmptyQueryTest.query.limit(0, 10).count)

    def test_refresh(self):
        class RomTestRefresh(Model):
            col = String()

        d = RomTestRefresh(col='hello')
        d.save()
        d.col = 'world'
        self.assertRaises(InvalidOperation, d.refresh)
        d.refresh(True)
        self.assertEquals(d.col, 'hello')
        d.col = 'world'
        session.refresh(d, force=True)
        self.assertEquals(d.col, 'hello')
        d.col = 'world'
        session.refresh_all(force=True)
        self.assertEquals(d.col, 'hello')
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

        self.assertEquals(RomTestDT.get_by(event_datetime=(datetime(2000, 1, 1), datetime(2000, 1, 1))), [])
        self.assertEquals(len(RomTestDT.get_by(event_datetime=(datetime(2000, 1, 1), datetime.utcnow()))), 2)

    def test_prefix_suffix_pattern(self):
        import rom
        if not rom.USE_LUA:
            return

        class RomTestPSP(Model):
            col = String(prefix=True, suffix=True)

        x = RomTestPSP(col="hello world how are you doing, join us today")
        x.save()

        self.assertEquals(RomTestPSP.query.startswith(col='he').count(), 1)
        self.assertEquals(RomTestPSP.query.startswith(col='notthere').count(), 0)
        self.assertEquals(RomTestPSP.query.endswith(col='rld').count(), 1)
        self.assertEquals(RomTestPSP.query.endswith(col='bad').count(), 0)
        self.assertEquals(RomTestPSP.query.like(col='?oin?').count(), 1)
        self.assertEquals(RomTestPSP.query.like(col='*oin+').count(), 1)
        self.assertEquals(RomTestPSP.query.like(col='oin').count(), 0)
        self.assertEquals(RomTestPSP.query.like(col='+oin').like(col='wor!d').count(), 1)

if __name__ == '__main__':
    _disable_lua_writes()
    global_setup()
    print "Testing standard writing"
    try:
        unittest.main()
    except:
        data = get_state()
    global_setup()
    _enable_lua_writes()
    print "Testing Lua writing"
    try:
        unittest.main()
    except:
        lua_data = get_state()
    global_setup()

    ## if data != lua_data:
        ## print "WARNING: Regular/Lua data writing does not match!"
        ## import pprint
        ## pprint.pprint(data)
        ## pprint.pprint(lua_data)
