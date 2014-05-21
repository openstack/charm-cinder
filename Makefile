#!/usr/bin/make
PYTHON := /usr/bin/env python

lint:
	@flake8 --exclude hooks/charmhelpers hooks unit_tests
	@charm proof

test:
	@echo Starting tests...
	@$(PYTHON) /usr/bin/nosetests --nologcapture unit_tests

sync:
	@charm-helper-sync -c charm-helpers.yaml

publish: lint test
	bzr push lp:charms/cinder
	bzr push lp:charms/trusty/cinder
