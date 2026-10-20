# NEAR Books

**NEAR Books rebuilds NEAR Intents' revenue, buyback and issuance numbers from raw chain data, reconciles them against DefiLlama, and publishes the result as a static dashboard, a read-only MCP server and a NEAR smart contract that stores dated metric snapshots and falsifiable predictions. The snapshots that decide predictions are attested: a pinned WASM worker reads NEAR mainnet inside OutLayer's Intel TDX enclave and the contract receives its output on-chain, so no reporter has to be trusted.** Every claim in the findings table is an assertion that `make verify` re-runs offline in about two seconds from a committed data snapshot, with tolerances derived from the data rather than typed in.

- **Track:** Thesis (an evidence-backed research entry; the contract and MCP server make it checkable by people and by agents).
- **Status:** prototype built for a feasibility measurement, snapshot as of 2026-10-20. Testnet only, no mainnet deployment, no funds involved.

## NEAR technology used

| What | How it is used here | Where |
|---|---|---|
| NEAR archival JSON-RPC (`archival-rpc.mainnet.fastnear.com`) | `block` headers for `total_supply` at 43 heights, `view_account` for wallet balances and `treasury.near` at past heights | `pipeline/fetch.py` |
| FastNear transfers API | Every native transfer into the three revenue wallets since 2026-01-01 (5,027 rows) | `pipeline/fetch.py` |
| `near-sdk-rs` 5.29.1 smart contract | `nearbooks` oracle: attested snapshots (permissionless `request_snapshot`), reporter snapshots, owner-registered predictions, permissionless resolution | `contract/` |
| OutLayer (Intel TDX) on-chain execution | `request_snapshot(date, block)` calls `outlayer.testnet` `request_execution` with the owner-pinned worker hash; the result comes back through NEAR yield/resume to `on_snapshot_result` | `contract/src/lib.rs` |
| OutLayer WASM worker (Rust, `wasm32-wasip2`, `wasi-http-client`) | Reads mainnet over HTTPS JSON-RPC (archival FastNear, fallback `rpc.mainnet.near.org`) at the first block of a UTC day: `block` header `total_supply`, `view_account` of the three revenue wallets, `intents.near mt_supply` vs `ft_balance_of` for the top 8 Intents tokens | `worker/` |
| FastFS | Hosts the worker WASM that OutLayer downloads and checks against the pinned sha256 | `outlayer upload` |
| OutLayer attestations + Intel DCAP | Public per-run record (TDX quote, wasm/input/output hashes); the verifier binds the quote to the run and checks it against OutLayer's on-chain approved measurements | `contract/scripts/verify_attestation.py`, `docs/attestation.md` |
| Function-call access keys | The reporter account holds a key that can call only `submit_snapshot` on the contract; the first testnet run below signs with exactly that key | `contract/scripts/testnet_demo.sh` |
| `cargo-near` 0.22 | Builds the wasm (`cargo near build non-reproducible-wasm`) | `contract/build.sh` |
| NEAR testnet + faucet-sponsored accounts | Deployments and end-to-end runs with tx hashes (below) | `contract/testnet-run.log`, `contract/testnet-attested.log` |
| NEAR Intents | The audited subject. The pipeline reads its revenue wallets and DefiLlama's Intents series; the worker checks `intents.near` reserves on-chain. The 1Click token list is used once, offline, to rank tokens by value (`worker/scripts/rank_intents_tokens.py`). | `pipeline/`, `worker/` |

## Quickstart (every command below was run; outputs are pasted from those runs)

Requirements: `python3` >= 3.10 (standard library only), `make`. For the contract and worker: Rust stable (1.98.0 used) with the `wasm32-unknown-unknown` and `wasm32-wasip2` targets, optionally `cargo-near`. No API keys, no accounts, no environment variables.

### 1. Verify all findings offline

```
make verify          # from the repository root
```

Output (abridged to the verdict lines; the full table with observed values and tolerances is in "Findings" below):

