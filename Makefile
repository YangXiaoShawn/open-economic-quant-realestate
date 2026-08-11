# Mortgage Rate Lock-In, Housing Liquidity, and Local Market Dynamics
#
# All targets are thin wrappers around `lockin` CLI subcommands so that the
# pipeline is reproducible from either the Makefile or Python directly.
#
# CONFIG selects the run profile.  `configs/sample.yaml` is the default and runs
# entirely on labeled synthetic loan fixtures + official public aggregate data.

SHELL := /bin/bash
UV ?= $(shell command -v uv 2>/dev/null || echo "$$HOME/Library/Python/3.9/bin/uv")
CONFIG ?= configs/sample.yaml
RUN := $(UV) run --

.DEFAULT_GOAL := help

.PHONY: help setup prepare-sample-data ingest-mortgages build-loan-events build-lockin \
        build-local-panel validate-data estimate-hazards estimate-local-effects benchmark \
        simulate-policy test lint typecheck reproduce-sample report dashboard fetch-public-data clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

setup: ## Create the virtualenv and install the package with dev extras
	$(UV) venv --python 3.12
	$(UV) pip install -e ".[dev,dashboard]"

prepare-sample-data: ## Generate labeled SYNTHETIC loan fixtures (no restricted data)
	$(RUN) lockin prepare-sample-data --config $(CONFIG)

fetch-public-data: ## Download official public series (PMMS, FHFA HPI, HMDA, Census BPS)
	$(RUN) lockin fetch-public-data --config $(CONFIG)

ingest-mortgages: ## Parse origination + monthly performance files into partitioned Parquet
	$(RUN) lockin ingest-mortgages --config $(CONFIG)

build-loan-events: ## Build the documented loan-event table (exits, censoring, truncation)
	$(RUN) lockin build-loan-events --config $(CONFIG)

build-lockin: ## Attach point-in-time market rates and compute all lock-in measures
	$(RUN) lockin build-lockin --config $(CONFIG)

build-local-panel: ## Build geography-month active stock + local housing-market panel
	$(RUN) lockin build-local-panel --config $(CONFIG)

validate-data: ## Run schema, coverage, and internal-consistency validation checks
	$(RUN) lockin validate-data --config $(CONFIG)

check-registered-data: ## Preflight registered Freddie Mac archives (seconds, not hours)
	$(RUN) lockin check-registered-data --config $(CONFIG)

emit-layout: ## Regenerate data/reference/freddie_llds_layout.yaml from the schema module
	$(RUN) lockin emit-layout

estimate-hazards: ## KM/CIF description + discrete-time logit/cloglog + competing risks
	$(RUN) lockin estimate-hazards --config $(CONFIG)

estimate-local-effects: ## Continuous-treatment event study + DiD-style panel specifications
	$(RUN) lockin estimate-local-effects --config $(CONFIG)

benchmark: ## Compare estimates against published lock-in research benchmarks
	$(RUN) lockin benchmark --config $(CONFIG)

simulate-policy: ## Run model-dependent policy counterfactual scenarios
	$(RUN) lockin simulate-policy --config $(CONFIG)

report: ## Regenerate every report in reports/ from result artifacts
	$(RUN) lockin report --config $(CONFIG)

test: ## Run the unit test suite (offline; network tests are deselected)
	$(RUN) pytest -m "not network"

test-all: ## Run every test including live public-data network checks
	$(RUN) pytest

lint: ## ruff check + format check
	$(RUN) ruff check src tests
	$(RUN) ruff format --check src tests

typecheck: ## mypy over core interfaces
	$(RUN) mypy

reproduce-sample: prepare-sample-data ingest-mortgages build-loan-events build-lockin \
                  build-local-panel validate-data estimate-hazards estimate-local-effects \
                  robustness benchmark simulate-policy report ## Full end-to-end sample run
	@echo "Sample reproduction complete. See outputs/ and reports/."

dashboard: ## Launch the Streamlit dashboard
	$(RUN) streamlit run dashboard/app.py -- --config $(CONFIG)

clean: ## Remove generated outputs (keeps downloaded public-data cache)
	rm -rf outputs data/interim data/processed
