import time
from setup import *
from rom import *
from rom.exceptions import *
from pytest import raises


class RomTestBasic(Model):
    val = Integer()
    oval = Integer(default=7)
    created_at = Float(default=time.time)
    req = String(required=True)

def test_column_error():
    with raises(ColumnError):
        RomTestBasic()

def test_invalid_column_value():
    with raises(InvalidColumnValue):
        RomTestBasic(oval='t')

def test_missing_column_value():
    with raises(MissingColumn):
        RomTestBasic(created_at=7)


def test_object_save_load():
    x = RomTestBasic(val=1, req="hello")
    x.save()
    id = x.id
    x = x.to_dict()

    y = RomTestBasic.get(id)
    y = y.to_dict()
    assert(x == y)