```
python3 -I pipeline/compute.py --data data/snapshot-2026-10-20 --out data/snapshot-2026-10-20/metrics.json
wrote data/snapshot-2026-10-20/metrics.json: as_of 2026-10-20, revenue 2026-01-01..2026-10-19 = $7,518,638 (DefiLlama $7,561,260), buyback wallet 2,024,302 NEAR
cp data/snapshot-2026-10-20/metrics.json site/metrics.json
python3 -I pipeline/verify.py data/snapshot-2026-10-20/metrics.json
python3 -I pipeline/compute.py --data data/snapshot-2026-10-20 --out data/snapshot-2026-10-20/metrics.json
python3 -I pipeline/verify.py data/snapshot-2026-10-20/metrics.json
PASS F1a  Captured revenue rebuilt from raw wallet inflows matches DefiLlama year-to-date
PASS F1b  Day-level series line up: correlation peaks at lag 0 (no timestamp shift)
PASS F2a  Buyback wallet balance is fully explained by transfers in minus transfers out (nothing burned from it)
PASS F2b  Total supply rose in every sampled interval since 1 Jan (no net burn visible)
PASS F2c  DefiLlama labels all captured revenue as buyback, but only part of it ever reached the buyback wallet
PASS F2d  fefundsadmin has spent almost nothing of what it holds
PASS F3a  HSP-029's treasury share of supply growth (10.13%) reproduces from RPC
PASS F3b  HSP-029's realised issuance (2.45%/yr) reproduces
PASS F4a  NEAR is not net-deflationary at today's volume: break-even volume exceeds current volume even under the least favourable price and take rate seen in the last 30 days
PASS F4b  At the proposed 1.6% issuance, break-even volume falls but stays above current volume
PASS F4c  Spot price used (DefiLlama coins) agrees with the USD valuation FastNear attached to recent transfers
PASS F4d  Our daily sums reproduce DefiLlama's own 30-day revenue field (parsing check)
as_of 2026-10-20: 12/12 findings hold -> ALL PASS
python3 -I pipeline/verify.py data/snapshot-2026-10-20/metrics.json --check-readme README.md
README findings block matches the verified output
```

You should see `12/12 findings hold -> ALL PASS`. The last line confirms the README table below equals what the code just computed.

### 2. Run the tests

```
make test             # all of the below
make test-py          # pipeline + MCP server (16 tests)
make test-contract    # contract unit tests (67 tests)
make test-worker      # OutLayer worker unit tests with a fake RPC transport (12 tests)
```

```
Ran 16 tests in 0.610s
OK
test result: ok. 67 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 21.66s
test result: ok. 12 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s
```

