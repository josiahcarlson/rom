#!/usr/bin/env python

from distutils.core import setup

long_description = open('README.rst').read()
version = open('VERSION').read().strip()

setup(
    name='rom',
    version=version,
    description='A Redis object mapper for Python',
    author='Josiah Carlson',
    author_email='josiah.carlson@gmail.com',
    url='https://github.com/josiahcarlson/rom',
    packages=['rom', 'test'],
    classifiers=[
        'Development Status :: 4 - Beta',
        'License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
    ],
    license='GNU LGPL v2.1',
    long_description=long_description,
    requires=['redis'],
)

