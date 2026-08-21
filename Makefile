PYTHON ?= python3
WORKSPACE ?= examples/atlas
CAMPAIGN ?= full-audit
RUN = PYTHONPATH=src $(PYTHON) -m wizengamot.cli

.PHONY: bootstrap validate test count plan privacy release verify

bootstrap:
	./scripts/bootstrap.sh

validate:
	$(RUN) --workspace $(WORKSPACE) validate

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

count:
	$(RUN) --workspace $(WORKSPACE) count

plan:
	$(RUN) --workspace $(WORKSPACE) plan --campaign $(CAMPAIGN)

privacy:
	$(PYTHON) scripts/privacy_check.py --all-public

release:
	$(PYTHON) scripts/build_public_release.py --output dist/wizengamot-public.zip

verify: validate test privacy plan
