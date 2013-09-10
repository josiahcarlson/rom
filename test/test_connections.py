import redis
from setup import *
from rom import *


class RomTestFoo(Model):
    pass

class RomTestBar(Model):
    _conn = redis.Redis(db=14)


def test_w_class_specific_db():
    RomTestFoo().save()
    RomTestBar().save()
    assert(RomTestBar._conn.get('RomTestBar:id:') == '1')
    assert(util.CONNECTION.get('RomTestBar:id:') == None)
