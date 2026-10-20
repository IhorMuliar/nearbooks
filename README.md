# NEAR Books

**NEAR Books rebuilds NEAR Intents' revenue, buyback and issuance numbers from raw chain data, reconciles them against DefiLlama, and publishes the result as a static dashboard, a read-only MCP server and a NEAR smart contract that stores dated metric snapshots and falsifiable predictions.** Every claim in the findings table is an assertion that `make verify` re-runs offline in about two seconds from a committed data snapshot, with tolerances derived from the data rather than typed in.

- **Track:** Thesis (an evidence-backed research entry; the contract and MCP server make it checkable by people and by agents).
- **Status:** prototype built for a feasibility measurement, snapshot as of 2026-10-20. Testnet only, no mainnet deployment, no funds involved.

## NEAR technology used

| What | How it is used here | Where |
|---|---|---|
| NEAR archival JSON-RPC (`archival-rpc.mainnet.fastnear.com`) | `block` headers for `total_supply` at 43 heights, `view_account` for wallet balances and `treasury.near` at past heights | `pipeline/fetch.py` |
| FastNear transfers API | Every native transfer into the three revenue wallets since 2026-01-01 (5,027 rows) | `pipeline/fetch.py` |
| `near-sdk-rs` 5.29.1 smart contract | `nearbooks` oracle: reporter-gated snapshots, owner-registered predictions, permissionless resolution | `contract/` |
| Function-call access keys | The reporter account holds a key that can call only `submit_snapshot` on the contract; the testnet run below signs with exactly that key | `contract/scripts/testnet_demo.sh` |
| `cargo-near` 0.22 | Builds the wasm (`cargo near build non-reproducible-wasm`) | `contract/build.sh` |
| NEAR testnet + faucet-sponsored accounts | Deployment and a full end-to-end run with tx hashes (below) | `contract/testnet-run.log` |
| NEAR Intents | The audited subject. This repo reads its revenue wallets and DefiLlama's Intents series; it does not call the Intents or 1Click APIs. | `pipeline/` |

## Quickstart (every command below was run; outputs are pasted from those runs)

Requirements: `python3` >= 3.10 (standard library only), `make`. For the contract: Rust stable with the `wasm32-unknown-unknown` target, optionally `cargo-near`. No API keys, no accounts, no environment variables.

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
make test-py          # pipeline + MCP server (16 tests)
make test-contract    # contract unit tests (29 tests)
```

```
----------------------------------------------------------------------
Ran 16 tests in 0.612s

