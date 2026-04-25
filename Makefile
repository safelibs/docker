PYTHON ?= python3
WORKSPACE ?= .work
DIST ?= dist
IMAGE_NAMESPACE ?= safelibs
BASE_IMAGE ?= ubuntu:24.04

.PHONY: test check clean

test:
	$(PYTHON) -m unittest discover -s tests -v

check:
	$(MAKE) test

clean:
	rm -rf $(WORKSPACE) $(DIST)
