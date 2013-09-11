from setup import *
from rom import *
from rom.exceptions import *
from pytest import raises


class RomTestNormal(Model):
    attr = String()
    
def test_save_inline_with_no_attributes():
    assert(RomTestNormal().save())

def test_save_inline_with_attributes():
    assert(RomTestNormal(attr='hello').save())

def test_save_with_no_changes():
    x = RomTestNormal()
    assert(x.save())
    assert(x.save() != True )
    assert(x is RomTestNormal.get(x.id))


def test_create_w_id():
    '''
    You aren't allowed to create a new object and specify its id
    that happens internally.
    '''
    with raises(InvalidColumnValue):
        RomTestNormal(id = 1)
