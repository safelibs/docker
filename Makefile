PYTHON ?= python3
WORKSPACE ?= .work
DIST ?= dist
IMAGE_NAMESPACE ?= safelibs
BASE_IMAGE ?= ubuntu:24.04
VALIDATOR_SITE_URL ?= https://safelibs.github.io/validator/site-data.json
PORT_MODE ?= port-04-test
SELECTION_MANIFEST ?= $(DIST)/validator-selection.json
PORT_DEB_ROOT ?= $(WORKSPACE)/debs/$(PORT_MODE)
PORT_DEB_LOCK ?= $(DIST)/port-debs-lock.json
LIBRARIES ?=
VERIFY_ROOT ?= $(WORKSPACE)/verify

.PHONY: test check clean resolve-validator fetch-port-debs

test:
	$(PYTHON) -m unittest discover -s tests -v

check:
	$(MAKE) test

resolve-validator:
	$(PYTHON) -m tools.validator_selection --site-url "$(VALIDATOR_SITE_URL)" --mode "$(PORT_MODE)" --output "$(SELECTION_MANIFEST)" $(foreach library,$(LIBRARIES),--library "$(library)")

fetch-port-debs:
	$(PYTHON) -m tools.fetch_port_debs --selection-manifest "$(SELECTION_MANIFEST)" --output-root "$(PORT_DEB_ROOT)" --output "$(PORT_DEB_LOCK)"

clean:
	rm -rf $(WORKSPACE) $(DIST)
