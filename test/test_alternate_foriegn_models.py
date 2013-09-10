from setup import *
from rom import *
from rom.exceptions import *

ctr = [0]
class Alternate(object):
    def __init__(self, id=None):
        if id is None:
            id = ctr[0]
            ctr[0] += 1
        self.id = id
    
    @classmethod
    def get(self, id):
        return Alternate(id)

class RomTestFModel(Model):
    attr = ForeignModel(Alternate)
    

def test_save():
    a = Alternate()
    ai = a.id
    i = RomTestFModel(attr=a).id
    session.commit()   # two lines of magic to destroy session history
    session.rollback() #
    del a
    
    f = RomTestFModel.get(i)
    assert(f.attr.id == ai)

