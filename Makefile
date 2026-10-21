# NEAR Books. Everything below runs with python3 (stdlib only); the contract targets also need Rust.
SNAPSHOT ?= data/snapshot-2026-10-20
SNAPSHOT_M2 ?= data/snapshot-2026-10-20-m2
PY       ?= python3 -I

.PHONY: help data verify verify-live verify-deploy verify-deploy-testnet cache cache-testnet test test-py test-contract test-worker wasm wasm-local worker keeper-dry readme site clean

help:
	@echo "make verify                 recompute and check every finding and claim offline (~3 s)"
	@echo "make test                   Python, contract and worker tests"
	@echo "make verify-deploy          check the mainnet contract against its record and a reproducible build (network, docker)"
	@echo "make verify-deploy-testnet  the same for the earlier testnet deployment"
	@echo "make data / verify-live     fetch fresh raw data (network, ~30 min) / then recompute and check it"
	@echo "make cache / cache-testnet  re-read the contract into site/contract-cache*.json, then run make readme"
	@echo "make readme                 regenerate the generated blocks in README.md and docs/"
	@echo "make wasm / wasm-local      reproducible contract build (docker) / quick local build"
	@echo "make worker                 build the TEE worker and print the sha256 the contract pins"
	@echo "make keeper-dry             plan the keeper's next run (network, signs nothing)"
	@echo "make site                   serve the dashboard at http://127.0.0.1:8000/site/"

# --- offline path: committed snapshot -> metrics.json -> assertions
verify:
	$(PY) pipeline/compute.py --data $(SNAPSHOT) --m2 $(SNAPSHOT_M2) --out $(SNAPSHOT)/metrics.json
	cp $(SNAPSHOT)/metrics.json site/metrics.json
	$(PY) pipeline/verify.py $(SNAPSHOT)/metrics.json --claims-json site/claims.json
	$(PY) pipeline/verify.py $(SNAPSHOT)/metrics.json
	$(PY) pipeline/verify.py $(SNAPSHOT)/metrics.json --check-readme README.md

# --- online path: pull everything, recompute, assert
data:
	$(PY) pipeline/fetch.py --out data/live
	$(PY) pipeline/fetch_m2.py --main data/live --out data/live-m2
	$(PY) pipeline/compute.py --data data/live --m2 data/live-m2 --out data/live/metrics.json

verify-deploy:
	$(PY) contract/scripts/check_deployment.py

verify-deploy-testnet:
	NEARBOOKS_SKIP_REPRO=$${NEARBOOKS_SKIP_REPRO:-1} $(PY) contract/scripts/check_deployment.py --network testnet

cache:
	$(PY) mcp/chain.py dump site/contract-cache.json

cache-testnet:
	$(PY) mcp/chain.py --network testnet dump site/contract-cache-testnet.json

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
