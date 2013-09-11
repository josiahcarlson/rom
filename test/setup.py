import redis
import os
import sys

# put our path in front so we can be sure we are testing locally not against the global package
sys.path.insert(1, os.path.dirname(os.path.dirname(__file__)))

from rom import util
from rom import *


def setup():
    util.CONNECTION = redis.Redis(db=15)
    clean()
    
def clean():
    '''remove all keys with the RomTest prefix'''
    for i in [14,15]:
        db = redis.Redis(db=i)
        keys = db.keys('RomTest*')
        if keys :
           db.delete(*keys)

def teardown():
    clean()
