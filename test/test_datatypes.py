import sys
import os
from pprint import pprint as p

# put our path in front so we can be sure we are testing locally not against the global package
sys.path.insert(1, os.path.dirname(os.path.dirname(__file__)))


from rom.datatypes import (Column, Integer, Float, Decimal, String, Text, Json, 
    PrimaryKey, Boolean, DateTime, Date, Time)

def test_column():
    assert(Column())

def test_integer():
    assert(Integer())
    
def test_integer():
    assert(Float())

def test_float():
    assert(Decimal())

def test_decimal():
    assert(Decimal())

def test_string():
    assert(String())

def test_text():
    assert(Text())

def test_json():
    assert(Json())

def test_pimarykey():
    assert(PrimaryKey())

def test_boolean():
    assert(Boolean())


def test_datetime():
    assert(DateTime())

def test_date():
    assert(Date())

def test_time():
    assert(Time())
