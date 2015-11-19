#!/usr/bin/env python

try:
    from setuptools import setup
except:
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
    packages=['rom'],
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)',
        'License :: OSI Approved :: GNU Lesser General Public License v2 (LGPLv2)',
        'License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3.3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
    ],
    license='GNU LGPL v2.1',
    long_description=long_description,
    requires=['redis', 'six'],
    install_requires=['redis', 'six'],
)

