from pytest import raises
from setup import *
from rom import *
from rom.exceptions import *


class RomTestIndexModel(Model):
    key = String(required=True, unique=True)

class RomTestUnique(Model):
    attr = String(unique=True)


def test_bad_index_value():
    with raises(ColumnError):
        class RomTestBadIndexModel2(Model):
            bad = Integer(unique=True)

def test_missing_required_unique():
    with raises(MissingColumn):
        RomTestIndexModel()

def test_object_save_and_load():
    item = RomTestIndexModel(key="hello")
    item.save()
    m = RomTestIndexModel.get_by(key="hello")
    assert(m)
    assert(m.id == item.id)
    assert(m is item)

def test_unique_contstraint_violation():
    a = RomTestUnique(attr='hello')
    b = RomTestUnique(attr='hello2')
    a.save()
    b.save()
    b.attr = 'hello'
    with raises(UniqueKeyViolation):
        b.save()
    
    c = RomTestUnique(attr='hello')
    with raises(UniqueKeyViolation):
        c.save()
