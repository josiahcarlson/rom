from setup import *
from rom import *

class RomTestJson(Model):
    col = Json()

def test_json_multisave():
    d = {'hello': 'world'}
    x = RomTestJson(col=d)
    x.save()
    del x
    for i in xrange(5):
        x = RomTestJson.get(1)
        assert(x.col == d)
        x.save(full=True)
        session.rollback()
