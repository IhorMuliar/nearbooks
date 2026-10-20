# NEAR Books. Everything below runs with python3 (stdlib only); the contract targets also need Rust.
SNAPSHOT ?= data/snapshot-2026-10-20
SNAPSHOT_M2 ?= data/snapshot-2026-10-20-m2
PY       ?= python3 -I

.PHONY: help data verify verify-live verify-deploy cache test test-py test-contract test-worker wasm wasm-local worker keeper-dry readme site clean

help:
	@echo "make verify        recompute + assert findings from the committed snapshot, check README numbers and local builds (offline, ~3 s)"
	@echo "make verify-deploy compare contract/deployment.json with the testnet contract, rebuild it reproducibly (docker) and check local builds (network)"
	@echo "make cache         re-read the testnet contract into site/contract-cache.json (network; then make readme)"
	@echo "make data          fetch fresh raw data into data/live + data/live-m2 (network, ~6 min + ~25 min NEARData block sample)"
	@echo "make verify-live   make data, then recompute + assert on the fresh data"
	@echo "make test          python tests (pipeline, MCP) + contract and worker unit tests"
	@echo "make wasm          reproducible build (docker, cargo-near) of the deployed commit into contract/res/nearbooks.wasm"
	@echo "make wasm-local    quick non-reproducible build of the working tree into contract/res/nearbooks-local.wasm"
	@echo "make keeper-dry    plan the keeper's next run against the testnet contract (network, no key, signs nothing)"
	@echo "make worker        build the OutLayer worker (wasm32-wasip2) and print the sha256 the contract pins"
	@echo "make site          serve the dashboard at http://127.0.0.1:8000/site/ (reads the testnet contract live)"

# --- offline path: committed snapshot -> metrics.json -> assertions
verify:
	$(PY) pipeline/compute.py --data $(SNAPSHOT) --m2 $(SNAPSHOT_M2) --out $(SNAPSHOT)/metrics.json
	cp $(SNAPSHOT)/metrics.json site/metrics.json
	$(PY) pipeline/verify.py $(SNAPSHOT)/metrics.json
	$(PY) pipeline/verify.py $(SNAPSHOT)/metrics.json --check-readme README.md

# --- online path: pull everything, recompute, assert
data:
	$(PY) pipeline/fetch.py --out data/live
	$(PY) pipeline/fetch_m2.py --main data/live --out data/live-m2
	$(PY) pipeline/compute.py --data data/live --m2 data/live-m2 --out data/live/metrics.json

verify-deploy:
	$(PY) contract/scripts/check_deployment.py

cache:
	$(PY) mcp/chain.py dump site/contract-cache.json

verify-live: data
	$(PY) pipeline/verify.py data/live/metrics.json

readme:
	$(PY) pipeline/update_readme.py $(SNAPSHOT)/metrics.json README.md

test: test-py test-contract test-worker

test-py:
	$(PY) -m unittest discover -s tests -t . -v

test-contract:
	cd contract && cargo test

test-worker:
	cd worker && cargo test --locked

wasm:
	contract/scripts/repro_build.sh

wasm-local:
	cd contract && ./build.sh

keeper-dry:
	$(PY) keeper/keeper.py --dry-run

worker:
	cd worker && ./build.sh

site:
	python3 -m http.server 8000 --bind 127.0.0.1

clean:
	rm -rf data/live data/live-m2 contract/target worker/target
