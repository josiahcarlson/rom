from setup import *
from rom import *

class RomTestBoolean(Model):
    col = Boolean(index=True)

def initialize():
    RomTestBoolean(col=True).save()
    RomTestBoolean(col=1).save()
    RomTestBoolean(col=False).save()
    RomTestBoolean(col='').save()
    RomTestBoolean(col=None).save() # None is considered "not data", so is ignored
    y = RomTestBoolean()
    y.save()
    return y

def test_boolean_get_by():
    initialize()
    assert(len(RomTestBoolean.get_by(col=True))== 2)
    assert(len(RomTestBoolean.get_by(col=False))== 2)
    session.rollback()
    
    
def test_boolean_change():
    y = initialize()
    yid = y.id
    x = RomTestBoolean.get(1)
    x.col = False
    x.save()
    assert(len(RomTestBoolean.get_by(col=True))== 1)
    assert(len(RomTestBoolean.get_by(col=False))== 3)
    assert(len(RomTestBoolean.get_by(col=True))== 1)
    assert(len(RomTestBoolean.get_by(col=False))== 3)
    y = RomTestBoolean.get(yid)
    assert(y.col == None)