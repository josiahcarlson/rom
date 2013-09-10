from setup import *
from rom import *
from datetime import datetime


class RomTestDateTime(Model):
    col1 = DateTime(index=True)
    col2 = Date(index=True)
    col3 = Time(index=True)


def test_datetimes():  
    now = datetime.utcnow()
    dtt = RomTestDateTime(col1=now, col2=now.date(), col3=now.time())
    dtt.save()
    session.commit()
    del dtt
    assert(len(RomTestDateTime.get_by(col1=now)) == 1)
    assert(len(RomTestDateTime.get_by(col2=now.date())) == 1)
    assert(len(RomTestDateTime.get_by(col3=now.time())) == 1)
