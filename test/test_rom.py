
from decimal import Decimal as _Decimal
import time
import unittest

import redis

from rom import util

util.CONNECTION = redis.Redis(db=15)
connect = util.connect

from rom import *
from rom.exceptions import *

class TestORM(unittest.TestCase):
    @connect
    def setUp(self, conn):
        conn.flushdb()
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

    def test_saving(self):
        class Normal(Model):
            attr = String()

        self.assertTrue(Normal().save())
        self.assertTrue(Normal(attr='hello').save())
        x = Normal()
        self.assertTrue(x.save())
        self.assertFalse(x.save())

        self.assertTrue(x is Normal.get(x.id))

    def test_index(self):
        class IndexedModel(Model):
            attr = String(index=True)
            attr2 = String(index=True)
            attr3 = Integer(index=True)
            attr4 = Float(index=True)
            attr5 = Decimal(index=True)

        IndexedModel(
            attr='hello world',
            attr2='how are you doing?',
            attr3=7,
            attr4=4.5,
            attr5=_Decimal('2.643'),
        ).save()
        IndexedModel(
            attr='world',
            attr3=100,
            attr4=-1000,
            attr5=_Decimal('2.643'),
        ).save()
        
        
        self.assertEquals(IndexedModel.query.filter(attr='hello').count(), 1)
        self.assertEquals(IndexedModel.query.filter(attr2=['how', 'are']).count(), 1)
        self.assertEquals(IndexedModel.query.filter(attr='hello', attr2=['how', 'are']).count(), 1)
        self.assertEquals(IndexedModel.query.filter(attr='hello', noattr='bad', attr2=['how', 'are']).count(), 0)
        self.assertEquals(IndexedModel.query.filter(attr='hello', attr3=(None, None)).count(), 1)
        self.assertEquals(IndexedModel.query.filter(attr='hello', attr3=(None, 10)).count(), 1)
        self.assertEquals(IndexedModel.query.filter(attr='hello', attr3=(None, 10)).execute()[0].id, 1)
        self.assertEquals(IndexedModel.query.filter(attr='hello', attr3=(5, None)).count(), 1)
        self.assertEquals(IndexedModel.query.filter(attr='hello', attr3=(5, 10), attr4=(4,5), attr5=(2.5, 2.7)).count(), 1)
        self.assertEquals(IndexedModel.query.filter(attr='hello', attr3=(10, 20), attr4=(4,5), attr5=(2.5, 2.7)).count(), 0)
        
        results = IndexedModel.query.filter(attr='world').order_by('attr4').execute()
        self.assertEquals([x.id for x in results], [2,1])

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
