FILES=`ls docker-compose.*.yaml`
# A little nasty here, but we can do it!
# The grep finds the 'rom-test-<service version>' in the .yaml
# The sed removes extra spaces and colons
# Which we pass into our rebuild
GET_TARGET=grep rom-test docker-compose.$${target}.yaml | sed 's/[ :]//g'
COMPOSE_PREFIX=docker-compose -f docker-compose.

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


compose-build-all:
	echo ${FILES}
	for target in ${FILES} ; do \
		docker-compose -f $${target} build -- `${GET_TARGET}` redis-data-storage; \
	done

compose-build-%:
	for target in $(patsubst compose-build-%,%,$@) ; do \
		echo ${COMPOSE_PREFIX}$${target}.yaml build `${GET_TARGET}`; \
		${COMPOSE_PREFIX}$${target}.yaml build `${GET_TARGET}`; \
	done


compose-up-%:
	for target in $(patsubst compose-up-%,%,$@) ; do \
		${COMPOSE_PREFIX}$${target}.yaml up --remove-orphans `${GET_TARGET}`; \
	done

compose-down-%:
	for target in $(patsubst compose-down-%,%,$@) ; do \
		echo ${COMPOSE_PREFIX}$${target}.yaml down `${GET_TARGET}`; \
	done

testall:
	# they use the same Redis, so can't run in parallel
	make -j1 test-3.11 test-3.10 test-3.9 test-3.8 test-3.7 test-3.6 test-3.5 test-3.4 test-2.7

test-%:
	# the test container runs the tests on up, then does an exit 0 when done
	for target in $(patsubst test-%,%,$@) ; do \
		make compose-build-$${target} && make compose-up-$${target}; \
	done


upload:
	git tag `cat VERSION`
	git push origin --tags
	python3.6 setup.py sdist
	python3.6 -m twine upload --verbose dist/rom-`cat VERSION`.tar.gz

test-tox:
	tox

docs:
	python3.6 -c "import rom; open('README.rst', 'wb').write(rom.__doc__.encode('latin-1')); open('VERSION', 'wb').write(rom.VERSION.encode('latin-1'));"
	docker-compose -f docker-compose.docs.yaml build
	docker-compose -f docker-compose.docs.yaml run rom-test-docs $(SPHINXBUILD) -b html $(ALLSPHINXOPTS) /app/_build/html
	cp -r $(BUILDDIR)/html/. docs
	@echo
	@echo "Build finished. The HTML pages are in docs"
