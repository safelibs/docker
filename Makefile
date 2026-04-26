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
IMAGE_BUILD_PLAN ?= $(DIST)/image-build-plan.json
CONTEXT_ROOT ?= $(WORKSPACE)/contexts
DOCKER ?= docker
LIBRARIES ?=
VERIFY_ROOT ?= $(WORKSPACE)/verify

.PHONY: test check clean resolve-validator fetch-port-debs build-images verify-images

test:
	$(PYTHON) -m unittest discover -s tests -v

check:
	$(MAKE) test

resolve-validator:
	$(PYTHON) -m tools.validator_selection --site-url "$(VALIDATOR_SITE_URL)" --mode "$(PORT_MODE)" --output "$(SELECTION_MANIFEST)" $(foreach library,$(LIBRARIES),--library "$(library)")

fetch-port-debs:
	$(PYTHON) -m tools.fetch_port_debs --selection-manifest "$(SELECTION_MANIFEST)" --output-root "$(PORT_DEB_ROOT)" --output "$(PORT_DEB_LOCK)"

build-images:
	$(PYTHON) -m tools.build_images --lock-manifest "$(PORT_DEB_LOCK)" --output "$(IMAGE_BUILD_PLAN)" --context-root "$(CONTEXT_ROOT)" --image-namespace "$(IMAGE_NAMESPACE)" --base-image "$(BASE_IMAGE)" --docker "$(DOCKER)" $(foreach library,$(LIBRARIES),--library "$(library)")

verify-images:
	$(PYTHON) -m tools.verify_images --image-build-plan "$(IMAGE_BUILD_PLAN)" --docker "$(DOCKER)" $(foreach library,$(LIBRARIES),--library "$(library)")

clean:
	rm -rf $(WORKSPACE) $(DIST)
