from setup import *
from rom import *
from decimal import Decimal as _Decimal


class RomTestIndexedModel(Model):
    attr = String(index=True)
    attr2 = String(index=True)
    attr3 = Integer(index=True)
    attr4 = Float(index=True)
    attr5 = Decimal(index=True)


def initialize():
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
    return x

def test_count():
    initialize()
    assert( RomTestIndexedModel.query.filter(attr='hello').count() == 1 )


def test_chained_filters():
    initialize()
    assert(RomTestIndexedModel.query.filter(attr2='how').filter(attr2='are').count() == 1)
    assert(RomTestIndexedModel.query.filter(attr='hello', noattr='bad').filter(attr2='how').filter(attr2='are').count() == 0)

def test_filter_range():
    initialize()
    assert(RomTestIndexedModel.query.filter(attr='hello', attr3=(None, None)).count() == 1)
    assert(RomTestIndexedModel.query.filter(attr='hello', attr3=(None, 10)).count() == 1)
    assert(RomTestIndexedModel.query.filter(attr='hello', attr3=(None, 10)).count() == 1)
    assert(RomTestIndexedModel.query.filter(attr='hello', attr3=(None, 10)).execute()[0].id == 1)
    assert(RomTestIndexedModel.query.filter(attr='hello', attr3=(5, None)).count() == 1)
    assert(RomTestIndexedModel.query.filter(attr='hello', attr3=(5, 10), attr4=(4,5), attr5=(2.5, 2.7)).count() == 1)


def test_filter_verify_objects():
    x = initialize()
    assert(RomTestIndexedModel.query.filter(attr='hello', attr3=(5, 10), attr4=(4,5), attr5=(2.5, 2.7)).count() == 1)
    first = RomTestIndexedModel.query.filter(attr='hello', attr3=(5, 10), attr4=(4,5), attr5=(2.5, 2.7)).first()
    assert(first)
    assert(first is x)
    
def test_filter_matching():
    initialize()
    assert(RomTestIndexedModel.query.filter(attr='hello', attr3=(10, 20), attr4=(4,5), attr5=(2.5, 2.7)).count() == 0)
    assert(RomTestIndexedModel.query.filter(attr3=100).count(), 1)
    assert(RomTestIndexedModel.query.filter(attr='world', attr5=_Decimal('2.643')).count() == 2)

def test_results():
    x = initialize()
    assert(RomTestIndexedModel.query.filter(attr='hello', attr3=(10, 20), attr4=(4,5), attr5=(2.5, 2.7)).count() == 0)
    assert(RomTestIndexedModel.query.filter(attr3=100).count(), 1)
    assert(RomTestIndexedModel.query.filter(attr='world', attr5=_Decimal('2.643')).count() == 2)
    results = RomTestIndexedModel.query.filter(attr='world').order_by('attr4').execute()
    assert([x.id for x in results], [2,1])
