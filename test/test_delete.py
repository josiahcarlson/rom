from setup import *
from rom import *

class RomTestDeletion(Model):
    col1 = String(index=True)

def test_deletion():
    x = RomTestDeletion(col1="this is a test string that should be indexed")
    session.commit()
    assert(len(RomTestDeletion.get_by(col1='this')) == 1)

    x.delete()
    assert(len(RomTestDeletion.get_by(col1='this')) == 0)

    session.commit()
    assert(len(RomTestDeletion.get_by(col1='this')) == 0)
