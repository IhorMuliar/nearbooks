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

Generated by `make readme` from `pipeline/verify.py`; `make verify` fails if this block (or any other generated block below) is stale. Tolerances are computed from the same data (standard errors of daily residuals, spread across the 7/28/92-day windows, the lowest price and highest weekly take rate seen in the last 30 days, display rounding of the published figure being compared, Wilson intervals at the cluster-effective sample size), not typed constants. The only parameters that are not measured are listed in `pipeline/config.json` with their sources: the HSP-029 values (issuance cap 2.5%, target 1.6%, published treasury share 10.13%, published realised issuance 2.45%), the published figures we compare against (`published_figures`: the revenue.near.org wallet breakdown read on 2026-10-20 and the two numbers Altcoin Buzz printed on 2026-10-19), and the sample design (`neardata_sample`: seed, blocks per month). Inputs: `data/snapshot-2026-10-20/` (M1 pull) and `data/snapshot-2026-10-20-m2/` (pre-2026 wallet history and the NEARData block sample; its manifest is bound to the first one by hash).

<!-- findings:begin (generated by `make readme`) -->
| ID | Finding | Observed | Tolerance (derived from data) | Source | Check |
|---|---|---|---|---|---|
| F1a | Captured revenue rebuilt from raw wallet inflows matches DefiLlama year-to-date | $7,518,638 vs $7,561,260 (-0.56%) | +-0.92% (2 standard errors of the 292 daily residuals; sd $2,042/day) | FastNear transfers API; DefiLlama fees/near-intents dailyRevenue | PASS |
| F1b | Day-level series line up: correlation peaks at lag 0 (no timestamp shift) | r = 0.9987 at lag 0; best other lag in -3..+3: r = 0.2526 | lag 0 must beat every shifted lag (noise floor 0.25) | same | PASS |
| F2a | Buyback wallet reconciles to the yoctoNEAR: balance = external revenue in + internal transfers in - outflows - its own transaction fees (nothing burned from it) | 2,024,301.607 NEAR (RPC) = 1,526,258.949 external revenue + 510,068.000 internal from 1csfundsadmin.sputnik-dao.near - 12,025.000 out (12,000 to 508aa18569e5..., 25 to nf-payments.near) -0.342 fees (14 balance steps with no transfer row, largest 0.0301); unexplained 0 yoctoNEAR | 0 yoctoNEAR unexplained (integer sums over 2,732 FastNear rows vs RPC); non-transfer moves must stay below the smallest outflow (5.0 NEAR) | archival RPC view_account; FastNear transfers API (amounts and start/end-of-block balances) | PASS |
| F2b | Total supply rose in every sampled interval since 1 Jan (no net burn visible) | 42/42 intervals up; 1,283,940,417 -> 1,309,454,857 NEAR (+1.99%) | all intervals (no tolerance) | archival RPC block headers | PASS |
| F2c | DefiLlama labels all captured revenue as buyback, but only part of it ever reached the buyback wallet | 69.0% of 2,948,824 NEAR (since 2026-02-23) reached it; DefiLlama implies 100% | share must be below 99.44% (100% minus the revenue measurement gap F1a) | FastNear transfers API; DefiLlama dailyHoldersRevenue + methodology | PASS |
| F2d | fefundsadmin has spent almost nothing of what it holds | holds 1,917,105 NEAR; outflows 1.00 NEAR in 10 transfers | outflows must be below 1.9 NEAR (1e-6 of its balance, i.e. rounding noise) | FastNear transfers API; archival RPC | PASS |
| F2e | revenue.near.org's per-wallet 'Total NEAR' is each wallet's all-time external revenue inflow, not its balance (the dashboard is right about revenue) | fefundsadmin: 1,916,643.1 vs ours 1,916,643.14; buybacks: 1,524,137.4 vs ours 1,524,137.43; 1csfundsadmin: 555,348.3 vs ours 555,348.27 (inflows to end of 2026-10-19, page read 2026-10-20); buyback wallet balance at that time 2,022,180.1 | each within +-0.05 NEAR (the page rounds to 0.1 NEAR); proposal bonds (refunded AttachedDeposit) excluded; all three wallet histories reconcile to 0 yoctoNEAR | revenue.near.org Wallet Breakdown (pinned in config.json); FastNear transfers API incl. pre-2026 history; archival RPC | PASS |
| F2f | The 2.02M vs 1.51M NEAR gap Altcoin Buzz reported on 2026-10-19 is the internal transfer from 1csfundsadmin, minus outflows, plus revenue received after the dashboard's cutoff (both published numbers are right) | NearBlocks 2,015,496.446 at block 220,621,578 vs ours 2,015,496.446; dashboard 1,510,410.8 = our external inflow to end of 2026-10-17 (-0.026); gap 505,085.6 = 510,068.0 internal - 12,025.0 out -0.3 fees + 7,043.0 revenue after the cutoff (closes to -0.026) | each published figure within its display rounding (+-0.0005 / +-0.05 NEAR); decomposition closes within their sum (+-0.0505) | Altcoin Buzz 2026-10-19 (figures pinned in config.json); FastNear transfers API | PASS |
| F3a | HSP-029's treasury share of supply growth (10.13%) reproduces from RPC | 7d: 10.207%, 28d: 10.180%, 92d: 10.147% vs published 10.13% | +-0.060 pp (spread of our own 7/28/92-day windows) | archival RPC (block header total_supply, treasury.near view_account); HSP-029 text | PASS |
| F3b | HSP-029's realised issuance (2.45%/yr) reproduces | 28d net issuance 87,677 NEAR/day = 2.444%/yr vs published 2.45% | +-0.022 pp (spread across our windows) | archival RPC; HSP-029 text | PASS |
| F4a | NEAR is not net-deflationary at today's volume: break-even volume exceeds current volume even under the least favourable price and take rate seen in the last 30 days | break-even $795M/day = 5.3x current $149M/day; conservative bound 4.5x | conservative bound uses price $4.54 (lowest of spot and 7d implied) and take 5.88 bps (highest weekly); must exceed 1.0x | DefiLlama dexs volume; FastNear transfers; archival RPC | PASS |
| F4b | At the proposed 1.6% issuance, break-even volume falls but stays above current volume | $502M/day = 3.4x current | conservative bound 2.9x must exceed 1.0x | as F4a; target rate from HSP-029 | PASS |
| F4c | Spot price used (DefiLlama coins) agrees with the USD valuation FastNear attached to recent transfers | spot $4.67; implied by 260 transfers in last 7d: median $4.99, range $4.54-$5.38 | spot must lie inside the 7d implied range | DefiLlama coins API; FastNear transfers API | PASS |
| F4d | Our daily sums reproduce DefiLlama's own 30-day revenue field (parsing check) | $2,308,206 vs published total30d $2,308,206 | exact | DefiLlama | PASS |
| F5a | The NEARData sample is exactly the seeded stratified plan (one stratum per month, uniform heights) | 360 blocks = 10 months x 36; seed 20261020; 1 skipped heights replaced by the next block; 0 blocks outside their month | heights must reproduce from the seed (compute.py refuses otherwise); every block inside its month; replacements < 20 heights away | NEARData block API; pipeline/neardata.py | PASS |
| F5b | No single 1Click referral channel carries a majority of referred swaps in 2026 (dip4 token_diff `referral`) | largest `1click-rango` 33% [21-47], next `1click-swapkit` 27% [17-40] (intervals overlap, so the two cannot be ranked); 66 referred swaps in 360 blocks, 10 channel tags | 95% Wilson interval (stratified, cluster-effective sample size) of the largest channel must lie below 50% | NEARData block sample (intents.near EVENT_JSON logs) | PASS |
| F6a | The share of transactions that are meta-transactions (NEP-366 Delegate action) fell during 2026 | 2026-01: 85.4% [72.9-92.7] of 970 tx; 2026-09: 36.4% [27.0-46.8] of 198 tx | 95% Wilson intervals at the cluster-effective sample size (design effect 20.0 / 2.3); intervals must not overlap | NEARData block sample (chunk transactions) | PASS |
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