OK
```
```
running 27 tests
test result: ok. 27 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.73s
```

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

Without `cargo-near`, `contract/build.sh` falls back to `cargo build --target wasm32-unknown-unknown --release`. That works only because `contract/.cargo/config.toml` sets `--cfg near`; a plain build without it compiled and deployed fine but every panic surfaced as a bare `WasmTrap(Unreachable)` with no message (found and fixed during this run).

### 6. Re-pull everything from the network (optional, about 6 minutes)

```
make verify-live      # fetch -> data/live, compute, assert on the fresh data
```

The duration is the FastNear transfers API rate limit (HTTP 429 with backoff). Results move a little with time (new blocks, new price); the findings are written to survive that, see "Limits".

## Testnet run (real transactions, faucet-sponsored accounts, no funds)

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

Predictions cannot be resolved yet: P1 and P2 become resolvable after 2026-12-31 and P3 and P4 after 2027-01-31, once the reporter submits a snapshot dated on or after those days. The resolve path (Confirmed, Refuted, Void) is covered by the unit tests only, not by a testnet transaction.

## Architecture

```
 FastNear transfers API ─┐
 archival RPC (headers,  ├─> pipeline/fetch.py ─> data/<dir>/*.json{,l} + manifest.json (sha256 of every file)
   view_account)         │                                   │
 DefiLlama (fees, volume,┘                                   v
   price)                                          pipeline/compute.py  (pure function of <dir>; no network, no clock)
                                                             │
                                                             v
                                                   metrics.json ──────────────┬─> pipeline/verify.py  (12 assertions, derived tolerances)
                                                             │                │        └─> README findings block (make readme)
                                                             │                ├─> site/index.html     (static dashboard)
                                                             │                ├─> mcp/server.py       (stdio MCP, read-only)
                                                             v                └─> contract/scripts/make_args.py
                                          snapshot{date, metrics x1e6, sha256(metrics.json)}          │
                                                                                                       v
                         reporter function-call key ── submit_snapshot ──> contract `nearbooks` <── add_prediction (owner)
                                                                                  │
                                                          anyone ── resolve_prediction ──> Confirmed | Refuted
```

- **Determinism.** `compute.py` reads only the data directory. `manifest.json` records the head block, as-of date and sha256 of each raw file; `compute.py` refuses to run if a file does not match. The committed `data/snapshot-2026-10-20/` was written by `fetch.py` on 2026-10-20.
- **Where the numbers come from.** Captured revenue is native NEAR arriving at `fefundsadmin.sputnik-dao.near`, `1csfundsadmin.sputnik-dao.near` and `buybacks.multisignature.near` from accounts other than those three, valued in USD by FastNear's `usd_amount`. Supply comes from block-header `total_supply`. Wallet balances come from `view_account`. Volume and spot price come from DefiLlama.
- **Contract.** Metric values are signed integers in micro-units (`value x 1e6`). Snapshots are append-only with strictly increasing dates and cannot be dated in the future (checked against block time). Predictions can only be registered with a future `resolve_after`, are never edited, and the first snapshot dated on or after `resolve_after` decides the outcome. If that snapshot reports the metric the prediction becomes `confirmed` or `refuted`; if it does not, it becomes `void`. There is no scan window and later snapshots are never consulted. There are no deposits, stakes or payouts.
- **Why a function-call key.** It limits what a compromised or automated reporter can do: it can append snapshots and nothing else (demonstrated above).

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
- **Resolution rule (fixed after a security review).** An earlier version scanned a window of up to 64 snapshots for the first one containing the metric. That let a reporter omit the metric from the first eligible snapshot so a later one would decide, and could leave a prediction unresolvable. Now the first snapshot on or after `resolve_after` decides, and a missing metric yields an explicit final `void` status. Tests: `missing_metric_in_first_eligible_snapshot_voids_and_later_snapshots_do_not_rescue`, `first_eligible_snapshot_decides_even_if_later_ones_differ`, `resolution_works_with_many_snapshots`, `void_is_final`. A reporter can still choose to omit a metric to void a prediction; that is visible on-chain, but it is a remaining trust assumption.
- **The oracle cannot verify truth.** The contract guarantees who reported a number, when, that it cannot be rewritten, and that predictions are judged mechanically. `evidence_hash` is supplied by the reporter and not verified on-chain. There is no TEE attestation or Sigstore provenance in this prototype; the function-call key is the only control. The wasm is a `non-reproducible-wasm` build (no Docker reproducible build was run).
- **Testnet only.** Nothing was deployed to mainnet. The testnet accounts are throwaway; their keys live outside the repository (git-ignored `.tools/`).
- **The dashboard and the MCP server read `metrics.json`**, not the chain or the contract. The page was checked in headless Chromium (render plus scripted clicks); no other browsers were tried.
- **Data moves.** Re-running `make data` later changes the as-of date, price and volume. The assertions are statistical or structural, so they should keep holding, but a failure on fresh data is a real signal and not necessarily a bug.
- **Dropped from the earlier prototype:** the meta-transaction (Delegate) share pilot, because it needs throttled NEARData block downloads and was a 73-block sample.
- **Rate limits.** FastNear's transfers API returns HTTP 429 under bursts, so a fresh pull takes minutes.

## Repository layout

```
pipeline/   fetch.py  compute.py  verify.py  update_readme.py  common.py  config.json  predictions.json
data/       snapshot-2026-10-20/   committed raw inputs + manifest.json + metrics.json (4.1 MB)
contract/   src/lib.rs  src/tests.rs  build.sh  scripts/{make_args.py,testnet_demo.sh}  testnet-run.log  testnet-run-redeploy.log
mcp/        server.py
site/       index.html  metrics.json  screenshot.png
tests/      test_pipeline.py  test_mcp.py
Makefile  LICENSE (MIT)  README.md
```

## License

MIT, see `LICENSE`.
