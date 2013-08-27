
from datetime import datetime, date, time as dtime
from decimal import Decimal as _Decimal
import time
import unittest

import redis

from rom import util

util.CONNECTION = redis.Redis(db=15)
connect = util._connect

from rom import *
from rom.exceptions import *

class TestORM(unittest.TestCase):
    def setUp(self):
        connect(None).flushdb()
    def tearDown(self):
        self.setUp()

    def test_basic_model(self):
        class BasicModel(Model):
            val = Integer()
            oval = Integer(default=7)
            created_at = Float(default=time.time)
            req = String(required=True)

        self.assertRaises(ColumnError, BasicModel)
        self.assertRaises(InvalidColumnValue, lambda: BasicModel(oval='t'))
        self.assertRaises(MissingColumn, lambda: BasicModel(created_at=7))

        # try object saving/loading
        x = BasicModel(val=1, req="hello")
        x.save()
        id = x.id
        x = x.to_dict()

        y = BasicModel.get(id)
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
            class BadIndexModel2(Model):
                bad = Integer(unique=True)
        self.assertRaises(ColumnError, foo2)

        class IndexModel(Model):
            key = String(required=True, unique=True)

        self.assertRaises(MissingColumn, IndexModel)
        item = IndexModel(key="hello")
        item.save()

        m = IndexModel.get_by(key="hello")
        self.assertTrue(m)
        self.assertEquals(m.id, item.id)
        self.assertTrue(m is item)

    def test_foreign_key(self):
        def foo():
            class BFkey1(Model):
                bad = ManyToOne("Bad")
            BFkey1()
        self.assertRaises(ORMError, foo)

        def foo2():
            class BFkey2(Model):
                bad = OneToMany("Bad")
            BFkey2()
        self.assertRaises(ORMError, foo2)

        class Fkey1(Model):
            fkey2 = ManyToOne("Fkey2")
        class Fkey2(Model):
            fkey1 = OneToMany("Fkey1")

        x = Fkey2()
        y = Fkey1(fkey2=x) # implicitly saves x
        y.save()

        xid = x.id
        yid = y.id
        x = y = None
        y = Fkey1.get(yid)
        self.assertEquals(y.fkey2.id, xid)
        fk1 = y.fkey2.fkey1

        self.assertEquals(len(fk1), 1)
        self.assertEquals(fk1[0].id, y.id)

    def test_unique(self):
        class Unique(Model):
            attr = String(unique=True)

        a = Unique(attr='hello')
        b = Unique(attr='hello2')
        a.save()
        b.save()
        b.attr = 'hello'
        self.assertRaises(UniqueKeyViolation, b.save)

        c = Unique(attr='hello')
        self.assertRaises(UniqueKeyViolation, c.save)

    def test_saving(self):
        class Normal(Model):
            attr = String()

        self.assertTrue(Normal().save())
        self.assertTrue(Normal(attr='hello').save())
        x = Normal()
        self.assertTrue(x.save())
        self.assertFalse(x.save())
        session.commit()

        self.assertTrue(x is Normal.get(x.id))

    def test_index(self):
        class IndexedModel(Model):
            attr = String(index=True)
            attr2 = String(index=True)
            attr3 = Integer(index=True)
            attr4 = Float(index=True)
            attr5 = Decimal(index=True)

        x = IndexedModel(
            attr='hello world',
            attr2='how are you doing?',
            attr3=7,
            attr4=4.5,
            attr5=_Decimal('2.643'),
        )
        x.save()
        IndexedModel(
            attr='world',
            attr3=100,
            attr4=-1000,
            attr5=_Decimal('2.643'),
        ).save()

        self.assertEquals(IndexedModel.query.filter(attr='hello').count(), 1)
        self.assertEquals(IndexedModel.query.filter(attr2='how').filter(attr2='are').count(), 1)
        self.assertEquals(IndexedModel.query.filter(attr='hello').filter(attr2='how').filter(attr2='are').count(), 1)
        self.assertEquals(IndexedModel.query.filter(attr='hello', noattr='bad').filter(attr2='how').filter(attr2='are').count(), 0)
        self.assertEquals(IndexedModel.query.filter(attr='hello', attr3=(None, None)).count(), 1)
        self.assertEquals(IndexedModel.query.filter(attr='hello', attr3=(None, 10)).count(), 1)
        self.assertEquals(IndexedModel.query.filter(attr='hello', attr3=(None, 10)).execute()[0].id, 1)
        self.assertEquals(IndexedModel.query.filter(attr='hello', attr3=(5, None)).count(), 1)
        self.assertEquals(IndexedModel.query.filter(attr='hello', attr3=(5, 10), attr4=(4,5), attr5=(2.5, 2.7)).count(), 1)
        first = IndexedModel.query.filter(attr='hello', attr3=(5, 10), attr4=(4,5), attr5=(2.5, 2.7)).first()
        self.assertTrue(first)
        self.assertTrue(first is x)
        self.assertEquals(IndexedModel.query.filter(attr='hello', attr3=(10, 20), attr4=(4,5), attr5=(2.5, 2.7)).count(), 0)
        self.assertEquals(IndexedModel.query.filter(attr3=100).count(), 1)
        self.assertEquals(IndexedModel.query.filter(attr='world', attr5=_Decimal('2.643')).count(), 2)

        results = IndexedModel.query.filter(attr='world').order_by('attr4').execute()
        self.assertEquals([x.id for x in results], [2,1])

    def test_alternate_models(self):
        ctr = [0]
        class Alternate(object):
            def __init__(self, id=None):
                if id is None:
                    id = ctr[0]
                    ctr[0] += 1
                self.id = id

            @classmethod
            def get(self, id):
                return Alternate(id)

        class FModel(Model):
            attr = ForeignModel(Alternate)

        a = Alternate()
        ai = a.id
        i = FModel(attr=a).id
        session.commit()   # two lines of magic to destroy session history
        session.rollback() #
        del a

        f = FModel.get(i)
        self.assertEquals(f.attr.id, ai)

    def test_model_connection(self):
        class Foo(Model):
            pass

        class Bar(Model):
            _conn = redis.Redis(db=14)

        Bar._conn.delete('Bar:id:')

        Foo().save()
        Bar().save()

        self.assertEquals(Bar._conn.get('Bar:id:'), '1')
        self.assertEquals(util.CONNECTION.get('Bar:id:'), None)
        Bar.get(1).delete()
        Bar._conn.delete('Bar:id:')

    def test_entity_caching(self):
        class Goo(Model):
            pass

        f = Goo()
        i = f.id
        p = id(f)
        session.commit()

        for j in xrange(10):
            Goo()

        g = Goo.get(i)
        self.assertTrue(f is g)

    def test_index_preservation(self):
        """ Edits to unrelated columns should not remove the index of other
        columns. Issue: https://github.com/josiahcarlson/rom/issues/2. """

        class M(Model):
            u = String(unique=True)
            i = Integer(index=True)
            unrelated = String()

        M(u='foo', i=11).save()

        m = M.get_by(u='foo')
        m.unrelated = 'foobar'
        self.assertEqual(len(M.get_by(i=11)), 1)
        m.save()
        self.assertEqual(len(M.get_by(i=11)), 1)
        self.assertEqual(len(M.get_by(i=(10, 12))), 1)

    def test_json_multisave(self):
        class JsonTest(Model):
            col = Json()

        d = {'hello': 'world'}
        x = JsonTest(col=d)
        x.save()
        del x
        for i in xrange(5):
            x = JsonTest.get(1)
            self.assertEquals(x.col, d)
            x.save(full=True)
            session.rollback()

    def test_boolean(self):
        class BooleanTest(Model):
            col = Boolean(index=True)

        BooleanTest(col=True).save()
        BooleanTest(col=1).save()
        BooleanTest(col=False).save()
        BooleanTest(col='').save()
        BooleanTest(col=None).save() # None is considered "not data", so is ignored
        y = BooleanTest()
        yid = y.id
        y.save()
        del y
        self.assertEquals(len(BooleanTest.get_by(col=True)), 2)
        self.assertEquals(len(BooleanTest.get_by(col=False)), 2)
        session.rollback()
        x = BooleanTest.get(1)
        x.col = False
        x.save()
        self.assertEquals(len(BooleanTest.get_by(col=True)), 1)
        self.assertEquals(len(BooleanTest.get_by(col=False)), 3)
        self.assertEquals(len(BooleanTest.get_by(col=True)), 1)
        self.assertEquals(len(BooleanTest.get_by(col=False)), 3)
        y = BooleanTest.get(yid)
        self.assertEquals(y.col, None)

    def test_datetimes(self):
        class DateTimesTest(Model):
            col1 = DateTime(index=True)
            col2 = Date(index=True)
            col3 = Time(index=True)

        now = datetime.utcnow()
        dtt = DateTimesTest(col1=now, col2=now.date(), col3=now.time())
        dtt.save()
        session.commit()
        del dtt
        self.assertEquals(len(DateTimesTest.get_by(col1=now)), 1)
        self.assertEquals(len(DateTimesTest.get_by(col2=now.date())), 1)
        self.assertEquals(len(DateTimesTest.get_by(col3=now.time())), 1)

    def test_deletion(self):
        class DeletionTest(Model):
            col1 = String(index=True)

        x = DeletionTest(col1="this is a test string that should be indexed")
        session.commit()
        self.assertEquals(len(DeletionTest.get_by(col1='this')), 1)

        x.delete()
        self.assertEquals(len(DeletionTest.get_by(col1='this')), 0)

        session.commit()
        self.assertEquals(len(DeletionTest.get_by(col1='this')), 0)

if __name__ == '__main__':
    import sys
    if '--really-run' in sys.argv:
        del sys.argv[1:]
        unittest.main()
    else:
        print '''
Because we perform database flushes during testing, to ensure that you don't
accidentally blow away data that is important to you, we have disabled
testing. You can, of course, bypass this. But it is discouraged.'''
