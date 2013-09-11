from setup import *
from rom import *

class RomTestGoo(Model):
    pass

def test_entity_caching_base():
    f = RomTestGoo()
    i = f.id
    p = id(f)
    session.commit()

    for j in xrange(10):
        RomTestGoo()

    g = RomTestGoo.get(i)
    assert(f is g)