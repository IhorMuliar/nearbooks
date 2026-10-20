# NEAR Books. Everything below runs with python3 (stdlib only); the contract targets also need Rust.
SNAPSHOT ?= data/snapshot-2026-10-20
SNAPSHOT_M2 ?= data/snapshot-2026-10-20-m2
PY       ?= python3 -I

.PHONY: help data verify verify-live test test-py test-contract test-worker wasm worker readme site clean

help:
	@echo "make verify        recompute + assert findings from the committed snapshot (offline, ~2 s)"
	@echo "make data          fetch fresh raw data into data/live + data/live-m2 (network, ~6 min + ~25 min NEARData block sample)"
	@echo "make verify-live   make data, then recompute + assert on the fresh data"
	@echo "make test          python tests (pipeline, MCP) + contract and worker unit tests"
	@echo "make wasm          build contract/ to wasm"
	@echo "make worker        build the OutLayer worker (wasm32-wasip2) and print the sha256 the contract pins"
	@echo "make site          serve the dashboard at http://127.0.0.1:8000/site/"

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
	cd contract && ./build.sh

worker:
	cd worker && ./build.sh

site:
	python3 -m http.server 8000 --bind 127.0.0.1

clean:
	rm -rf data/live data/live-m2 contract/target worker/target
