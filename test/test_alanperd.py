from rom import *
from rom import util
import rom 
util.set_connection_settings(host='10.40.0.190', db=1)

class Person(rom.Model):
    name=String(prefix=True, suffix=True,index=True)
    sex=String(prefix=True, suffix=True,index=True)
    edad=Integer(prefix=True,suffix=True,index=True)
    idPerson=String(prefix=True, suffix=True,index=True)
    description=String(prefix=True, suffix=True,index=True)

import unittest
class TestFilter(unittest.TestCase):
    
    def populate(self):
        # WARNING , it cleans the db
        import redis
        r = redis.StrictRedis(host='10.40.0.190',db=1)
        r.flushdb()
        
        objPerson=Person()
        objPerson.name='Julia'
        objPerson.sex='female'
        objPerson.edad=20
        objPerson.ididPerson="8947589545872"
        objPerson.description="ayuntamientodeciudad"
        objPerson.save()
        
        objPerson=Person()
        objPerson.name='Maria'
        objPerson.sex='female'
        objPerson.edad=20
        objPerson.idPerson="8947589545872"
        objPerson.description="ayuntamientodeguipuzcoa"
        objPerson.save()
            
        objPerson=Person()
        objPerson.name='Julio'
        objPerson.sex='male'
        objPerson.edad=20
        objPerson.idPerson="8947589545872"
        objPerson.description="ayuntamientodepalencia"
        objPerson.save()
        
        objPerson=Person()
        objPerson.name='Alan'
        objPerson.sex='male'
        objPerson.edad=20
        objPerson.idPerson="8947589545872"
        objPerson.description="ayuntamientodeciudad"
        objPerson.save()
            
        objPerson=Person()
        objPerson.name='Fernando'
        objPerson.sex='male'
        objPerson.edad=21
        objPerson.idPerson="8937589569872"
        objPerson.description="ayuntamientodeburgos"
        objPerson.save()
            
        objPerson=Person()
        objPerson.name='Julia'
        objPerson.sex='female'
        objPerson.edad=20
        objPerson.idPerson="8947689545872"
        objPerson.description="ayuntamientodeburgos"
        objPerson.save()
        
        objPerson=Person()
        objPerson.name='Juliana'
        objPerson.sex='female'
        objPerson.edad=20
        objPerson.idPerson="8947689545872"
        objPerson.description="ayuntamientodeburgos"
        objPerson.save()
        
        objPerson=Person()
        objPerson.name='Emilia'
        objPerson.sex='female'
        objPerson.edad=20
        objPerson.idPerson="894789545872"
        objPerson.description="ayuntamientodeciudad"
        objPerson.save()

    def test_nest(self):
        self.populate()
        for c in Person.query.startswith(idPerson='89475').filter(description="ayuntamientodeburgos").all():
            print c.name + " " + c.idPerson
        
        self.assertEqual(Person.query.startswith(idPerson='89475').filter(description="ayuntamientodeburgos").count(),0)

    def test_pattern(self):
        self.populate()
        for c in Person.query.like(idPerson='*94*').filter(description="ayuntamientodeburgos").all():
            print c.name + " " + c.idPerson
        self.assertEqual(Person.query.like(idPerson='*94*').filter(description="ayuntamientodeburgos").count(),2)
        

if __name__ == '__main__':
    unittest.main()
    #import time 
    #while True:
    #    print "This prints once a minute."
    #    time.sleep(60)
    
    