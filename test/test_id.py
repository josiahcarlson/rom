from setup import *
from rom import *
import time
from pytest import mark

class RomTestID(Model):
    pass
 
@mark.xfail
def test_save_supplying_id():
    id = time.time()
    x = RomTestID(id = id)
    assert( x.save() )
    assert( x.id == id )