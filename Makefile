#!/usr/bin/make
PYTHON := /usr/bin/env python

lint:
	@flake8 --exclude hooks/charmhelpers hooks unit_tests
	@charm proof

unit_test:
	@$(PYTHON) /usr/bin/nosetests --nologcapture --with-coverage unit_tests

test:
	@echo Starting amulet deployment tests...
        #NOTE(beisner): can remove -v after bug 1320357 is fixed
	#   https://bugs.launchpad.net/amulet/+bug/1320357
	@juju test -v -p AMULET_HTTP_PROXY

bin/charm_helpers_sync.py:
	@mkdir -p bin
	@bzr cat lp:charm-helpers/tools/charm_helpers_sync/charm_helpers_sync.py \
        > bin/charm_helpers_sync.py

sync: bin/charm_helpers_sync.py
	@$(PYTHON) bin/charm_helpers_sync.py -c charm-helpers-hooks.yaml
	@charm-helper-sync -c charm-helpers-tests.yaml

publish: lint unit_test
	bzr push lp:charms/cinder
	bzr push lp:charms/trusty/cinder