Contract tests cover the attested path:
- the callback refuses a wrong caller, a re-pinned wasm hash, a changed OutLayer contract, and malformed, mismatched or oversized output,
- a failed OutLayer call returns only the request's own storage reserve,
- the requester pays storage (the contract's free balance is unchanged by a snapshot),
- one attested snapshot per date, backfill in any order, bounded by an `earliest_date` that only moves earlier,
- reporter snapshots stay tagged `Reported` and never resolve predictions.

`test_findings_are_not_vacuous` corrupts the metrics in seven ways and checks that the matching finding turns into FAIL. `test_snapshot_tampering_is_detected` edits a raw file and checks that `compute.py` refuses it (every raw file is hashed in `manifest.json`).

### 3. See the dashboard

```
make site             # then open http://127.0.0.1:8000/site/
```

The page renders `site/metrics.json` (a copy that `make verify` refreshes): headline cards, the reconciliation table, a cumulative-revenue chart, a deflation calculator with sliders for price, volume, take (bps) and issuance, a burn-versus-hold toggle, and the prediction list. Screenshot of the committed page: `site/screenshot.png`. A scripted headless-Chromium run clicked the presets, the burn/hold toggle and the volume slider and read back the verdicts (for example volume at $2,000M/day flips the verdict to "Total supply shrinks").

### 4. Ask the MCP server

```
python3 mcp/server.py data/snapshot-2026-10-20/metrics.json     # stdio, newline-delimited JSON-RPC
```

Tools: `get_metrics`, `get_reconciliation`, `list_predictions`. A real exchange:

```
$ printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"demo","version":"0"}}}' \
    '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
    '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_reconciliation","arguments":{}}}' \
    | python3 mcp/server.py data/snapshot-2026-10-20/metrics.json
{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "nearbooks", "version": "0.1.0"}}}
{"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "{\n \"rows\": [\n  {\n   \"item\": \"Captured revenue 2026-01-01..2026-10-19\",\n   \"onchain\": 7518637.612172455,\n   \"defillama\": 7561260.05, ...
```

For an MCP client (for example Claude Code): `claude mcp add nearbooks -- python3 /path/to/nearbooks/mcp/server.py`.

### 5. Build the contract

```
cargo install cargo-near --locked --no-default-features   # default features need libudev (Ledger support)
make wasm
```

```
✓ Contract successfully built! (in CARGO_NEAR_BUILD_ENVIRONMENT=host)
     -                Binary: contract/res/nearbooks.wasm
     -                  Size: 145.3 KB (145302 bytes)
     - SHA-256 checksum hex : 89c1f889e2f40829e21124f2f5218dbf86ce1e02dce5fe14aca0492e8bf59ac9
```

Build the worker (the sha256 is the hash the contract pins; the same bytes come out when building from another directory):

```
make worker
```
```
rustc 1.98.0 (88d9e12ae 2026-08-18)
20102fb3000a8c78ac93389d8f5be759e18b2e34bce5a9f1686854d15abd1e80  res/nearbooks-worker.wasm
```

Without `cargo-near`, `contract/build.sh` falls back to `cargo build --target wasm32-unknown-unknown --release`. That works only because `contract/.cargo/config.toml` sets `--cfg near`; a plain build without it compiled and deployed fine but every panic surfaced as a bare `WasmTrap(Unreachable)` with no message (found and fixed during this run).

### 6. Re-pull everything from the network (optional, about 6 minutes)

```
make verify-live      # fetch -> data/live, compute, assert on the fresh data
```

The duration is the FastNear transfers API rate limit (HTTP 429 with backoff). Results move a little with time (new blocks, new price); the findings are written to survive that, see "Limits".

## Attested snapshots on testnet (real transactions, no funds)

`contract/scripts/testnet_attested.sh` deployed the contract to `nearbooks-v4.okf-kill-1792497882.testnet`, pinned the worker and requested two days. Each request ran the worker in OutLayer's TDX enclave against NEAR mainnet. Full log: `contract/testnet-attested.log`. How a third party checks it, and which checks were executed: `docs/attestation.md`.

```
$ python3 -I worker/scripts/first_block.py 2026-10-20
220726930
$ near contract call-function as-transaction nearbooks-v4.okf-kill-1792497882.testnet request_snapshot \
    json-args '{"date":"2026-10-20","block_height":220726930}' prepaid-gas '300 Tgas' attached-deposit '0.03 NEAR' ...
Execution completed successfully. Output: Json(383 bytes), Resources: { instructions: 10469135, time_ms: 3107 }, Cost: 1310701000000000000000 yoctoNEAR, Refund: 8689299000000000000000 yoctoNEAR
EVENT_JSON:{"standard":"nearbooks","version":"1.1.0","event":"storage_settled","data":{"nonce":1,"bytes":844,"kept":"8440000000000000000000","refunded":"11560000000000000000000"}}
"stored attested snapshot 2026-10-20"
$ python3 -I contract/scripts/verify_attestation.py nearbooks-v4.okf-kill-1792497882.testnet 2026-10-20 --wasm worker/res/nearbooks-worker.wasm --dcap dcap-qvl
...
PASS  attestation.input_hash == sha256(contract input)  {"n":"1","d":"2026-10-20","b":"220726930"}
PASS  attestation.output_hash == snapshot output_sha256  4e4e928a3455f8e862fb64ed915b7ad0b98c7fd7f0afea8aed3ca02636002b76
PASS  quote report_data == sha256(task fields): the quote is bound to this run  573ccec0...
PASS  quote MRTD+RTMR0-3 are in worker.outlayer.testnet approved measurements  (108 approved sets)
PASS  Intel DCAP signature chain verifies (dcap-qvl)  TCB status UpToDate
12/12 checks pass
```

| Step | Result | Transaction |
|---|---|---|
| Deploy + `new`, then `pin_worker` (event `worker_pinned`) | ok | `4E9iJrxbhqtci5jupCVZg4kdPxi2sEg5xNFmjZGMe98Q`, `2RGJqpXMAHe8y2gQPNmdvYHTmvMKJnuXaK18eQW7ZUhQ` |
| `request_snapshot("2026-10-20", 220726930)` | stored, OutLayer request 5137. Supply 1,309,428,647 NEAR, buyback wallet 2,022,180 NEAR, 8/8 Intents tokens fully backed. 844 bytes of storage paid from the requester's reserve; the contract's free balance was unchanged. | `CmYRyYdp6ZNeoCLe3ecJDs4VD2aDGZvRr7N6r7edSjGe` |
| `set_earliest_date("2026-10-19")`, then `request_snapshot("2026-10-19", 220581913)` (backfill, archival read) | stored, request 5138. The request was refused before the move, and moving the date forward again was refused. | `DzBN63Ce5oDG9y5Kvb4PfYk6irjCnFfSrw2tLkdoQQoZ`, `4iXCYdwZ8TfVeC6Buw1bYodcj2M972f273b1gvgodGsd` |
| Block 220726931 for 2026-10-20 (not the day's first block) | the worker refused, the contract returned `rejected: ...`, nothing stored, the full reserve refunded | `Kwnry9cjDk76NwMqQ83q2kGS4oBC5gHYvKakCZAnQtW` |
| Refused before any run: 0.01 NEAR (no storage reserve), a date not yet started, a second request for 2026-10-20, a stranger re-pinning, a direct `on_snapshot_result` call | rejected | not included |

A run costs about 0.0024 NEAR (0.0013 OutLayer plus gas), plus 0.0084 NEAR of storage per stored snapshot. Of the 0.03 NEAR deposit, OutLayer refunds the unused run part and the contract refunds the unused storage reserve.

## Reporter testnet run (real transactions, faucet-sponsored accounts, no funds)

`contract/scripts/testnet_demo.sh` created two accounts with `near account create-account sponsor-by-faucet-service`, deployed the cargo-near build, gave the reporter a function-call key limited to `submit_snapshot`, and exercised the contract. Full log: `contract/testnet-run.log` (console output of that run, saved from the terminal because a later failed re-run overwrote the file). That run used the first contract build, whose prediction resolution had the scan-window flaw described under "Limits".

| Step | Result | Transaction |
|---|---|---|
| Create owner/contract account `nbproto-owner-1696.testnet` | ok | `k35Y4jPAE8oVHGFnjJV38bxLjykJhD1rLN9vMfoZGhe` |
| Create reporter account `nbproto-rep-1696.testnet` | ok | `3dUxy6oZoDUHyYhmHsmws5hPkpm8CZKjw6j7u6VQf9Ts` |
| Deploy + `new(owner, reporter)` | ok | `C3vKYWYEtFPXUgJhX7guN5n1gMFwnsSPr1WG3uDrwUDg` |
| Add reporter function-call key (`submit_snapshot` only) | ok | `DEaDWGBca3YrqZWMupa2joJsEn1bBY4t8BoLRHw5eMWx` |
| Owner calls `submit_snapshot` | rejected: `only the reporter can submit snapshots` | not included |
| Reporter submits the 2026-10-20 snapshot with the function-call key | ok | `Fnvem2YpsFJrf6d3vepf16nP38dVa1dQq58jd6aC5bHa` |
| Reporter submits the same date again | rejected: `snapshot date must be later than the latest stored date` | not included |
| Owner registers predictions P1..P4 | ok | `YP5LTqcrG84RReEEB51W4LFSpW6CvrY9Hu8yY5NAfas`, `7z7zpnzSTyRTM6rAfRmKQYPbVPPUVehCvKbMeVN5fVus`, `CKnw9m5La47BFqgRmg8K6Ef4fpFiEZbe73uwESUKWfny`, `FWcwatFfkqaKcP8SPCq9Ny7DbkgbeUuFhLoRPBxG3sTg` |
| Resolve P1 before 2026-12-31 | rejected: `no snapshot on/after resolve_after yet` | not included |
| Reporter's function-call key tries `resolve_prediction` | refused by the access key | not included |

Check it yourself (a view call on testnet; the last step of the demo log runs the same call):

```
$ near contract call-function as-read-only nbproto-owner-1696.testnet snapshot_count json-args '{}' network-config testnet now
1
```

An RPC `tx` lookup of the snapshot transaction returned signer `nbproto-rep-1696.testnet`, receiver `nbproto-owner-1696.testnet`, public key `ed25519:8UcJs25HpfXkVJxPv38uMnk2867TZg11qoyp1PG4qhEq` (the function-call key), method `submit_snapshot`, status `SuccessValue`; `latest_snapshot` returned `evidence_hash` `7cc350b8c8149066a2bbbe9535dc5f24dd958d357edcf363a94edbf8ce9c1cc3`, which equals `sha256sum data/snapshot-2026-10-20/metrics.json`.

**Redeploy of the fixed contract.** After the resolution-rule fix, the new build (sha256 `89c1f889...59ac9`) was redeployed onto the same accounts without an init call (tx `HFyqaEt3zwJJiKH81crAwHdHBFWJCjLcj4CStFysw51o`). State survived (1 snapshot, 4 pending predictions) and the same negative checks were repeated against the new code; see `contract/testnet-run-redeploy.log`. A fully fresh end-to-end run was attempted but the testnet faucet answered HTTP 429 (Too Many Requests) after the earlier account creations, so `testnet_demo.sh` supports `REUSE=1` for this case.

That deployment uses the earlier reporter-only contract. In the current contract, predictions resolve only from attested snapshots. P1 (`supply_total_near`) and P2 (`buyback_wallet_near`) can resolve from the attested snapshot of 2026-12-31. P3 and P4 use metrics the worker does not compute yet, so they would resolve Void. The resolve path (Confirmed, Refuted, Void) is covered by unit tests only, not by a testnet transaction.

## Architecture

```
 FastNear transfers API ─┐
 archival RPC (headers,  ├─> pipeline/fetch.py ─> data/<dir>/*.json{,l} + manifest.json ─> pipeline/compute.py ─> metrics.json
   view_account)         │                                                                                            │
 DefiLlama ──────────────┘          verify.py (12 assertions) <── site/index.html <── mcp/server.py <─────────────────┤
                                                                                                                      v
                        reporter function-call key ── submit_snapshot ──> [Reported series] (informational)
                                                                                    contract `nearbooks`
 anyone ── request_snapshot(date, first block of date) + 0.03 NEAR ──> request_execution ──> outlayer.testnet
                                                                                     │ (pinned wasm sha256)
                         OutLayer TDX worker: worker/ wasm32-wasip2 ── HTTPS JSON-RPC ──> NEAR mainnet (archival)
                                                                                     │ yield/resume
             on_snapshot_result ──> [Attested series, one per date] <── resolve_prediction (anyone) ──> Confirmed | Refuted | Void
                                                                                     │
             public attestation (TDX quote, wasm/input/output hash) ──> contract/scripts/verify_attestation.py
```

- **Determinism.** `compute.py` reads only the data directory. `manifest.json` records the head block, as-of date and sha256 of each raw file; `compute.py` refuses to run if a file does not match. The committed `data/snapshot-2026-10-20/` was written by `fetch.py` on 2026-10-20.
- **Where the numbers come from.** Captured revenue is native NEAR arriving at `fefundsadmin.sputnik-dao.near`, `1csfundsadmin.sputnik-dao.near` and `buybacks.multisignature.near` from accounts other than those three, valued in USD by FastNear's `usd_amount`. Supply comes from block-header `total_supply`. Wallet balances come from `view_account`. Volume and spot price come from DefiLlama.
- **Contract.** Metric values are signed integers in micro-units (`value x 1e6`). There are two series.
  - **Attested series.** One snapshot per UTC date, observed at that date's first mainnet block. That makes it canonical: whoever triggers it and whenever, the result is the same. The first valid result wins. A missed day can be backfilled, but not before `earliest_date`, which only moves earlier and never blocks a Pending prediction's date.
  - **Reported series.** Append-only, strictly increasing dates, never in the future.
  - **Predictions.** They can only be registered with a future `resolve_after` and are never edited. The attested snapshot for `resolve_after` decides: `confirmed` or `refuted` if it has the metric, `void` if not. No other snapshot is consulted, so nobody can choose when the deciding observation is taken.
  - **Funds.** There are no stakes or payouts. Of the `request_snapshot` deposit, 0.01 NEAR goes to OutLayer, which refunds its unused part to the caller. A 0.02 NEAR storage reserve stays in the contract, and the callback refunds whatever the stored snapshot did not use. So requesters pay for storage, and the contract never sends anything beyond a request's own unused reserve.
- **Attested path.** The callback accepts output only:
  - from the configured OutLayer contract's promise result,
  - for the pinned wasm hash and an unchanged OutLayer account,
  - when it echoes this request's nonce, date and block, and parses strictly.

  It stores the OutLayer request id, the sha256 of the output bytes and the attestation URL. See `docs/attestation.md`, which also lists the owner's remaining powers: re-pinning the worker for future snapshots (with an event, and the hash recorded on every snapshot), and code upgrades through the account's full-access key.
- **Why a function-call key.** It limits what a compromised or automated reporter can do: it can append reported snapshots and nothing else (demonstrated above). Reported snapshots no longer decide predictions.

## Findings

Generated by `make readme` from `pipeline/verify.py`; `make verify` fails if this block is stale. Tolerances are computed from the same data (standard errors of daily residuals, spread across the 7/28/92-day windows, the lowest price and highest weekly take rate seen in the last 30 days), not typed constants. The only parameters that are not measured are listed in `pipeline/config.json` with their sources (HSP-029 values: issuance cap 2.5%, target 1.6%, published treasury share 10.13%, published realised issuance 2.45%).

<!-- findings:begin (generated by `make readme`) -->
| ID | Finding | Observed | Tolerance (derived from data) | Source | Check |
|---|---|---|---|---|---|
| F1a | Captured revenue rebuilt from raw wallet inflows matches DefiLlama year-to-date | $7,518,638 vs $7,561,260 (-0.56%) | +-0.92% (2 standard errors of the 292 daily residuals; sd $2,042/day) | FastNear transfers API; DefiLlama fees/near-intents dailyRevenue | PASS |
| F1b | Day-level series line up: correlation peaks at lag 0 (no timestamp shift) | r = 0.9987 at lag 0; best other lag in -3..+3: r = 0.2526 | lag 0 must beat every shifted lag (noise floor 0.25) | same | PASS |
| F2a | Buyback wallet balance is fully explained by transfers in minus transfers out (nothing burned from it) | 2,024,302 NEAR on-chain = 1,526,259 external + 510,068 internal in - 12,025 out (residual -0.34 NEAR) | +-2.0 NEAR (1e-6 of balance; covers gas/storage not in transfer rows) | archival RPC view_account; FastNear transfers API | PASS |
| F2b | Total supply rose in every sampled interval since 1 Jan (no net burn visible) | 42/42 intervals up; 1,283,940,417 -> 1,309,454,857 NEAR (+1.99%) | all intervals (no tolerance) | archival RPC block headers | PASS |
| F2c | DefiLlama labels all captured revenue as buyback, but only part of it ever reached the buyback wallet | 69.0% of 2,948,824 NEAR (since 2026-02-23) reached it; DefiLlama implies 100% | share must be below 99.44% (100% minus the revenue measurement gap F1a) | FastNear transfers API; DefiLlama dailyHoldersRevenue + methodology | PASS |
| F2d | fefundsadmin has spent almost nothing of what it holds | holds 1,917,105 NEAR; outflows 1.00 NEAR in 10 transfers | outflows must be below 1.9 NEAR (the same 1e-6-of-balance rounding scale as F2a) | FastNear transfers API; archival RPC | PASS |
| F3a | HSP-029's treasury share of supply growth (10.13%) reproduces from RPC | 7d: 10.207%, 28d: 10.180%, 92d: 10.147% vs published 10.13% | +-0.060 pp (spread of our own 7/28/92-day windows) | archival RPC (block header total_supply, treasury.near view_account); HSP-029 text | PASS |
| F3b | HSP-029's realised issuance (2.45%/yr) reproduces | 28d net issuance 87,677 NEAR/day = 2.444%/yr vs published 2.45% | +-0.022 pp (spread across our windows) | archival RPC; HSP-029 text | PASS |
| F4a | NEAR is not net-deflationary at today's volume: break-even volume exceeds current volume even under the least favourable price and take rate seen in the last 30 days | break-even $795M/day = 5.3x current $149M/day; conservative bound 4.5x | conservative bound uses price $4.54 (lowest of spot and 7d implied) and take 5.88 bps (highest weekly); must exceed 1.0x | DefiLlama dexs volume; FastNear transfers; archival RPC | PASS |
| F4b | At the proposed 1.6% issuance, break-even volume falls but stays above current volume | $502M/day = 3.4x current | conservative bound 2.9x must exceed 1.0x | as F4a; target rate from HSP-029 | PASS |
| F4c | Spot price used (DefiLlama coins) agrees with the USD valuation FastNear attached to recent transfers | spot $4.67; implied by 260 transfers in last 7d: median $4.99, range $4.54-$5.38 | spot must lie inside the 7d implied range | DefiLlama coins API; FastNear transfers API | PASS |
| F4d | Our daily sums reproduce DefiLlama's own 30-day revenue field (parsing check) | $2,308,206 vs published total30d $2,308,206 | exact | DefiLlama | PASS |
<!-- findings:end -->

Headline numbers behind the table (all in `data/snapshot-2026-10-20/metrics.json`):

| Metric | Value |
|---|---|
| Captured revenue 2026-01-01..2026-10-19 | $7,518,638 on-chain vs $7,561,260 DefiLlama |
| Last 30 days | $2,302,510 on-chain vs $2,308,206 DefiLlama |
| `buybacks.multisignature.near` balance | 2,024,302 NEAR (3 outflows, 12,025 NEAR) |
| Supply 2026-01-01 -> 2026-10-20 | 1,283,940,417 -> 1,309,454,857 NEAR (42 of 42 sampled intervals up) |
| Net issuance, trailing 28 days | 87,677 NEAR/day (about $410K/day) |
| Volume needed for net deflation | about $795M/day today (5.3x the $149M/day 30-day average); about $502M/day at the proposed 1.6% issuance |

## Limits and honesty

- **F1 is a consistency check, not independent proof.** DefiLlama's methodology says its revenue is "NEAR/wNEAR swept into its revenue wallets", which is the same underlying data as ours. What F1 shows is that the public number is reproducible from raw transfers and has no timing shift, not that the wallets are the right ones.
- **Only native NEAR is counted as revenue.** About $1.13M of USDC and other token inflows to the same wallets in the window is excluded (`non_near_inflows_usd_excluded` in `metrics.json`); DefiLlama's definition is also NEAR/wNEAR.
- **"Buyback is custody, not a burn" is an on-chain observation about this wallet, not about all possible burns.** Supply never fell between samples and the buyback wallet's outflows are 3 transfers (to `nf-payments.near` twice and one implicit account). The claim on revenue.near.org that buybacks "permanently remove NEAR from circulation" is not fetched or checked by this repo; DefiLlama's own methodology text says "not burned".
- **The break-even model is deliberately simple.** It assumes the 30-day take rate and price stay fixed, treats the whole measured net issuance (including the 10% that goes to the treasury) as the quantity to offset, and estimates fee burn as "what a 2.5% cap would mint minus what supply actually grew". Volume comes from DefiLlama only; gross fees and full-year volume are not rebuilt from chain data.
- **Resolution rule (fixed after two security reviews).**
  - First fix: an early version scanned up to 64 snapshots for the first one containing the metric. A reporter could omit a metric and so let a later snapshot decide.
  - Second fix: once anyone could trigger a snapshot "now", a caller could choose when the deciding snapshot was observed.
  - Now: only the attested snapshot for exactly `resolve_after`, observed at its first mainnet block, decides, and a missing metric gives a final `void`. Tests: `the_snapshot_for_resolve_after_decides_not_neighbours`, `a_later_snapshot_cannot_stand_in_for_the_missing_deciding_one`, `reported_snapshots_never_resolve_predictions`, `metric_the_worker_does_not_report_voids`.
  - Consequence: predictions on metrics the worker does not compute (P3 net issuance, P4 break-even volume) will resolve `void` until the worker computes them.
- **What the attestation does and does not cover.**
  - Covered: the pinned worker code, its exact input and output, and that a genuine, approved TDX worker ran it (checked off-chain, `docs/attestation.md`).
  - The contract cannot verify the TDX quote on-chain. It relies on the bytes arriving from the configured OutLayer contract for the pinned hash.
  - The worker trusts its RPC endpoints and does not check state proofs. The evidence hash commits to the raw responses it saw.
  - Reporter snapshots remain unverified (`evidence_hash` is supplied by the reporter).
  - The contract wasm is a `non-reproducible-wasm` build. The worker wasm builds byte-identically from another directory with the same toolchain.
- **Testnet only.** Nothing was deployed to mainnet. The testnet accounts are throwaway; their keys live outside the repository (git-ignored `.tools/`).
- **The dashboard and the MCP server read `metrics.json`**, not the chain or the contract. The page was checked in headless Chromium (render plus scripted clicks); no other browsers were tried.
- **Data moves.** Re-running `make data` later changes the as-of date, price and volume. The assertions are statistical or structural, so they should keep holding, but a failure on fresh data is a real signal and not necessarily a bug.
- **Dropped from the earlier prototype:** the meta-transaction (Delegate) share pilot, because it needs throttled NEARData block downloads and was a 73-block sample.
- **Rate limits.** FastNear's transfers API returns HTTP 429 under bursts, so a fresh pull takes minutes.

## Repository layout

```
pipeline/   fetch.py  compute.py  verify.py  update_readme.py  common.py  config.json  predictions.json
data/       snapshot-2026-10-20/   committed raw inputs + manifest.json + metrics.json (4.1 MB)
contract/   src/lib.rs  src/tests.rs  build.sh  scripts/{make_args.py,testnet_demo.sh,testnet_attested.sh,verify_attestation.py}
            testnet-run.log  testnet-run-redeploy.log  testnet-attested.log
worker/     src/{lib.rs,main.rs,tests.rs}  build.sh  scripts/{first_block.py,rank_intents_tokens.py}   OutLayer WASM (wasm32-wasip2)
docs/       attestation.md
mcp/        server.py
site/       index.html  metrics.json  screenshot.png
tests/      test_pipeline.py  test_mcp.py
Makefile  LICENSE (MIT)  README.md
```

## License

MIT, see `LICENSE`.
