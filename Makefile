FILES=`ls docker-compose.*.yaml`
# A little nasty here, but we can do it!
# The grep finds the 'rom-test-<service version>' in the .yaml
# The sed removes extra spaces and colons
# Which we pass into our rebuild
GET_TARGET=grep -m1 test-$${target} docker-compose.yaml | sed 's/[ :]//g'
COMPOSE_PREFIX=docker-compose -f docker-compose

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

default: perms
	-find . -type f | xargs chmod -x

clean: default
	-rm -f *.pyc rom/*.pyc MANIFEST
	-rm -rf build _build

perms:
	-sudo chown ${USER}:${USER} -R .

compose-build-all:
	docker-compose build

compose-up-%:
	for target in $(patsubst compose-up-%,%,$@) ; do \
		${COMPOSE_PREFIX}.yaml up --remove-orphans `${GET_TARGET}`; \
	done

compose-down-%:
	for target in $(patsubst compose-down-%,%,$@) ; do \
		echo ${COMPOSE_PREFIX}.yaml down `${GET_TARGET}`; \
	done

testall:
	# they use the same Redis, so can't run in parallel
	make -j1 test-3.13 test-3.12 test-3.11 test-3.10 test-3.9 test-3.8 test-3.7 test-3.6 test-3.5 test-3.4 test-2.7

test-%:
	# the test container runs the tests on up, then does an exit 0 when done
	for target in $(patsubst test-%,%,$@) ; do \
		make compose-up-$${target}; \
	done

upload:
	git tag `cat VERSION`
	git push origin --tags --force
	docker-compose run --rm -w /source rom-uploader python3.13 -m build --sdist
	docker-compose run --rm -w /source rom-uploader python3.13 -m twine upload --skip-existing dist/rom-`cat VERSION`.tar.gz

test-tox:
	tox

docs:
	python3 -c "import rom; open('README.rst', 'wb').write(rom.__doc__.encode('latin-1')); open('VERSION', 'wb').write(rom.VERSION.encode('latin-1'));"
	docker-compose build rom-docs
	docker-compose run --rm -w /source rom-docs $(SPHINXBUILD) -b html $(ALLSPHINXOPTS) /source/_build/html
	make perms
	cp -r $(BUILDDIR)/html/. docs
	@echo
	@echo "Build finished. The HTML pages are in docs"
