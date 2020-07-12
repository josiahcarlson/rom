
'''
Rom - the Redis object mapper for Python

Copyright 2013-2020 Josiah Carlson

Released under the LGPL license version 2.1 and version 3 (you can choose
which you'd like to be bound under).
'''

from __future__ import print_function
import base64
from collections import namedtuple
from datetime import datetime, timedelta
from decimal import Decimal as _Decimal
import sys
import time
import unittest
import warnings

import redis
import six

from rom import util

util.CONNECTION = redis.Redis(db=15)
connect = util._connect

from rom import *
from rom.exceptions import *

string = String if six.PY2 else Text


def global_setup(a=1):
    c = connect(None)
    for p in ('RomTest*', 'RestrictA*', 'RestrictB*'):
        keys = c.keys(p)
        if keys:
            c.delete(*keys)
    if not a:
        return
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
            data.append((k, {"set": list(c.smembers(k))}))
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
        x = RomTestBasicModel(val=1, req=b"h\xd0\x80llo".decode('utf-8'))
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

        util.session.rollback()
        self.assertEqual([m.id for m in RomTestBasicModel.query], [id])
        self.assertEqual([m.id for m in RomTestBasicModel.query.iter_result(no_hscan=True)], [id])

    def test_unique_index(self):
        class RomTestIndexModel(Model):
            key = Text(required=True, unique=True)

        self.assertRaises(MissingColumn, RomTestIndexModel)
        item = RomTestIndexModel(key="hello")
        item.save()
        item2 = item.copy()
        self.assertRaises(UniqueKeyViolation, item2.save)

        m = RomTestIndexModel.get_by(key="hello")
        self.assertTrue(m)
        self.assertEqual(m.id, item.id)
        self.assertTrue(m is item)

    def test_unique_integer_index(self):
        class RomTestIndexModel2(Model):
            key = Integer(unique=True)

        item = RomTestIndexModel2(key=5)
        item.save()
        # verify this works when there is no data
        RomTestIndexModel2().save()
        RomTestIndexModel2().save()

        m = RomTestIndexModel2.get_by(key=5)
        self.assertTrue(m)
        self.assertEqual(m.id, item.id)
        self.assertTrue(m is item)

    def test_foreign_key(self):
        def foo():
            class RomTestBFkey1(Model):
                bad = ManyToOne("RomTestBad", 'no action')
            RomTestBFkey1()
        self.assertRaises(ORMError, foo)

        def foo2():
            class RomTestBFkey2(Model):
                bad = OneToMany("RomTestBad")
            RomTestBFkey2()
        self.assertRaises(ORMError, foo2)

        class RomTestFkey1(Model):
            fkey2 = ManyToOne("RomTestFkey2", 'no action')
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

    def test_foreign_key_delete(self):
        class RomTestM2Ofk(Model):
            ref = ManyToOne("RomTestO2Mfk", 'no action')
        class RomTestO2Mfk(Model):
            lst = OneToMany("RomTestM2Ofk")

        x = RomTestO2Mfk()
        x.save()
        ys = [RomTestM2Ofk(ref=x) for z in range(5)]
        [y.save() for y in ys]

        del ys[0].ref
        ys[0].save()
        self.assertEqual(ys[0].ref, None)
        ys[0].ref = x
        ys[0].save()
        self.assertEqual(ys[1].ref, x)

        ys[1].ref = None
        ys[1].save()
        self.assertEqual(ys[1].ref, None)
        self.assertEqual(len(x.lst), 4)
        # Test for double-delete
        ys[1].ref = None
        ys[1].save()
        ys[1].ref = x
        ys[1].save()
        self.assertEqual(ys[1].ref, x)

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
        plain = lambda x: [x.lower()] if x else None
        plain2 = lambda a,d: [d['attr'].lower()] if d.get('attr') else None
        class RomTestIndexedModel(Model):
            attr = Text(index=True, keygen=FULL_TEXT)
            attr2 = Text(index=True, keygen=FULL_TEXT)
            attr3 = Integer(index=True)
            attr4 = Float(index=True)
            attr5 = Decimal(index=True)
            attr6 = IndexOnly('attr', keygen=plain, suffix=True)
            attr7 = IndexOnly(keygen2=plain2, suffix=True)

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
        self.assertRaises(QueryError, lambda: RomTestIndexedModel.query.filter(attr='hello', noattr='bad'))
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
        self.assertEqual([y.id for y in results], [2,1])

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
        self.assertRaises(QueryError, lambda: RomTestIndexedModel.query.order_by('attr8'))

        # Only the first call raises the warning, so only bother to call it
        # for our first pass through tests (non-Lua case).
        with warnings.catch_warnings(record=True) as w:
            RomTestIndexedModel.query.order_by('attr')
            self.assertEqual(len(w), 1)

        for aname in ('attr6', 'attr7'):
            self.assertEqual(RomTestIndexedModel.query.endswith(**{aname:'world'}).count(), 2)
            self.assertEqual(RomTestIndexedModel.query.endswith(**{aname:' world'}).count(), 1)
            self.assertRaises(InvalidColumnValue, lambda: RomTestIndexedModel(**{aname:'hello'}))
            a = RomTestIndexedModel(**{aname:None})

            self.assertRaises(InvalidColumnValue, lambda: a.update(**{aname:'blah'}))
            a.update(attr='another world').save()
            self.assertEqual(RomTestIndexedModel.query.endswith(**{aname:' world'}).count(), 2)
            if sys.version_info >= (3,6):
                a.update('different world').save()
                self.assertEqual(RomTestIndexedModel.query.endswith(**{aname:'different world'}).count(), 1)
                a.update('overwritten', attr='kept').save()
                self.assertEqual(RomTestIndexedModel.query.endswith(**{aname:'kept'}).count(), 1)
            a.delete()
            session.rollback()

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

        x = RomTestFoo()
        x.save()
        y = RomTestBar()
        y.save()
        self.assertEqual(RomTestFoo._connection, util.CONNECTION)
        self.assertEqual(x._connection, util.CONNECTION)
        self.assertNotEqual(RomTestBar._connection, util.CONNECTION)
        self.assertNotEqual(y._connection, util.CONNECTION)

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
            col1 = Text(index=True, keygen=FULL_TEXT)

        x = RomTestDeletionTest(col1="this is a test string that should be indexed")
        session.commit()
        self.assertEqual(len(RomTestDeletionTest.get_by(col1='this')), 1)

        x.delete()
        self.assertEqual(len(RomTestDeletionTest.get_by(col1='this')), 0)

        session.commit()
        self.assertEqual(len(RomTestDeletionTest.get_by(col1='this')), 0)

    def test_empty_query(self):
        class RomTestEmptyQuery(Model):
            col1 = Text()

        x = RomTestEmptyQuery()
        x.save()
        session.forget(x)
        self.assertEqual(len(RomTestEmptyQuery.query.all()), 1)
        self.assertEqual(RomTestEmptyQuery.query.first().id, x.id)
        self.assertEqual(RomTestEmptyQuery.query.count(), 1)
        self.assertEqual(RomTestEmptyQuery.query.limit(0, 10).count(), 1)
        self.assertEqual(RomTestEmptyQuery.query.limit(1, 10).count(), 0)

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
        class RomTestPSP(Model):
            col = Text(prefix=True, suffix=True, keygen=FULL_TEXT)
            col2 = Text(prefix=True, suffix=True, keygen=SIMPLE)
            col3 = Text(prefix=True, suffix=True, keygen=SIMPLE_CI)

        x = RomTestPSP(col="hello world how are you doing, join us today",
                       col2="This is just another Test",
                       col3="And This is yet Another")
        x.save()

        self.assertEqual(RomTestPSP.query.startswith(col='he').count(), 1)
        self.assertEqual(RomTestPSP.query.startswith(col='notthere').count(), 0)
        self.assertEqual(RomTestPSP.query.endswith(col='rld').count(), 1)
        self.assertEqual(RomTestPSP.query.endswith(col='bad').count(), 0)
        self.assertEqual(RomTestPSP.query.like(col='?oin?').count(), 1)
        self.assertEqual(RomTestPSP.query.like(col='*oin+').count(), 1)
        self.assertEqual(RomTestPSP.query.like(col='oin').count(), 0)
        self.assertEqual(RomTestPSP.query.like(col='+oin').like(col='wor!d').count(), 1)
        self.assertEqual(RomTestPSP.query.like(col='*1').count(), 0)
        self.assertEqual(RomTestPSP.query.like(col='*r').count(), 1)

        self.assertEqual(RomTestPSP.query.startswith(col2="This is just").count(), 1)
        self.assertEqual(RomTestPSP.query.startswith(col2="this is just").count(), 0)
        self.assertEqual(RomTestPSP.query.endswith(col2="another Test").count(), 1)
        self.assertEqual(RomTestPSP.query.endswith(col2="another test").count(), 0)
        self.assertEqual(RomTestPSP.query.like(col2="This?is").count(), 1)
        self.assertEqual(RomTestPSP.query.like(col2="this?is").count(), 0)

        self.assertEqual(RomTestPSP.query.startswith(col3="and this is").count(), 1)
        self.assertEqual(RomTestPSP.query.startswith(col3="And This Is").count(), 1)
        self.assertEqual(RomTestPSP.query.startswith(col3="And This isn't").count(), 0)
        self.assertEqual(RomTestPSP.query.endswith(col3="yet another").count(), 1)
        self.assertEqual(RomTestPSP.query.endswith(col3="Yet another").count(), 1)
        self.assertEqual(RomTestPSP.query.endswith(col3="and another").count(), 0)
        self.assertEqual(RomTestPSP.query.like(col3="And?This").count(), 1)
        self.assertEqual(RomTestPSP.query.like(col3="and?this").count(), 1)
        self.assertEqual(RomTestPSP.query.like(col3="*this is*").count(), 1)
        self.assertEqual(RomTestPSP.query.like(col3="nope").count(), 0)

    def test_unicode_text(self):
        ch = unichr(0xfeff) if six.PY2 else chr(0xfeff)
        pre = ch + 'hello'
        suf = 'hello' + ch

        class RomTestUnicode1(Model):
            col = Text(index=True, unique=True, keygen=FULL_TEXT)
            col2 = Text(index=True, keygen=IDENTITY)

        RomTestUnicode1(col=pre, col2=pre).save()
        RomTestUnicode1(col=suf, col2=suf).save()

        self.assertEqual(RomTestUnicode1.query.filter(col=pre, col2=pre).count(), 1)
        self.assertEqual(RomTestUnicode1.query.filter(col=suf, col2=suf).count(), 1)
        self.assertTrue(RomTestUnicode1.get_by(col=pre))
        self.assertTrue(RomTestUnicode1.get_by(col2=pre))
        self.assertTrue(RomTestUnicode1.get_by(col=suf))
        self.assertTrue(RomTestUnicode1.get_by(col2=suf))

        class RomTestUnicode2(Model):
            col = Text(prefix=True, suffix=True, keygen=FULL_TEXT)
            col2 = Text(prefix=True, suffix=True, keygen=IDENTITY)

        RomTestUnicode2(col=pre, col2=pre).save()
        RomTestUnicode2(col=suf, col2=suf).save()

        self.assertEqual(RomTestUnicode2.query.startswith(col="h").count(), 1)
        self.assertEqual(RomTestUnicode2.query.startswith(col2="h").count(), 1)
        self.assertEqual(RomTestUnicode2.query.startswith(col=ch).count(), 1)
        self.assertEqual(RomTestUnicode2.query.startswith(col2=ch).count(), 1)
        self.assertEqual(RomTestUnicode2.query.endswith(col="o").count(), 1)
        self.assertEqual(RomTestUnicode2.query.endswith(col2="o").count(), 1)
        self.assertEqual(RomTestUnicode2.query.endswith(col=ch).count(), 1)
        self.assertEqual(RomTestUnicode2.query.endswith(col2=ch).count(), 1)

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
            a = ManyToOne('RomTestCleanupA', 'no action')

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
        aid = a.id
        apk = a._pk
        self.assertTrue(b.a)

        a.delete()  # Nullify FK on b.
        self.assertFalse(redis.hkeys('RomTestCleanupA:%d' % a.id))
        # verify removal from the session object
        self.assertFalse(RomTestCleanupA.get(aid))
        self.assertFalse(apk in session.known)
        self.assertFalse(apk in session.wknown)
        session.rollback() # XXX purge session cache

        b = RomTestCleanupB.get(b.id)
        self.assertFalse(b.a)

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

    def test_restrict_on_delete(self):
        """ Verify that Restrict is thrown when there is a foreign object referencing
            the deleted object."""

        class RomTestCascadeRestrict(Model):
            alist = OneToMany('RomTestRestrictA')

        class RomTestRestrictA(Model):
            foo = Text()
            cref = ManyToOne('RomTestCascadeRestrict', 'cascade')
            blist = OneToMany('RomTestRestrictB')

        class RomTestRestrictB(Model):
            bar = Text()
            a = ManyToOne('RomTestRestrictA', 'restrict')

        c = RomTestCascadeRestrict()
        c.save()
        a = RomTestRestrictA(foo='foo', cref=c)
        a.save()
        b = RomTestRestrictB(bar='foo', a=a)
        b.save()
        self.assertRaises(RestrictError, c.delete)
        self.assertRaises(RestrictError, a.delete)
        del a.cref
        a.save()
        c.delete()
        self.assertRaises(RestrictError, a.delete)

    def test_restrict_on_delete_121(self):
        """ Verify that Restrict is thrown when there is a foreign object referencing
            the deleted object."""

        class RomTestCascadeRestrictOne(Model):
            pass

        class RomTestRestrictAOne(Model):
            foo = Text()
            c = OneToOne('RomTestCascadeRestrictOne', 'cascade')

        class RomTestRestrictBOne(Model):
            a = OneToOne('RomTestRestrictAOne', 'restrict')
            bar = Text()

        c = RomTestCascadeRestrictOne()
        c.save()
        a = RomTestRestrictAOne(foo='foo', c=c)
        a.save()
        b = RomTestRestrictBOne(bar='foo', a=a)
        b.save()

        self.assertRaises(RestrictError, c.delete)
        self.assertRaises(RestrictError, a.delete)
        self.assertEqual(RomTestRestrictBOne.query.filter(a=NOT_NULL).count(), 1)
        del b.a
        b.save()
        c.delete()

    def _test_restrict_skip_on_delete(self):
        # Functionality currently disabled, might be useful in the future, but
        # currently differs from existing Postgres/MySQL/Oracle/MSSql behavior
        class RomTestRoot(Model):
            leaf = OneToMany('RomTestLeaf')
        class RomTestNode(Model):
            root = OneToOne('RomTestRoot', 'cascade')
            node = OneToOne('RomTestNode', 'cascade')
            leaf = OneToMany('RomTestLeaf')
        class RomTestLeaf(Model):
            node = ManyToOne('RomTestNode', 'restrict')
            root = ManyToOne('RomTestRoot', 'cascade')

        root = RomTestRoot()
        root.save()
        node1 = RomTestNode(root=root)
        node1.save()
        node2 = RomTestNode(node=node1) # assignment to an attribute auto-saves
        node2.save()

        l1 = RomTestLeaf(root=root, node=node2)
        l2 = RomTestLeaf(root=root, node=node2)
        l1.save()
        l2.save()

        ids = [
            (RomTestRoot, root.id),
            (RomTestNode, node1.id),
            (RomTestNode, node2.id),
            (RomTestLeaf, l1.id),
            (RomTestLeaf, l2.id),
        ]

        # resulting structure:
        #    root.node -> node1;.node -> node2;.leaf -> [l1, l2]
        #    root.leaf -> [l1, l2]
        # Cascading deletes use a breadth-first traversal of references, so the
        # queue of deletes when starting from the root will look like:
        #    [root, node1, l1, l2, node2]
        # Because l1 and l2 are deleted as part of the cascading delete from
        # the root, and *not* from the restricted delete from node2 (or node1),
        # a delete of the root should cascade fully (node1 is not necessary for
        # this to work; it's there for other tests).
        # Given the above, a delete from node1 or node2 should cause a
        # RestrictError.
        del root, node1, node2, l1, l2
        session.rollback()
        # from node1 gets a queue of:
        # [node1, node2] -restrict-> [l1, l2]
        self.assertRaises(RestrictError, RomTestNode.get(ids[2][1]).delete)
        session.rollback()
        # from node2 gets a queue of:
        # [node2] -restrict-> [l1, l2]
        self.assertRaises(RestrictError, RomTestNode.get(ids[1][1]).delete)

        session.rollback()
        RomTestRoot.get(ids[0][1]).delete()

        # make sure the deletions happened
        for mdl, id in ids:
            self.assertEqual(mdl.get(id), None)

    def test_on_delete_extra(self):
        def fail1():
            class RomTestFail(Model):
                col = OneToOne('OtherModel', 'set default', required=True)
        def fail2():
            class RomTestFail(Model):
                col = OneToOne('OtherModel', 'set null', required=True)
        self.assertRaises(ColumnError, fail1)
        self.assertRaises(ColumnError, fail2)

        class RomTestSetNull(Model):
            col = OneToOne('RomTestSetNull', 'set null')

        a = RomTestSetNull()
        b = RomTestSetNull(col=a)
        b.save()
        ia = a.id
        self.assertEqual(len(b.get_by(col=ia)), 1)
        a.delete()
        self.assertEqual(len(b.get_by(col=ia)), 0)

    def test_prefix_suffix1(self):
        class RomTestPerson(Model):
            name = Text(prefix=True, suffix=True, index=True, keygen=FULL_TEXT)

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
        class RomTestPerson2(Model):
            idPerson = string(prefix=True, suffix=True, index=True, keygen=FULL_TEXT)
            description = string(prefix=True, suffix=True, index=True, keygen=FULL_TEXT)
        data = [
            ["8947589545872", "ayuntamientodeciudad"],
            ["8947589545872", "ayuntamientodeguipuzcoa"],
            ["8947589545872", "ayuntamientodepalencia"],
            ["8947589545872", "ayuntamientodeciudad"],
            ["8937589569872", "ayuntamientodeburgos"],
            ["8947689545872", "ayuntamientodeburgos"],
            ["8947689545872", "ayuntamientodeburgos"],
            ["894789545872", "ayuntamientodeciudad"]
        ]
        cols = ['idPerson', 'description']
        for d in data:
            RomTestPerson2(**dict(zip(cols, d)))
        session.commit()

        self.assertEqual(RomTestPerson2.query.startswith(idPerson='89375').filter(description="ayuntamientodeburgos").count(), 1)
        self.assertEqual(RomTestPerson2.query.like(idPerson='*94*').filter(description="ayuntamientodeburgos").count(), 2)

    def test_null_session(self):
        class RomTestNullSession(Model):
            data = string()

        x = RomTestNullSession(data="test")
        x.save()
        session.rollback()

        util.use_null_session()
        y = RomTestNullSession.get(x.id)
        self.assertNotEqual(x, y)
        self.assertEqual(util.session.get(x._pk), None)

        util.use_rom_session()
        z = RomTestNullSession.get(x.id)
        self.assertEqual(util.session.get(x._pk), z)

        util.session.rollback()
        del x, y, z

    def test_saving_after_modifying_datetime_object(self):
        class RomTestObjectChangeAndSave(Model):
            col1 = DateTime()

        x = RomTestObjectChangeAndSave(col1=_now)
        self.assertTrue(x.save())

        y = RomTestObjectChangeAndSave.get(x.id)
        y.col1 = datetime.now()
        self.assertTrue(y.save())

    def test_index_cleared(self):
        class RomTestIndexClear(Model):
            col1 = Integer(index=True)

        for i in range(10):
            RomTestIndexClear(col1=i)

        session.commit()
        session.rollback()
        self.assertEqual(RomTestIndexClear.query.count(), 10)
        self.assertEqual(len(list(RomTestIndexClear.query)), 10)
        self.assertEqual(len(RomTestIndexClear.query.all()), 10)
        self.assertEqual(len(list(RomTestIndexClear.query.iter_result(no_hscan=True))), 10)
        self.assertEqual(len(list(RomTestIndexClear.query.limit(4, 10))), 6)
        self.assertEqual(len(list(RomTestIndexClear.query.limit(2, 4))), 4)
        self.assertEqual(len(RomTestIndexClear.query.limit(2, 4).all()), 4)

        for j in range(1, 11):
            RomTestIndexClear.get(j).delete()
        conn = connect(None)
        self.assertEqual(conn.hgetall('RomTestIndexClear::'), {})
        self.assertEqual(RomTestIndexClear.query.count(), 10)
        self.assertEqual(RomTestIndexClear.query.filter(col1=(0, 10)).all(), [])

    def test_multi_col_unique_index(self):
        class RomTestCompositeUnique(Model):
            col1 = Integer()
            col2 = Integer()
            col3 = string()

            unique_together = [
                ('col1', 'col2', 'col3'),
            ]

        for c1 in range(10):
            for c2 in range(10):
                for c3 in ('a', 'b', 'c', 'test', 'blah'):
                    RomTestCompositeUnique(col1=c1, col2=c2, col3=c3).save()

        self.assertRaises(UniqueKeyViolation, RomTestCompositeUnique(col1=5, col2=5, col3='c').save)

        a = RomTestCompositeUnique.get(5)
        a.col1 = 4
        self.assertRaises(UniqueKeyViolation, a.save)
        a.col3 = 'world'
        a.save()
        a.col1 = 0
        a.col3 = 'blah'
        a.save()

    def test_iter_result(self):
        # also tests alternate column names for the primary key, and an index
        # on the primary key
        class RomTestIterResult(Model):
            _id = PrimaryKey(index=True)
            id = Integer()
            col1 = Integer(index=True)
            alternate = Integer(required=True)

        ids = []
        for i in range(1, 51):
            a = RomTestIterResult(col1=i, alternate=1)
            a.save()
            ids.append(a._id)

        session.rollback()
        total = 0
        for it in RomTestIterResult.query.order_by('col1').iter_result(30, 10):
            total += it.col1
        session.rollback()
        self.assertEqual(total, 50 * 51 / 2)

        # test no decoding
        total = 0
        for it in RomTestIterResult.query.select('col1', decode=False).order_by('col1').iter_result(30, 10):
            total += int(it['col1'])
        self.assertEqual(total, 50 * 51 / 2)

        self.assertEqual(len(RomTestIterResult.query.select('col1', decode=False).order_by('col1').limit(0, 10).all()), 10)

        # test with decoding
        total = 0
        for it in RomTestIterResult.query.select('col1', decode=True).order_by('col1').iter_result(30, 10):
            total += it['col1']
        self.assertEqual(total, 50 * 51 / 2)

        from rom.query import _namedtuple_data_factory as ntf
        from rom.query import _tuple_data_factory
        from rom.query import _list_data_factory

        # test alternate factory function output
        total = 0
        for it in RomTestIterResult.query.select('col1', decode=True, ff=ntf).order_by('col1').iter_result(30, 10):
            total += it.col1
        self.assertEqual(total, 50 * 51 / 2)

        # test alternate factory function output
        total = 0
        for it in RomTestIterResult.query.select('col1', decode=True, ff=_tuple_data_factory).order_by('col1').iter_result(30, 10):
            total += it[0]
        self.assertEqual(total, 50 * 51 / 2)

        # test alternate factory function output
        total = 0
        for it in RomTestIterResult.query.select('col1', decode=True, ff=_list_data_factory).order_by('col1').iter_result(30, 10):
            total += it[0]
        self.assertEqual(total, 50 * 51 / 2)

        # test bare column factory function output
        def mf(ignore):
            def make(data):
                return data[0]
            return make

        total = 0
        for it in RomTestIterResult.query.select('col1', include_pk=False, decode=True, ff=mf):
            total += int(it)
        self.assertEqual(total, 50 * 51 / 2)

        # also test open-ended get_by() on the indexed primary key
        self.assertEqual(len(RomTestIterResult.get_by(_id=(None, None))), 50)
        # also test order-by on the indexed primary key
        self.assertEqual(len(RomTestIterResult.query.order_by('_id').all()), 50)
        session.rollback()

        # check select on the plain order by id...
        total = 0
        for it in RomTestIterResult.query.select('_id', decode=True, ff=ntf).order_by('_id'):
            total += it.id
        self.assertEqual(total, 50 * 51 / 2)

        # Let's get all the entities
        session.rollback()
        self.assertEqual(len(RomTestIterResult.query.all()), 50)

        # and get all the entities without an order by...
        total = 0
        for it in RomTestIterResult.query.select('col1', include_pk=False, decode=True, ff=ntf):
            total += it.col1
        self.assertEqual(total, 50 * 51 / 2)

        # And let's verify that it used the primary key index to iterate...
        c = connect(None)
        c.delete('%s:%s:idx'%(RomTestIterResult._namespace, RomTestIterResult._pkey))
        self.assertEqual(len(RomTestIterResult.query.all()), 0)
        session.rollback()

        # hack the primary key column to force using HSCAN when available
        RomTestIterResult._columns['_id']._index = False

        total = 0
        for it in RomTestIterResult.query.select('col1', include_pk=False, ff=ntf):
            total += int(it.col1)
        self.assertEqual(total, 50 * 51 / 2)

        self.assertEqual(len(RomTestIterResult.query.all()), 50)

        # And now use the naive query
        total = 0
        for it in RomTestIterResult.query.select('col1', include_pk=False, ff=ntf).iter_result(no_hscan=True):
            total += int(it.col1)
        self.assertEqual(total, 50 * 51 / 2)

        self.assertEqual(len(list(RomTestIterResult.query.iter_result(no_hscan=True))), 50)

    def test_foreign_model_references(self):
        class RomTestM2O(Model):
            col1 = ManyToOne('RomTestO2M', 'no action')
            col2 = ManyToOne('RomTestO2M', 'no action')

        def r():
            class RomTestO2M(Model):
                col1 = OneToMany('RomTestM2O')

        def ok():
            class RomTestO2M(Model):
                col1 = OneToMany('RomTestM2O', 'col2')

        self.assertRaises(ColumnError, r)
        ok()

        class RomTestO2M_(Model):
            col1 = OneToMany('RomTestM2O_')

        def r():
            class RomTestM2O_(Model):
                col1 = ManyToOne('RomTestO2M_', 'no action')
                col2 = ManyToOne('RomTestO2M_', 'no action')

        self.assertRaises(ColumnError, r)

        class RomTestO2M__(Model):
            col1 = OneToMany('RomTestM2O__', 'col2')

        class RomTestM2O__(Model):
            col1 = ManyToOne('RomTestO2M__', 'no action')
            col2 = ManyToOne('RomTestO2M__', 'no action')

    def _test_clean_old_index(self):
        class RomTestCleanOld(Model):
            _namespace = 'RomTestNamespacedCleanup'
            col1 = Integer(index=True)
            col2 = string(index=True, keygen=FULL_TEXT)
            col3 = string(unique=True)

        now = str(time.time())

        a = RomTestCleanOld(col1=6, col2="this is content that should be indexed", col3=now)
        a.save()
        id = a.id
        self.assertEqual(len(RomTestCleanOld.get_by(col1=6)), 1)
        self.assertEqual(len(RomTestCleanOld.get_by(col1=(5, 7))), 1)
        self.assertEqual(len(RomTestCleanOld.get_by(col2='content')), 1)
        self.assertTrue(RomTestCleanOld.get_by(col3=now))
        session.rollback()
        del a
        c = connect(None)
        #self.assertEqual(c.hlen('RomTestNamespacedCleanup::'), 1)
        self.assertEqual(RomTestCleanOld.query.count(), 1)
        self.assertEqual(c.scard('RomTestNamespacedCleanup:col2:content:idx'), 1)
        self.assertEqual(c.zcard('RomTestNamespacedCleanup:col1:idx'), 1)
        self.assertEqual(c.hlen('RomTestNamespacedCleanup:col3:uidx'), 1)

        self.assertEqual(c.delete('RomTestNamespacedCleanup:%s'%id), 1)

        with warnings.catch_warnings(record=True) as w:
            all(util.clean_old_index(RomTestCleanOld, force_hscan=None))
            self.assertEqual(len(w), 1)

        self.assertEqual(c.hlen('RomTestNamespacedCleanup::'), 0)
        # it's a lie!
        self.assertEqual(RomTestCleanOld.query.count(), 1)
        self.assertEqual(c.scard('RomTestNamespacedCleanup:col2:content:idx'), 0)

        self.assertEqual(c.zcard('RomTestNamespacedCleanup:col1:idx'), 0)
        # can't clean out unique index when force_hscan is None - aka HSCAN disabled
        self.assertEqual(c.hlen('RomTestNamespacedCleanup:col3:uidx'), 1)
        c.delete('RomTestNamespacedCleanup:col3:uidx')

        # okay, now test for longer scan/clear.
        minid = int(c.get('RomTestNamespacedCleanup:id:')) + 1
        _count = 200
        for i in range(minid, minid+_count):
            RomTestCleanOld(col1=i, col3=str(i)).save()
        session.rollback()

        version = list(map(int, c.info()['redis_version'].split('.')[:2]))
        has_hscan = version >= [2, 8]
        if has_hscan:
            self.assertEqual(len(RomTestCleanOld.query.all()), _count)

        to_delete = list(range(minid, minid + _count, 37))
        c.delete(*['RomTestNamespacedCleanup:%i'%i for i in to_delete])
        self.assertTrue(all(c.hexists('RomTestNamespacedCleanup::', i) for i in to_delete))
        all(util.clean_old_index(RomTestCleanOld, 10, force_hscan=has_hscan))
        self.assertTrue(all(not c.hexists('RomTestNamespacedCleanup::', i) for i in to_delete))
        if has_hscan:
            self.assertTrue(all(not c.hexists('RomTestNamespacedCleanup:col3:uidx', i) for i in to_delete))

        to_delete = list(range(minid+29, minid + _count, 29))
        c.delete(*['RomTestNamespacedCleanup:%i'%i for i in to_delete])
        #self.assertTrue(all(c.hexists('RomTestNamespacedCleanup::', i) for i in to_delete))
        # should cause a warning
        with warnings.catch_warnings(record=True) as w:
            all(util.clean_old_index(RomTestCleanOld, 10, force_hscan=None))
            self.assertEqual(len(w), 1)
        self.assertTrue(all(not c.hexists('RomTestNamespacedCleanup::', i) for i in to_delete))
        # We can't really clean out unique indexes when hscan is disabled or not
        # available. :/
        self.assertTrue(all(c.hexists('RomTestNamespacedCleanup:col3:uidx', i) for i in to_delete))

    def test_multi_query(self):
        class RomTestIndexMultiCol(Model):
            attr1 = string(required=True, index=True, keygen=FULL_TEXT)
            attr2 = string(required=True, index=True, keygen=FULL_TEXT)
            attr3 = String(required=True, index=True, default=b'', keygen=FULL_TEXT)

        a = RomTestIndexMultiCol(attr1='548ef7ee7b77b93ab41ksjh3', attr2='2')
        a.save()

        import uuid
        RomTestIndexMultiCol(attr1=str(uuid.uuid4()), attr2='4', attr3=b'Voting').save()
        RomTestIndexMultiCol(attr1=str(uuid.uuid4()), attr2='4', attr3=b'boating').save()

        self.assertEqual(len(RomTestIndexMultiCol.query.filter(attr1='548ef7ee7b77b93ab41ksjh3').execute()), 1)
        self.assertEqual(len(RomTestIndexMultiCol.query.filter(attr2=['1', '2']).execute()), 1)
        self.assertEqual(len(RomTestIndexMultiCol.query.filter(attr1='548ef7ee7b77b93ab41ksjh3', attr2='2').execute()), 1)
        self.assertEqual(len(RomTestIndexMultiCol.query.filter(attr1='548ef7ee7b77b93ab41ksjh3', attr2=['2', '1']).execute()), 1)
        self.assertEqual(len(RomTestIndexMultiCol.query.filter(attr1='548ef7ee7b77b93ab41ksjh3', attr2=['1']).execute()), 0)
        self.assertEqual(len(RomTestIndexMultiCol.query.filter(attr3=b'boating').execute()), 1)
        self.assertEqual(len(RomTestIndexMultiCol.query.filter(attr3=b'voting').execute()), 1)
        self.assertEqual(len(RomTestIndexMultiCol.query.filter(attr3=b'Voting').execute()), 1)

    def test_issue_114(self):
        import uuid
        class RomTestIMASentence(Model):
                uuid = String(required=True, unique=True)
                section = String(required=True, index=True, keygen=FULL_TEXT)

        RomTestIMASentence(section='Voting', uuid=uuid.uuid4().bytes).save()
        self.assertEqual(RomTestIMASentence.query.filter(section='Voting').count(), 1)

    def test_namespace(self):
        _ex = {'prefix':True, 'suffix':True, 'keygen':FULL_TEXT}
        class TestNamespace(Model):
            _namespace = 'RomTestNamespace'
            test_i = Integer(index=True)
            test_s = string(unique=True, **_ex)
            test_t = string(index=True, keygen=FULL_TEXT)

        a = TestNamespace(test_i=4, test_s='hello', test_t='this is a test')
        a.save()
        session.add(a)

        # make sure no strange keys make it through
        self.assertEqual(util.CONNECTION.keys('TestNamespace*'), [])
        # make sure that there are keys named as we wanted them named
        self.assertTrue(len(util.CONNECTION.keys('RomTestNamespace*')) > 0)

        # verify that our indexes work the way we want them to...
        self.assertEqual(a.get_by(test_i=4), [a])
        self.assertEqual(a.get_by(test_i=(3, 5)), [a])
        self.assertEqual(a.get_by(test_i=6), [])
        self.assertEqual(a.get_by(test_s='hello'), a)
        self.assertEqual(a.query.startswith(test_s='hel').all(), [a])
        self.assertEqual(a.query.startswith(test_s='hel0').all(), [])
        self.assertEqual(a.query.endswith(test_s='lo').all(), [a])
        self.assertEqual(a.query.endswith(test_s='llo').all(), [a])
        self.assertEqual(a.query.endswith(test_s='elo').all(), [])
        self.assertEqual(a.get_by(test_t='this'), [a])
        self.assertEqual(a.get_by(test_t=['test', 'blah']), [a])
        self.assertEqual(a.query.count(), 1)

    def test_order_string_index(self):
        class RomTestOrderString(Model):
            test_s = string(index=True, keygen=SIMPLE)
            test_c = string(index=True, keygen=SIMPLE_CI)

        RomTestOrderString(test_s='Hello', test_c='World 2').save()
        RomTestOrderString(test_s='hello', test_c='world 1').save()
        # test case-sensitive ordering
        r1 = RomTestOrderString.query.order_by('test_s').all()
        self.assertEqual(len(r1), 2)

        if sys.version < '2.7':
            self.assertTrue(r1[0].id < r1[1].id)
        else:
            self.assertLess(r1[0].id, r1[1].id)

        # test case-insensitive ordering
        r2 = RomTestOrderString.query.order_by('test_c').all()
        self.assertEqual(len(r2), 2)
        if sys.version < '2.7':
            self.assertTrue(r2[0].id > r2[1].id)
        else:
            self.assertGreater(r2[0].id, r2[1].id)

    def test_string_in_all(self):
        by = 'hello'.encode('utf-8')
        class RomTestByteString(Model):
            scol = String(unique=True, index=True, suffix=True, keygen=FULL_TEXT)

        RomTestByteString(scol=by).save()
        self.assertTrue(RomTestByteString.get_by(scol=by))
        self.assertEqual(RomTestByteString.query.filter(scol=by).count(), 1)
        self.assertEqual(RomTestByteString.query.endswith(scol=by[1:]).count(), 1)
        self.assertRaises(UniqueKeyViolation, RomTestByteString(scol=by).save)

    def test_weird(self):
        ip = '192.168.10.22'
        class RomTestSlaveServers(Model):
            ip_address = String(required=True, unique=True)
            free_slots = Integer(required=True, default=10)
            status = String(required=True, default=b'idle')

        RomTestSlaveServers(ip_address=ip).save()
        self.assertRaises(UniqueKeyViolation, RomTestSlaveServers(ip_address=ip).save)

    def test_hooks(self):
        class RomTestHooks(Model):
            a = Integer(default=0)
            def _before_insert(self):
                self.a += 1
            def _after_insert(self):
                self.a += 4
            def _before_update(self):
                self.a += 16
            def _after_update(self):
                self.a += 64
            def _before_delete(self):
                self.a += 256
            def _after_delete(self):
                self.a += 1024

        d = RomTestHooks()
        self.assertEqual(d.a, 0)
        self.assertTrue(d._new)
        d.save()
        self.assertFalse(d._new)
        self.assertEqual(d.a, 5)
        d.save() # was modified, thanks to our hooks ;)
        self.assertEqual(d.a, 85)
        d.delete()
        self.assertEqual(d.a, 1365)
        self.assertTrue(d._deleted)

        def raise_exception():
            raise ValueError("What!")

        # Make sure that exceptions in the methods cause the operation to fail
        d = RomTestHooks()
        del d.a
        self.assertRaises(TypeError, d.save)
        self.assertTrue(d._new)
        d._before_insert = raise_exception
        self.assertRaises(ValueError, d.save)
        del d._before_insert
        d.a = 0
        d._after_insert = raise_exception
        self.assertRaises(ValueError, d.save)
        self.assertEqual(d.a, 1)
        del d._after_insert
        d.a = 0

        # test for updates
        self.assertFalse(d._new)
        del d.a
        self.assertRaises(TypeError, d.save)
        d._before_update = raise_exception
        self.assertRaises(ValueError, d.save)
        del d._before_update
        d.a = 0
        d._after_update = raise_exception
        self.assertRaises(ValueError, d.save)
        self.assertEqual(d.a, 16)
        del d._after_update
        d.a = 0

        # and again for deletes
        del d.a
        self.assertRaises(TypeError, d.delete)
        d._before_delete = raise_exception
        self.assertRaises(ValueError, d.delete)
        del d._before_delete
        d.a = 0
        d._after_delete = raise_exception
        self.assertRaises(ValueError, d.delete)
        self.assertEqual(d.a, 256)

    def test_data_race(self):
        class RomTestDataRace(Model):
            col = Integer()
        x = RomTestDataRace(col=5)
        x.save()
        session.rollback()
        y = RomTestDataRace.get(x.id)
        self.assertTrue(y)
        self.assertNotEqual(x, y)
        y.col = 6
        y.save()
        session.rollback()
        x.col = 7
        self.assertRaises(DataRaceError, x.save)
        self.assertRaises(DataRaceError, session.commit)
        x.refresh(force=True)
        x.save()
        y.delete()
        self.assertRaises(EntityDeletedError, x.save)
        x.save(force=True)

    def test_keygen2(self):
        def kg2(attr, data):
            keys = set(FULL_TEXT(data.get('a')) or [])
            keys.update(FULL_TEXT(data.get('b')) or [])
            return keys

        class RomTestKeygen2(Model):
            a = string(index=True, keygen2=kg2)
            b = string()

        RomTestKeygen2(a='hello world', b='how are you').save()
        self.assertEqual(RomTestKeygen2.query.filter(a='hello').filter(a='are').count(), 1)

    def test_multiindex(self):
        def kg(val):
            keys = dict.fromkeys(val.split())
            for k in list(keys):
                if k.isdigit():
                    keys['v:'+k] = int(k)
            return keys

        class RomTestMultiindex(Model):
            a = string(index=True, keygen=kg)

        RomTestMultiindex(a='hello world 123').save()
        self.assertEqual(RomTestMultiindex.query.filter(a='hello').count(), 1)
        self.assertEqual(RomTestMultiindex.query.filter(**{'a:v:123':(120, 125)}).count(), 1)

    def test_multiindex_sort(self):
        def kg(val):
            keys = dict.fromkeys(val.split())
            kk = 0
            for k in list(keys):
                if k.isdigit():
                    kk = int(k)
            for k in list(keys):
                keys['v:'+k] = kk

            return keys

        class RomTestMultiSort(Model):
            a = string(index=True, keygen=kg)

        RomTestMultiSort(a='hello world 123').save()
        RomTestMultiSort(a='other hello 999').save()
        self.assertEqual(RomTestMultiSort.query.filter(a='v:hello').count(), 2)
        self.assertEqual(RomTestMultiSort.query.filter(**{'a:v:hello':(120, 1000)}).count(), 2)
        r = RomTestMultiSort.query.filter(**{'a:v:hello':(120, 1000)}).order_by('a:v:hello').all()
        self.assertEqual(len(r), 2)
        self.assertTrue(r[0].a.startswith('hello world'))
        self.assertTrue(r[1].a.startswith('other hello'))

    def test_issue_128(self):
        def find_and_sort_keygen(data):
            r = SIMPLE(data)
            r[data] = None
            return r
        class RomTestText(Model):
            txt_comp = Text(index=True, keygen=find_and_sort_keygen)

        txt1 = RomTestText(txt_comp='BLACK')
        txt2 = RomTestText(txt_comp='BROWN')
        txt3 = RomTestText(txt_comp='BRONZE')
        txt4 = RomTestText(txt_comp='RED')
        txt5 = RomTestText(txt_comp='BROWN')
        txt6 = RomTestText(txt_comp='BLACK')
        txt7 = RomTestText(txt_comp='BROWN')

        session.commit()

        self.assertEqual(RomTestText.query.filter(txt_comp='BLACK').count(), 2)
        self.assertEqual(len(RomTestText.query.order_by('txt_comp').all()), 7)
        self.assertEqual(RomTestText.query.select('txt_comp').order_by('txt_comp').all(),
            [{'txt_comp': u'BLACK'}, {'txt_comp': u'BLACK'}, {'txt_comp': u'BRONZE'},
             {'txt_comp': u'BROWN'}, {'txt_comp': u'BROWN'}, {'txt_comp': u'BROWN'},
             {'txt_comp': u'RED'}])



    ## def test_script_flush(self):
        ## c = connect(None)
        ## script = util._script_load('''
            ## return 1
        ## ''')
        ## self.assertEqual(script(c), 1)
        ## c.execute_command('SCRIPT', 'FLUSH', parse="FLUSH")
        ## self.assertEqual(script(c), 1)

    def test_binary2(self):
        class RomTestBinaryData2(Model):
            value = String()
        self.test_binary(RomTestBinaryData2, ''.join(chr(i) for i in range(256)))

    def test_binary(self, RomTestBinaryData=None, bad=None):
        if not bad:
            bad = base64.b64decode(b'UEsDBBQAAAAIACUdX0djd8CXNQEAAAoCAAAYAAAAeGwvd29ya3NoZQ==')
            if six.PY3:
                bad = bad.decode('latin-1')

        if not RomTestBinaryData:
            class RomTestBinaryData(Model):
                value = String()

        d = RomTestBinaryData(value=bad)
        d.save()

        id = d.id
        del d
        session.rollback()
        d = RomTestBinaryData.get(id)
        ## print(type(dv), type(bad))
        if six.PY3:
            # This right here is ridiculous. The 2.x way was 10x better.
            ## print(binascii.hexlify(d.value))
            ## print(binascii.hexlify(bad.encode('latin-1')))
            self.assertEqual(d.value, bad.encode('latin-1'))
        else:
            ## print(d.value.encode('hex'))
            ## print(bad.encode('hex'))
            self.assertEqual(d.value, bad)

    def test_geo(self):
        conn = connect(None)
        version = list(map(int, conn.info()['redis_version'].split('.')))
        if version < [3, 2]:
            print("Skipping geo tests")
            return

        class RomTestGeo(Model):
            tags = String(index=True, keygen=FULL_TEXT)
            lat = Float()
            lon = Float()
            geo_index = [
                # also test out attrdict in here :)
                GeoIndex('basic', lambda x: {'lat':x.lat, 'lon':x['lon']})
            ]

        a = RomTestGeo(lat=50, lon=0, tags='channel')
        a.save()
        self.assertEqual(a.query.filter(tags='channel').near('basic', .5, 50, 60, 'km').count(), 1)
        self.assertEqual(a.query.filter(tags='park').near('basic', .5, 50, 60, 'km').count(), 0)
        self.assertEqual(a.query.filter(tags='channel').near('basic', 0, 50.5, 60, 'km').count(), 1)
        self.assertEqual(a.query.filter(tags='park').near('basic', 0, 50.5, 60, 'km').count(), 0)
        self.assertEqual(a.query.filter(tags='channel').near('basic', 1, 50, 60, 'km').count(), 0)
        self.assertEqual(a.query.filter(tags='channel').near('basic', 0, 51, 60, 'km').count(), 0)

        RomTestGeo(lat=50.99476, lon=0, tags='park').save()
        self.assertEqual(a.query.filter(tags='park').near('basic', 0, 51, 60, 'km').count(), 1)
        a.query.filter(tags='park').near('basic', 0, 50, 100, 'km').delete()
        self.assertEqual(a.query.filter(tags='park').near('basic', 0, 50, 60, 'km').count(), 0)

    def test_filter_delete(self):
        class RomTestFilterPerformance(Model):
            id = PrimaryKey(index=True)

        TEST_PERF = 0

        ids = []
        for i in range(10000 if TEST_PERF else 100):
            a = RomTestFilterPerformance()
            a.save()
            ids.append(a.id)
        session.rollback()

        if TEST_PERF:
            t = time.time()
            for i in range(100):
                RomTestFilterPerformance.query.filter(id=(1, 1)).cached_result(30)
            ela = time.time()-t
            print("\nelapsed: ", ela)
            print("qps: ", 100 / ela)

        self.assertEqual(RomTestFilterPerformance.query.filter(id=(ids[0], ids[-1])).count(), len(ids))
        t = time.time()
        RomTestFilterPerformance.query.filter(id=(ids[0], ids[-1])).delete(blocksize=len(ids))
        delta = time.time() - t
        self.assertEqual(RomTestFilterPerformance.query.filter(id=(ids[0], ids[-1])).count(), 0)

        if TEST_PERF:
            print('delete items in', delta, len(ids) / delta)

    def test_recursive_model(self):
        class RomTestRecursiveModel(Model):
            manager = ManyToOne('RomTestRecursiveModel', on_delete='restrict')
            reports = OneToMany('RomTestRecursiveModel')

        a = RomTestRecursiveModel()
        a.save()
        b = RomTestRecursiveModel(manager=a)
        b.save()
        self.assertEqual(a, b.manager)
        self.assertEqual(a.reports, [b])

    def test_lock(self):
        class RomTestModelLock(Model):
            example = Integer()

        a = RomTestModelLock()
        a.save()
        l = util.EntityLock(a, 1, 5)
        self.assertTrue(l.acquire())

        l2 = util.EntityLock(a, 1, 1)
        self.assertFalse(l2.acquire())
        self.assertTrue(l.refresh())
        self.assertTrue(l.release())

    def test_empty_keygen_result(self):
        class RomTestEmptyKeygen(Model):
            col = Text(required=True, index=True, keygen=FULL_TEXT, prefix=True)

        a = RomTestEmptyKeygen(col=u'& &')
        a.save()
        aid = a.id
        del a
        session.rollback()

        b = RomTestEmptyKeygen.get(aid)
        self.assertTrue(b.col)

    def test_type_check_datetime(self):
        class RomTestCheckedColumns(Model):
            c = SaferDateTime()

        # should be okay
        a = RomTestCheckedColumns(c=datetime(1970, 1, 2))
        a.save()

        # also should work, because we need to round trip Redis
        # Fail on setting attributes
        self.assertRaises(InvalidColumnValue, lambda: setattr(a, 'c', 1))
        self.assertRaises(InvalidColumnValue, lambda: setattr(a, 'c', 'blah'))
        self.assertRaises(InvalidColumnValue, lambda: setattr(a, 'c', '1'))
        self.assertRaises(InvalidColumnValue, lambda: setattr(a, 'c', ()))
        self.assertRaises(InvalidColumnValue, lambda: setattr(a, 'c', []))
        self.assertRaises(InvalidColumnValue, lambda: setattr(a, 'c', {}))
        a.c = datetime(1970, 1, 3)
        a.save()

        # Should fail!
        self.assertRaises(InvalidColumnValue, lambda: RomTestCheckedColumns(c=86400))
        self.assertRaises(InvalidColumnValue, lambda: RomTestCheckedColumns(c='86400'))

    def test_issue_115(self):
        class RomTestCategory(Model):
            name = String()

        a = RomTestCategory(name='D\xe9cor'); a.save()
        self.assertRaises(UnicodeEncodeError, lambda: RomTestCategory(name=u'D\ufffdcor'))
        self.assertEqual(len(RomTestCategory.query.execute()), 1)
        self.assertEqual(len(RomTestCategory.query.execute()), 1)

    def test_issue_118(self):
        class RomTestUndxModel(Model):
            txtkey = Text(required=True, unique=True)
            intkey = Integer(unique=True)

        self.assertRaises(MissingColumn, RomTestUndxModel)

        self.assertRaises(InvalidColumnValue, lambda: RomTestUndxModel(txtkey='hello', foo='bar'))
        item = RomTestUndxModel(txtkey='hello')
        item.save()
        a = item.to_dict()
        a.pop('id')
        self.assertEqual(a, {'txtkey': 'hello', 'intkey': None})

        self.assertRaises(AttributeError, lambda: item.foo)

        self.assertRaises(MissingColumn, lambda: RomTestUndxModel(intkey=20))
        item = None
        session.rollback()

        self.assertEqual(RomTestUndxModel.query.all()[0].intkey, None)

        self.assertEqual(RomTestUndxModel.query.select('txtkey', 'intkey').all(), [{'txtkey': 'hello', 'intkey': None}])

    def test_issue_125(self, null_session=True):
        m = MODELS['Model']
        MODELS.clear()
        MODELS['Model'] = m
        MODELS_REFERENCED.clear()
        class RomTestItem(Model):
            id = PrimaryKey(index=True)
            hash = Text(index=True, keygen=IDENTITY, prefix=True)
            tags = Json()
            company = ManyToOne(ftable='RomTestCompany',  on_delete='no action')

        class RomTestCompany(Model):
            id = PrimaryKey(index=True)
            name    = Text(index=True, keygen=IDENTITY_CI, prefix=True)

            #### ONE Company manufactures MANY Items
            items   = OneToMany(ftable='RomTestItem', column='company')

        if null_session:
            util.use_null_session()
            c = RomTestCompany(name='test')
            c.save()
            RomTestItem(hash='not_a_hash', tags=['tag1', 'tag2'], company=c).save()
        else:
            util.use_rom_session()
            c = RomTestCompany(name='test')
            self.assertTrue(session.commit())
            RomTestItem(hash='not_a_hash', tags=['tag1', 'tag2'], company=c)
            self.assertTrue(session.commit())

        i = None
        for i in RomTestItem.query.select('id', 'hash', 'tags', 'company').limit(0, 10).all():
            self.assertEqual(i['company'].name, 'test')
        self.assertTrue(i != None)
        util.use_rom_session()

    def test_issue_125_2(self):
        self.test_issue_125(False)

    def test_transfer_with_items(self):
        """
        Note: this tests both integer and floating point at the same time.
        You'll just want to use the one, not both. I suggest integer.
        But we support floats too. Because you let the folks stab themselves in
        the foot.
        """
        global_setup(a=0)

        transfer_item_lua = util._script_load("""
        -- keys - {ledger pk, dest pk}
        -- argv - {attr, complete}
        if tonumber(redis.call("hget", KEYS[1], ARGV[2])) < 1 then
            return {0, "item not paid for"}
        end
        local items = cjson.decode(redis.call("hget", KEYS[1], ARGV[1]))
        local dest = cjson.decode(redis.call("hget", KEYS[2], ARGV[1]))
        if #items == 0 then
            return {0, "already transferred"}
        end
        if #dest == 1 and not dest[1] then
            dest = {}
        end
        for i, v in ipairs(items) do
            dest[#dest + 1] = v
        end
        redis.call('hset', KEYS[1], ARGV[1], "[]")
        redis.call('hset', KEYS[2], ARGV[1], cjson.encode(dest))
        return {1, cjson.encode(dest)}
        """)
        def transfer_item(ledger, dest, attr, complete):
            lp = ledger._pk
            dp = dest._pk
            ledger.refresh()
            dest.refresh()
            r = transfer_item_lua(ledger._connection, [ledger._pk, dest._pk], [attr, complete])
            if not r[0]:
                raise ValueError(r[1])
            ledger.refresh()
            dest.refresh()

        class RomTestBalance(Model):
            ivalue = Integer(index=True)
            fvalue = Float(index=True)
            items = Json()

        def status_keygen(attr, dct):
            """
            Indexes for prices as necessary, then state as the item is sold and
            transferred:

            values:
                ``>0``: no buyer
                ``0``: has a buyer, money not transferred
                ``-1``: money has been transferred, items not yet
                ``-2``: items have been transferred, ready to delete ledger

            """
            if not dct["buyer"]:
                return {"": dct["iprice"] or dct["fprice"]}

            if int(dct["complete"] or 0):
                if dct["items"] != "[]":
                    return {"": -1} # ready to transfer items!

                return {"": -2} # already transferred items

            # money is not transferred yet, so need to do that
            return {"": 0}

        class RomTestLedgerItems(Model):
            seller = ManyToOne("RomTestBalance", "restrict")
            buyer = ManyToOne("RomTestBalance", "restrict")
            complete = Integer(default=0, index=True)
            ivalue = Integer(default=0, index=True)
            fvalue = Float(default=0, index=True)
            items = Json()
            state_index = IndexOnly(keygen2=status_keygen)

        tests = [
          ((5, 0, []), (5, 0, []), (3, 0, [2,3,4]), True),
          ((5, 0, []), (5, 0, []), (6, 0, [2,3,4]), False),
          ((0, 3.45336, []), (0, 6.3, []), (0, 0.83291, [2,3,4]), True),
          ((0, 4.25425, []), (0, 7.3, []), (0, 4.83291, [2,3,4]), False),
          ((0, 3.45336, []), (0, 6.3, []), (0, 0.83291, [2,3,4], 1), False),
          ((0, 3.45336, []), (0, 6.3, []), (0, 0.83291, [], 1), False)
        ]

        k = 'ivalue', 'fvalue', 'items', 'complete'
        md = lambda x: dict(zip(k, x))
        for buyer, seller, item, success in tests:
            bd = md(buyer)
            a = RomTestBalance(**bd)
            a.save()
            sd = md(seller)
            b = RomTestBalance(**sd)
            b.save()
            cd = md(item)
            cd["seller"] = b
            cd["buyer"] = a
            c = RomTestLedgerItems(**cd)
            c.save()

            if success:
                which = "ivalue" if buyer[0] else "fvalue"
                dec = 0 if which == "ivalue" else 5
                f = lambda: transfer_item(c, a, "items", "complete")
                self.assertRaises(ValueError, f)
                a.transfer(b, which, c.ivalue or c.fvalue, c, "complete", dec)
                f()
                self.assertTrue(a.items)
                self.assertFalse(c.items)
                self.assertEqual(getattr(a, which) + getattr(c, which), bd[which])
                self.assertEqual(getattr(b, which) - getattr(c, which), sd[which])

            else:
                f = lambda: a.transfer(b, which, c.ivalue or c.fvalue, c, "complete", dec)
                self.assertRaises(ValueError, f)
                f = lambda: transfer_item(c, a, "items", "complete")

                if len(item) == 4 and item[2]:
                    f()
                    self.assertTrue(a.items)
                    self.assertFalse(c.items)
                elif item[2]:
                    self.assertRaises(ValueError, f)
                    self.assertFalse(a.items)
                    self.assertTrue(c.items)


    def test_iter_excludes(self):
        def basic(val):
            if val:
                return [val.lower()]
            return []
        class RomTestExcludes(Model):
            pfix = Text(prefix=True, keygen=basic)
            sfix = Text(suffix=True, keygen=basic)

        global_setup(0)

        a = RomTestExcludes(pfix="hello", sfix="world")
        a.save()
        b = RomTestExcludes(pfix="other", sfix="things")
        b.save()
        c = RomTestExcludes(pfix="longer_prefix_other", sfix="longer_suffix")
        c.save()

        self.assertEqual(len(list(a.does_not_startwith('pfix', "world"))), 3)
        self.assertEqual(len(list(a.does_not_startwith('pfix', "othe"))), 2)
        self.assertEqual(len(list(a.does_not_startwith('pfix', "hel"))), 2)
        self.assertEqual(len(list(a.does_not_startwith('pfix', "longer_prefix"))), 2)
        self.assertEqual(len(list(a.does_not_startwith('pfix', "long"))), 2)
        self.assertEqual(len(list(a.does_not_startwith('pfix', "longer_unmatched"))), 3)

        self.assertEqual(len(list(a.does_not_endwith('sfix', "stuff"))), 3)
        self.assertEqual(len(list(a.does_not_endwith('sfix', "ings"))), 2)
        self.assertEqual(len(list(a.does_not_endwith('sfix', "rls"))), 3)
        self.assertEqual(len(list(a.does_not_endwith('sfix', "rld"))), 2)
        self.assertEqual(len(list(a.does_not_endwith('sfix', "er_suffix"))), 2)
        self.assertEqual(len(list(a.does_not_endwith('sfix', "fix"))), 2)
        self.assertEqual(len(list(a.does_not_endwith('sfix', "unmatched_suffix"))), 3)

def main():
    global_setup()
    try:
        unittest.main()
    finally:
        global_setup()


if __name__ == '__main__':
    main()
