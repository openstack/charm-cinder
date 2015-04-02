#!/usr/bin/make
PYTHON := /usr/bin/env python

lint:
	@flake8 --exclude hooks/charmhelpers actions hooks unit_tests tests
	@charm proof

unit_test:
	@echo Starting unit tests...
	@$(PYTHON) /usr/bin/nosetests --nologcapture --with-coverage unit_tests

test:
	@echo Starting amulet deployment tests...
	#NOTE(beisner): can remove -v after bug 1320357 is fixed
	#   https://bugs.launchpad.net/amulet/+bug/1320357
	@juju test -v -p AMULET_HTTP_PROXY --timeout 900 \
        00-setup 14-basic-precise-icehouse 15-basic-trusty-icehouse \
        16-basic-trusty-icehouse-git 17-basic-trusty-juno \
        18-basic-trusty-juno-git 19-basic-trusty-kilo \
        20-basic-trusty-kilo-git 21-basic-vivid-kilo \
        22-basic-vivid-kilo-git

bin/charm_helpers_sync.py:
	@mkdir -p bin
	@bzr cat lp:charm-helpers/tools/charm_helpers_sync/charm_helpers_sync.py \
        > bin/charm_helpers_sync.py

sync: bin/charm_helpers_sync.py
	@$(PYTHON) bin/charm_helpers_sync.py -c charm-helpers-hooks.yaml
	@$(PYTHON) bin/charm_helpers_sync.py -c charm-helpers-tests.yaml

publish: lint unit_test
	bzr push lp:charms/cinder
	bzr push lp:charms/trusty/cinder