### The buyback wallet, reconciled (F2a, F2e, F2f)

That the bought-back NEAR is held rather than burned is already public: SVRN's CEO said on the Bankless podcast that the tokens are not destroyed but held in a "permanent capital vehicle" ([Stocktwits, 2026-10-06](https://stocktwits.com/news-articles/markets/cryptocurrency/near-treasury-firm-ceo-says-buybacks-are-still-real-but-issuance-runs-six-times-higher/cZDteDIRBl3)), and DefiLlama's methodology says "not burned". This repo does not present that as a discovery. What it adds is a complete ledger for the wallet and an answer to the question [Altcoin Buzz](https://altcoinbuzz.io/near-intents-has-bought-back-6-68m-of-near-using-fees) left open on 2026-10-19: NearBlocks showed 2,015,496.446 NEAR in `buybacks.multisignature.near` while revenue.near.org listed 1,510,410.8 NEAR, and "nothing published alongside them explains the difference".

- **Ledger (F2a).** In integer yoctoNEAR over all 2,732 native-NEAR rows: RPC balance 2,024,301.607 NEAR = 1,526,258.949 NEAR of external revenue + 510,068.000 NEAR moved in from `1csfundsadmin.sputnik-dao.near` (502,793 NEAR of it on 2026-09-09) - 12,025 NEAR out (12,000 to one implicit account, 25 to `nf-payments.near`) - 0.342 NEAR of fees for transactions the wallet signed itself. Nothing is left unexplained, not even one yoctoNEAR. The fees show up as 14 balance steps with no transfer row in FastNear's own start/end-of-block balance columns; the largest is 0.0301 NEAR.
- **The dashboard is right about revenue (F2e).** The "Wallet Breakdown (All-time)" column on revenue.near.org is the NEAR each wallet received from outside the three revenue wallets, not its balance. With the full history (the two DAO wallets predate 2026, so their 2025 transfers are in the M2 snapshot), all three rows match our inflow to the end of the previous UTC day to within the page's 0.1 NEAR rounding: 1,524,137.4 vs 1,524,137.43 (buyback wallet), 1,916,643.1 vs 1,916,643.14 (`fefundsadmin`), 555,348.3 vs 555,348.27 (`1csfundsadmin`). Sputnik DAO proposal bonds (0.1 NEAR deposits refunded when a proposal resolves) are not counted, and neither are transfers between the three wallets. That is the right choice for a revenue figure: the 510,068 NEAR that `1csfundsadmin` forwarded was already counted as `1csfundsadmin` revenue, so counting it again under the buyback wallet would count it twice. DefiLlama's methodology says the same ("Transfers between those wallets are excluded").
- **The gap Altcoin Buzz saw (F2f).** Both of its numbers are correct and measure different things. NearBlocks' 2,015,496.446 NEAR at block 220,621,578 equals our balance at that block to the displayed 0.001 NEAR. The dashboard's 1,510,410.8 NEAR equals our external inflow up to the end of 2026-10-17 (the page lags by about a day). The 505,085.6 NEAR difference = 510,068.0 NEAR internal transfer - 12,025.0 NEAR out - 0.3 NEAR fees + 7,043.0 NEAR of revenue that arrived between the dashboard's cutoff and NearBlocks' block. That sum matches the difference to 0.026 NEAR.
- **Wording vs data.** The dashboard headline says the buybacks "permanently remove NEAR from circulation", and HSP-029 says fee-switch buybacks "stay removed". On chain the NEAR sits in an ordinary multisig account whose signers can move it (they have done so three times, 12,025 NEAR in total), and total supply rose in every sampled interval (F2b). Both statements describe a policy, not an on-chain lock.

### Cross-source reconciliation

Every row is computed by the pipeline (`pipeline/report.py`) from `metrics.json` and the published figures pinned in `pipeline/config.json`; "-" means the source does not publish that number.

<!-- sources:begin (generated by `make readme`) -->
| Item | On-chain (this repo) | DefiLlama | revenue.near.org | HSP-029 text | Other published | Finding |
|---|---|---|---|---|---|---|
| Captured revenue 2026-01-01..2026-10-19 | $7,518,638 | $7,561,260 (-0.56%) | not read (chart rendered client-side) | - | - | F1a |
| Captured revenue, 30 days to 2026-10-19 | $2,302,510 | $2,308,206 (-0.25%) | not read | - | Altcoin Buzz: $2.31M (quoting DefiLlama) | F1a, F4d |
| 'Buyback' revenue since 2026-02-23 | $6,694,211 (all captured revenue, same days) | $6,735,741 (HoldersRevenue) | - | - | Altcoin Buzz: $6.68M cumulative (DefiLlama) | F2c |
| Revenue into `fefundsadmin.sputnik-dao.near`, all time, to end of 2026-10-19 | 1,916,643.14 NEAR | - | 1,916,643.1 NEAR (read 2026-10-20) | - | - | F2e |
| Revenue into `1csfundsadmin.sputnik-dao.near`, all time, to end of 2026-10-19 | 555,348.27 NEAR | - | 555,348.3 NEAR (read 2026-10-20) | - | - | F2e |
| Revenue into `buybacks.multisignature.near`, all time, to end of 2026-10-19 | 1,524,137.43 NEAR | - | 1,524,137.4 NEAR (read 2026-10-20) | - | - | F2e |
| Buyback wallet balance | 2,024,301.607 NEAR at block 220,800,718; 2,015,496.446 at 220,621,578 | - | not shown (the wallet row is revenue in, see F2e) | - | NearBlocks via Altcoin Buzz: 2,015,496.446 NEAR at block 220,621,578 | F2a, F2f |
| Share of captured NEAR that reached the buyback wallet since 2026-02-23 | 69.0% | 100% (labels all captured revenue as buyback) | - | - | - | F2c |
| Buyback NEAR burned | 0 (balance fully reconciled; supply rose in every sampled interval) | "not burned" (methodology) | "feeding buybacks that permanently remove NEAR from circulation" | "Intents fee-switch buybacks ... stay removed" | SVRN CEO (Stocktwits 2026-10-06): held, not destroyed | F2a, F2b |
| Treasury share of supply growth | 10.147% (92 days) | - | - | 10.13% | - | F3a |
| Realised net issuance | 2.444%/yr (28 days) | - | - | 2.45%/yr | - | F3b |
<!-- sources:end -->

### Channel mix and meta-transactions (NEARData block sample, F5, F6)

**Method.** `pipeline/fetch_m2.py` draws a stratified sample of blocks: one stratum per calendar month of 2026 (bounded by the first block at or after UTC midnight, found over archival RPC), 36 heights per month drawn uniformly without replacement with `random.Random("20261020:<month>")`. A skipped height is replaced by the next block (1 of 360). Blocks come from NEARData (`https://mainnet.neardata.xyz/v0/block/<h>`). From one IP it allowed a burst of about 20 requests and then about 0.23 blocks/s, answering HTTP 429 above that, so the fetcher spaces requests 3.8 s apart and backs off on 429. The committed pull took 1,656 s (27.6 min) and was answered with 429 sixty times. Per block we keep only a raw extract (`neardata_sample.jsonl`, 0.6 MB): every `EVENT_JSON` log from receipts executed on `intents.near`, verbatim, and every chunk transaction's receiver and action kinds. `compute.py` rebuilds the seeded plan and refuses the sample if any height or the order differs, so blocks cannot be added, dropped or swapped without detection.

**Estimators.** A *referred swap* is a dip4 `token_diff` entry that carries a `referral` tag. The 1Click API sets the tag to `1click-<channel>`; solver legs and direct intents carry no tag and are not counted. A *meta-transaction* is a chunk transaction with a NEP-366 `Delegate` action. Blocks are clusters (one block can hold many relayed transactions), so each share is a ratio estimate whose variance comes from the per-block residuals. The 95% Wilson interval is taken at the effective sample size (units / design effect, never more than the units), and the 2026 row is a combined ratio estimate weighted by the number of blocks in each month.

**What it shows.** 66 referred swaps turned up in 360 blocks (about 0.18 per block), under 10 channel tags. At that density the per-month channel mix is mostly noise (3 to 13 swaps a month); the pooled 2026 row is the usable result. `1click-rango` (33% [21-47]) and `1click-swapkit` (27% [17-40]) are the two largest channels and cannot be ranked, and no channel has a majority (F5b). One pattern is visible but was noticed only after looking at the data, so it is not asserted: Rango tags dominate January and February (11 of 14), SwapKit tags dominate August to October (15 of 23). Meta-transactions are much better measured because blocks hold far more transactions than swaps. Their share was 79-87% of transactions in January to March, 45-65% in April to July and 30-36% in August to October (F6a: the January and September intervals do not overlap). In the same period the number of transactions per sampled block fell from about 27 to about 6, which is consistent with one large relayed workload going away. January's design effect of 20 shows how clustered that workload was. This replaces the 73-block pilot of the earlier prototype (78-88% in Q1, 22-30% in September and October). This sample reproduces the pilot's Q1 range and puts September and October somewhat higher.

<!-- channels:begin (generated by `make readme`) -->
| Month | Blocks | Referred swaps | `1click-rango` | `1click-swapkit` | `1click-protocol-fees` | `1click-unknown` | `1click-intents.tg` | `1click-1cs` | other |
|---|---|---|---|---|---|---|---|---|---|
| 2026-01 | 36 | 11 | 82% [50-95] | 0% [0-26] | 9% [2-39] | 0% [0-26] | 9% [2-39] | 0% [0-26] | 0% [0-26] |
| 2026-02 | 36 | 3 | 67% [17-95] | 0% [0-56] | 0% [0-56] | 33% [5-83] | 0% [0-56] | 0% [0-56] | 0% [0-56] |
| 2026-03 | 36 | 8 | 0% [0-32] | 0% [0-32] | 25% [7-59] | 12% [2-48] | 12% [2-48] | 12% [2-48] | 38% [13-70] |
| 2026-04 | 36 | 3 | 0% [0-56] | 33% [6-80] | 67% [20-94] | 0% [0-56] | 0% [0-56] | 0% [0-56] | 0% [0-56] |
| 2026-05 | 36 | 3 | 33% [6-80] | 33% [6-80] | 0% [0-56] | 0% [0-56] | 33% [6-80] | 0% [0-56] | 0% [0-56] |
| 2026-06 | 36 | 7 | 14% [2-54] | 29% [8-64] | 14% [2-54] | 29% [7-66] | 14% [2-54] | 0% [0-35] | 0% [0-35] |
| 2026-07 | 36 | 8 | 62% [27-88] | 0% [0-32] | 12% [2-49] | 12% [2-49] | 0% [0-32] | 12% [2-49] | 0% [0-32] |
| 2026-08 | 36 | 13 | 15% [4-42] | 69% [42-87] | 0% [0-23] | 0% [0-23] | 0% [0-23] | 0% [0-23] | 15% [4-45] |
| 2026-09 | 36 | 5 | 20% [4-63] | 20% [4-63] | 0% [0-43] | 20% [4-63] | 0% [0-43] | 20% [4-63] | 20% [4-63] |
| 2026-10 (partial) | 36 | 5 | 0% [0-43] | 100% [57-100] | 0% [0-43] | 0% [0-43] | 0% [0-43] | 0% [0-43] | 0% [0-43] |
| **2026 pooled** (stratified) | 360 | 66 | **33% [21-47]** | **27% [17-40]** | **11% [5-21]** | **9% [4-20]** | **6% [2-15]** | **5% [2-13]** | **9% [4-19]** |
<!-- channels:end -->

<!-- metatx:begin (generated by `make readme`) -->
| Month | Blocks | Transactions | Meta-transaction (Delegate) share [95% CI] | Design effect |
|---|---|---|---|---|
| 2026-01 | 36 | 970 | 85.4% [72.9-92.7] | 20.0 |
| 2026-02 | 36 | 783 | 78.9% [62.5-89.4] | 23.4 |
| 2026-03 | 36 | 1,057 | 86.9% [76.6-93.1] | 16.5 |
| 2026-04 | 36 | 217 | 65.0% [58.4-71.0] | 0.8 |
| 2026-05 | 36 | 186 | 51.1% [42.2-59.9] | 1.6 |
| 2026-06 | 36 | 211 | 44.5% [36.7-52.7] | 1.5 |
| 2026-07 | 36 | 214 | 59.3% [50.3-67.8] | 1.8 |
| 2026-08 | 36 | 174 | 31.0% [23.4-39.9] | 1.5 |
| 2026-09 | 36 | 198 | 36.4% [27.0-46.8] | 2.3 |
| 2026-10 (partial) | 36 | 209 | 30.1% [21.8-40.0] | 2.2 |
| **2026 pooled** (stratified) | 360 | 4,219 | **72.2% [64.6-78.7]** | - |
<!-- metatx:end -->

## Limits and honesty

- **F1 is a consistency check, not independent proof.** DefiLlama's methodology says its revenue is "NEAR/wNEAR swept into its revenue wallets", which is the same underlying data as ours. What F1 shows is that the public number is reproducible from raw transfers and has no timing shift, not that the wallets are the right ones.
- **Only native NEAR is counted as revenue.** About $1.13M of USDC and other token inflows to the same wallets in the window is excluded (`non_near_inflows_usd_excluded` in `metrics.json`); DefiLlama's definition is also NEAR/wNEAR.
- **"Buyback is custody, not a burn" is an on-chain observation about this wallet, not about all possible burns.** Supply never fell between samples and the buyback wallet's outflows are 3 transfers (to `nf-payments.near` twice and one implicit account). The headline on revenue.near.org that buybacks "permanently remove NEAR from circulation" is quoted from the page (read 2026-10-20) and set against the ledger in "The buyback wallet, reconciled"; DefiLlama's own methodology text says "not burned".
- **The break-even model is deliberately simple.** It assumes the 30-day take rate and price stay fixed, treats the whole measured net issuance (including the 10% that goes to the treasury) as the quantity to offset, and estimates fee burn as "what a 2.5% cap would mint minus what supply actually grew". Volume comes from DefiLlama only; gross fees and full-year volume are not rebuilt from chain data.
- **Resolution rule (fixed after a security review).** An earlier version scanned a window of up to 64 snapshots for the first one containing the metric. That let a reporter omit the metric from the first eligible snapshot so a later one would decide, and could leave a prediction unresolvable. Now the first snapshot on or after `resolve_after` decides, and a missing metric yields an explicit final `void` status. Tests: `missing_metric_in_first_eligible_snapshot_voids_and_later_snapshots_do_not_rescue`, `first_eligible_snapshot_decides_even_if_later_ones_differ`, `resolution_works_with_many_snapshots`, `void_is_final`. A reporter can still choose to omit a metric to void a prediction; that is visible on-chain, but it is a remaining trust assumption.
- **The oracle cannot verify truth.** The contract guarantees who reported a number, when, that it cannot be rewritten, and that predictions are judged mechanically. `evidence_hash` is supplied by the reporter and not verified on-chain. There is no TEE attestation or Sigstore provenance in this prototype; the function-call key is the only control. The wasm is a `non-reproducible-wasm` build (no Docker reproducible build was run).
- **Testnet only.** Nothing was deployed to mainnet. The testnet accounts are throwaway; their keys live outside the repository (git-ignored `.tools/`).
- **The dashboard and the MCP server read `metrics.json`**, not the chain or the contract. The page was checked in headless Chromium (render plus scripted clicks); no other browsers were tried.
- **Data moves.** Re-running `make data` later changes the as-of date, price and volume. The assertions are statistical or structural, so they should keep holding, but a failure on fresh data is a real signal and not necessarily a bug.
- **The block sample is small because NEARData is throttled.** 360 blocks is what fits in under 30 minutes at about 0.23 blocks/s. That is plenty for the meta-transaction share but thin for the channel mix: 66 referred swaps for the year, so monthly channel shares carry intervals tens of points wide. `--per-stratum` scales the sample for a longer run. The channel definition covers only 1Click-tagged swaps; untagged intents (direct API users, solver legs) are outside it, and the tag names are the integrators' own (`1click-unknown` is a tag, not a gap in our parsing).
- **The published figures are pinned, not fetched.** revenue.near.org renders most of its numbers client-side; the wallet-breakdown values were read from the page on 2026-10-20 and typed into `config.json` together with the URL. A later reading will differ by the revenue received since then; F2e uses the previous UTC day as the cutoff, which is what the page matched on both dates we have (2026-10-17 for Altcoin Buzz's reading, 2026-10-19 for ours).
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
