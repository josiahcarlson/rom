SHELL=/bin/bash

# You can set these variables from the command line.
SPHINXOPTS    =
SPHINXBUILD   = sphinx-build
PAPER         =
BUILDDIR      = _build

# Internal variables.
PAPEROPT_a4     = -D latex_paper_size=a4
PAPEROPT_letter = -D latex_paper_size=letter
ALLSPHINXOPTS   = -d $(BUILDDIR)/doctrees $(PAPEROPT_$(PAPER)) $(SPHINXOPTS) source
# the i18n builder cannot share the environment and doctrees with the others
I18NSPHINXOPTS  = $(PAPEROPT_$(PAPER)) $(SPHINXOPTS) source

.PHONY: clean docs test

default:
	find . -type f | xargs chmod -x

clean:
	-rm -f *.pyc rom/*.pyc MANIFEST
	-rm -rf dist build

install:
	python setup.py install

upload:
	git tag `cat VERSION`
	git push origin --tags
	python3.6 setup.py sdist upload

test:
	PYTHONPATH=`pwd` python2.6 test/test_rom.py
	PYTHONPATH=`pwd` python2.7 test/test_rom.py
	PYTHONPATH=`pwd` python3.3 test/test_rom.py
	PYTHONPATH=`pwd` python3.4 test/test_rom.py
	PYTHONPATH=`pwd` python3.5 test/test_rom.py
	PYTHONPATH=`pwd` python3.6 test/test_rom.py

install-test-requirements:
	sudo apt-get install python2.6 python2.6-dev python2.7 python2.7-dev python3.3 python3.3-dev python3.4 python3.4-dev python3.5 python3.5-dev
	# may require other steps to get pip installed
	sudo python2.6 -m pip.__init__ install setuptools
	sudo python2.6 -m pip.__init__ install redis hiredis six
	sudo python2.7 -m pip install setuptools
	sudo python2.7 -m pip install redis hiredis six
	sudo python3.3 -m pip install setuptools
	sudo python3.3 -m pip install redis hiredis six
	sudo python3.4 -m pip install setuptools
	sudo python3.4 -m pip install redis hiredis six
	sudo python3.5 -m pip install setuptools
	sudo python3.5 -m pip install redis hiredis six

test-tox:
	tox

docs:
	python -c "import rom; open('README.rst', 'wb').write(rom.__doc__); open('VERSION', 'wb').write(rom.VERSION);"
	$(SPHINXBUILD) -b html $(ALLSPHINXOPTS) $(BUILDDIR)/html
	cp -r $(BUILDDIR)/html/. docs
	@echo
	@echo "Build finished. The HTML pages are in docs"
