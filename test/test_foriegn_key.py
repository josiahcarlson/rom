from pytest import raises
from setup import *
from rom import *
from rom.exceptions import ORMError


class RomTestFkey1(Model):
    fkey2 = ManyToOne("RomTestFkey2")
    
class RomTestFkey2(Model):
    fkey1 = OneToMany("RomTestFkey1")


def test_bad_many_to_one():
    with raises(ORMError):
        class RomTestKey1(Model):
            bad = ManyToOne("Bad")
        RomTestKey1()

def test_bad_one_to_many():
    with raises(ORMError):
        class RomTestKey2(Model):
            bad = OneToMany("Bad")
        RomTestKey2()

def test_save_and_load():
    x = RomTestFkey2()
    y = RomTestFkey1(fkey2=x) # implicitly saves x
    y.save()
    
    xid = x.id
    yid = y.id
    x = y = None
    y = RomTestFkey1.get(yid)
    
    assert(y.fkey2.id == xid)
    
    fk1 = y.fkey2.fkey1
    
    assert(len(fk1) == 1)
    assert(fk1[0].id == y.id )