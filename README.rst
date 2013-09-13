
Rom - the Redis object mapper for Python

Copyright 2013 Josiah Carlson

Released under the LGPL license version 2.1 and version 3 (you can choose
which you'd like to be bound under).


What
====

Rom is a package whose purpose is to offer active-record style data modeling
within Redis from Python, similar to the semantics of Django ORM, SQLAlchemy +
Elixir, Google's Appengine datastore, and others.

Why
===

I was building a personal project, wanted to use Redis to store some of my
data, but didn't want to hack it poorly. I looked at the existing Redis object
mappers available in Python, but didn't like the features and functionality
offered.

What is available
=================

Data types:

* Strings, ints, floats, decimals, booleans
* datetime.datetime, datetime.date, datetime.time
* Json columns (for nested structures)
* OneToMany and ManyToOne columns (for model references)
* Non-rom ForeignModel reference support

Indexes:

* Numeric range fetches, searches, and ordering
* Full-word text search (find me entries with col X having words A and B)

Other features:

* Per-thread entity cache (to minimize round-trips, easy saving of all
  entities)

Getting started
===============

1. Make sure you have Python 2.6 or 2.7 installed
2. Make sure that you have Andy McCurdy's Redis library installed:
   https://github.com/andymccurdy/redis-py/ or
   https://pypi.python.org/pypi/redis
3. (optional) Make sure that you have the hiredis library installed for Python
4. Make sure that you have a Redis server installed and available remotely
5. Update the Redis connection settings for ``rom`` via
   ``rom.util.set_connection_settings()`` (other connection update options,
   including per-model connections, can be read about in the ``rom.util``
   documentation)::

    import redis
    from rom import util

    util.set_connection_settings(host='myhost', db=7)

.. warning:: If you forget to update the connection function, rom will attempt
 to connect to localhost:6379 .

6. Create a model::

    class User(Model):
        email_address = String(required=True, unique=True)
        salt = String()
        hash = String()
        created_at = Float(default=time.time)

7. Create an instance of the model and save it::

    PASSES = 32768
    def gen_hash(password, salt=None):
        salt = salt or os.urandom(16)
        comp = salt + password
        out = sha256(comp).digest()
        for i in xrange(PASSES-1):
            out = sha256(out + comp).digest()
        return salt, out

    user = User(email_address='user@host.com')
    user.salt, user.hash = gen_hash(password)
    user.save()
    # session.commit() or session.flush() works too

8. Load and use the object later::

    user = User.get_by(email_address='user@host.com')

Enabling Lua writing to support multiple unique columns
=======================================================

If you are interested in having multiple unique columns, you can enable a beta
feature that uses Lua to update all data written by rom. This eliminates any
race conditions that could lead to unique index retries, allowing writes to
succeed or fail much faster.

To enable this beta support, you only need to do::

    import rom
    rom._enable_lua_writes()

.. note:: You must be using Redis version 2.6 or later to be able to use this
 feature. If you are using a previous version without Lua support on the
 server side, this will not work.

