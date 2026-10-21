# How to check an attested NEAR Books snapshot

An attested snapshot holds NEAR mainnet numbers read by a pinned WASM worker in an Intel TDX enclave run by OutLayer and delivered through OutLayer's on-chain yield/resume, with no reporter key. The live contract is `nearbooks.near`; `nearbooks-v6.okf-kill-1792497882.testnet` ran the same code first (`docs/deployment.md`).

## What is claimed

For a UTC date D the worker observes B, the first block of D (`prev.timestamp < D 00:00:00 UTC <= B.timestamp`), and B28, the first block of D minus 28 days. At both it reads total supply (block header) and the balances of `fefundsadmin.sputnik-dao.near`, `buybacks.multisignature.near` and `1csfundsadmin.sputnik-dao.near`. At B it also checks Intents reserves: `intents.near mt_supply` against `ft_balance_of("intents.near")` for the top 8 tokens by value. The contract derives:
- `net_issuance_near_per_day` = (supply at B - supply at B28) / 28,
- `revenue_wallets_gain_near_28d` = the change in the three wallets' combined balance,
- `revenue_wallets_gain_over_net_issuance` = the ratio of the two, omitted when supply did not grow.

Chain of custody:

1. Request. Anyone calls `request_snapshot(D, B, B28)`, or the keeper calls `keeper_request_snapshot` paid from the keeper budget. D must not precede `earliest_date` unless a Pending prediction resolves on D. The contract keeps a storage reserve and sends `{"n":"<nonce>","d":"D","b":"B","b28":"B28"}` to `outlayer.near` `request_execution` (`outlayer.testnet` on testnet), naming the worker by **the contract's pin for D** (in force at D 00:00 UTC) and the caller, or for keeper requests the contract, as `payer_account_id`.
2. Run. The TDX worker refuses the WASM unless its sha256 equals the pin, and refuses B or B28 if it is not the first block of its day. It reads from the archival RPC and prints about 565 bytes of JSON.
3. Store. `on_snapshot_result` accepts the resumed bytes only if the call is the contract's own promise result from the OutLayer contract the request went to, the pin for D is unchanged, nonce, date and both blocks match, the timestamp falls on D, and every field parses strictly (`"v":"3"`). It stores the snapshot with the sha256 of the exact bytes and the attestation URL, keeps its storage (about 1,085 bytes) and refunds the rest of the reserve.
4. Attest. OutLayer publishes the TDX quote, `wasm_hash`, `input_hash`, `output_hash`, caller and transaction; the quote's `report_data` commits to all of them.

## Checks

```
python3 -I contract/scripts/verify_attestation.py nearbooks.near 2026-10-21 \
    --wasm worker/res/nearbooks-worker.wasm --dcap "$(command -v dcap-qvl)"   # cargo install dcap-qvl-cli
```

A `*.testnet` contract id reads testnet (`worker.outlayer.testnet`, `testnet-api.outlayer.ai`). Recorded output (about 20 s, `contract/logs/mainnet-attest-check-2026-10-21.log`; long hashes shortened as `bbed939a…7963`):

```
$ python3 -I contract/scripts/verify_attestation.py nearbooks.near 2026-10-21 --wasm worker/res/nearbooks-worker.wasm --dcap /path/to/dcap-qvl
snapshot 2026-10-21: nonce 0, OutLayer request 2565, mainnet block 220871336 (ts 1792540800533675965)
attestation: https://api.outlayer.ai/attestations/by-request/2565
PASS  snapshot's attestation link == the allowlisted URL for its request id
PASS  attestation.request_id == snapshot request_id  2565
PASS  attestation.wasm_hash == snapshot wasm_hash  bbed939a…7963
PASS  attestation.executed_wasm_sha256 == wasm_hash
PASS  snapshot wasm_hash == the contract's pin for that date  outlayer.near
PASS  wasm_hash is a worker this repo vouches for (contract/known_workers.json)  format 3 (28-day lookback), commit 422a4e6
INFO  current pin: bbed939a…7963 (same); waiting proposal: None
PASS  local worker build sha256 == wasm_hash  bbed939a…7963
PASS  attestation.input_hash == sha256(contract input)  {"n":"0","d":"2026-10-21","b":"220871336","b28":"216835292"}
PASS  attestation.output_hash == snapshot output_sha256  9847ddd7…8654
PASS  transaction calls this contract's request_snapshot or keeper_request_snapshot  8K3s7mq3k1w2xun6hDESvwEmdqpPzhWn5oXqdqp34Yg (keeper_request_snapshot)
PASS  OutLayer receipt returned bytes hashing to output_hash
PASS  stored metrics == attested output (all 13, incl. the 28-day ones recomputed here)  {"bb":"2033718030159469463819379165396",...}
INFO  TDX quote 5006 bytes, version 4, MRTD 7fe60787222bd394..., RTMR3 5bb8cf0af5012f89... (= worker_measurement)
PASS  quote report_data == sha256(task fields): the quote is bound to this run  7d9db83d…a88b
PASS  quote MRTD+RTMR0-3 are in worker.outlayer.near approved measurements  (66 approved sets)
PASS  Intel DCAP signature chain verifies (dcap-qvl)  TCB status UpToDate
15/15 checks pass
```

Without `--wasm` and `--dcap` the run ends `13/13 checks pass`. The evidence hash `4c798974…fdefb` equals that of the testnet snapshot for the same date (testnet request 5258): two separate enclave runs read byte-identical RPC responses. Testnet runs: 13/13 in `contract/logs/testnet-attest-2026-10-21.log`, and 15/15 for 2026-10-20 on `nearbooks-v5`. The script never fetches a URL from contract state; it rebuilds it from the allowlisted API base and the numeric request id, and refuses redirects.

| # | Check | Executed? |
|---|---|---|
| 1 | `attestation.wasm_hash` = `executed_wasm_sha256` = the snapshot's `wasm_hash` = the contract's pin for that date = sha256 of a local `make worker` build | yes |
| 2 | that hash is listed in `contract/known_workers.json` (worker builds this repo vouches for) | yes |
| 3 | `attestation.input_hash` = sha256 of `{"n":"0","d":"2026-10-21","b":"220871336","b28":"216835292"}`: the run answered exactly this request | yes |
| 4 | `attestation.output_hash` = the snapshot's `output_sha256` | yes |
| 5 | the attested transaction called this contract's `request_snapshot` or `keeper_request_snapshot`; the OutLayer receipt returned bytes with that hash; all 13 stored metrics equal what those bytes say (the 28-day ones recomputed independently) | yes |
| 6 | the quote's `report_data[0:32]` = sha256 of the task fields (layout from OutLayer's `worker/src/tdx_attestation.rs`): the quote is bound to this run | yes |
| 7 | the quote's MRTD and RTMR0-3 are one of the approved sets in `worker.outlayer.near` (`get_approved_measurements`; `worker.outlayer.testnet` on testnet) | yes |
| 8 | Intel DCAP signature chain verifies with `dcap-qvl` 0.7.0, TCB status `UpToDate` (collateral from Phala's PCCS) | yes, for the mainnet run of 2026-10-21 and the testnet v5 run of 2026-10-20 |
| 9 | B and B28 are the first blocks of their days: `python3 -I worker/scripts/first_block.py 2026-10-21 --pair` prints `220871336 216835292` | yes |
| 10 | the evidence hash reproduces: a local `wasmtime run -S http` of the pinned WASM with the same input printed `sha` `875a2965…7ec0`, equal to the on-chain `evidence_hash` | yes, for 2026-10-20 on testnet; for 2026-10-21 the mainnet run (request 2565) and the testnet run (request 5258) stored the same `evidence_hash` |
| 11 | the approved measurements correspond to OutLayer's published open-source worker image | **documented only** |
| 12 | only TEE-registered worker keys can resolve OutLayer executions on-chain | **documented only**, read from OutLayer's source |

The dashboard and `get_attested_snapshots(verify=true)` repeat checks 1-5, without the local build, before marking a snapshot verified.

## Timelock on re-pinning

The pin (worker hash and URL, OutLayer contract, resource limits) is an append-only history. `propose_pin` emits `pin_proposed` with `apply_after_ns` = now + `pin_delay_secs` (fixed at `new`: 86,400 s on mainnet, 3,600 s on testnet). `apply_pin` is refused before then; it appends an entry from the block time and emits `worker_pinned`. `cancel_pin_proposal` drops a waiting proposal, and a new one replaces it and restarts the clock. The first pin applies at once.

**The pin for date D is the entry in force at D 00:00 UTC.** A re-pin applied at time A governs only dates starting at or after A; earlier dates keep the old pin even if requested later, including a Pending prediction's resolve date inside the window. So a new worker gets `pin_delay_secs` of public notice before it observes any date, and a date's observer never changes once the date has begun.

Testnet demo on `nearbooks-v5`: a re-pin (time limit 60 s to 90 s) proposed on 2026-10-20 at 16:01 UTC was refused at once and applied after 2026-10-21 00:00 UTC. `get_pin_for_date` then gave the 60 s entry for 2026-10-21 and the 90 s one for 2026-10-22, and the later-requested 2026-10-21 snapshot ran under 60 s (tx `HA7ZpS6v3QnTpXXhxg5np5BKdjXD6CnPWs1n2KRr2VPy`). Resolving P1 (due 2026-12-31) early was refused. Logs: `contract/logs/testnet-resolve.log`, `contract/logs/testnet-resolve-2026-10-21.log`, `contract/logs/testnet-attest-v5-2026-10-21.log` (13/13). Tests: `contract/src/tests.rs`, section "timelock".

## Owner powers

The owner is the contract account itself: `nearbooks.near` on mainnet (`nearbooks-v6…` and `nearbooks-v5…` on testnet).

| Power | Effect | Limit / visibility |
|---|---|---|
| `propose_pin` / `apply_pin` / `cancel_pin_proposal` | Decides which worker observes future dates | Timelocked (above). Events `pin_proposed`, `worker_pinned`, `pin_proposal_cancelled`. Every snapshot records its `wasm_hash`; the hashes this repo vouches for are in `contract/known_workers.json`, with a reproducible build. A worker that stops working only stops new snapshots: predictions stay Pending, none is decided wrongly. A worker that runs but reports wrong numbers is a risk the contract cannot detect; the timelock and the published hashes are the answer to it. |
| `add_prediction` | Registers claims | Only for a future `resolve_after`. Cannot edit, delete or resolve them. |
| `set_earliest_date` | Widens backfill | Can only move earlier. A Pending prediction's date is always requestable. |
| `set_reporter` | Rotates the reporter | Reported snapshots never resolve predictions. |
| `set_keeper` | Names the one account that may spend the keeper budget, and its daily cap | Cap at most 1 NEAR per UTC day (contract rule). Event `keeper_set`. The owner cannot spend the budget itself. |
| `withdraw_keeper_budget` | Takes unspent budget back to the owner | Only up to the recorded budget. Event `keeper_budget_withdrawn`. |
| `transfer_ownership` | Hands every power above to another account, for example a multisig | Event `owner_transferred`. |
| **Code upgrade through the account's full-access key** | **Can change any rule, including the timelock** | **Possible until that key is deleted**, the last step before submission. Until then, check the deployed `code_hash` against the build (`make verify-deploy`, which also lists the account's keys). |

The owner cannot write or alter attested snapshots, move requesters' funds (OutLayer refunds the run part; the contract returns only a request's own unused reserve) or resolve a prediction except by the public rule.

`nearbooks.near` still has its full-access key. Because it owns itself, deleting that key both freezes the code and ends the owner calls (re-pin, new predictions, keeper settings); the contract then runs only by its public rules, and anyone can still fund the keeper budget.

## Keeper budget

A function-call key cannot attach a deposit, so a bot holding only one cannot call the payable `request_snapshot`. The contract (v6) adds a prepaid budget:

| Call | Who | Effect |
|---|---|---|
| `fund_keeper()` | anyone, payable | budget += deposit (event `keeper_funded`) |
| `set_keeper(keeper_id, daily_cap)` | owner | names the keeper (or none) and the cap per UTC day, at most 1 NEAR |
| `keeper_request_snapshot(D, B, B28)` | the keeper only, no deposit | same checks as `request_snapshot`, then draws `min_request_deposit()` from the budget. Refused if that would exceed the day's cap (gross) or the budget. OutLayer's payer is the contract. Event `keeper_spent`. |
| `on_snapshot_result` | the contract | for a keeper request, the unused storage reserve is credited back to the budget, not transferred (`storage_settled` with `"to_budget":true`) |
| `withdraw_keeper_budget(amount)` | owner | sends unspent budget to the owner |
| `get_keeper()` | view | keeper, cap, budget, the day's spending, cost of one request |

The budget never exceeds what the contract holds: OutLayer's refund of its unused 0.01 NEAR (0.00862 NEAR on testnet, 2026-10-20) is a plain transfer the contract cannot attribute, so it is not credited, and neither is a failed OutLayer call.

The keeper account (`keeper.nearbooks.near`; testnet `nearbooks-keeper…`) has a function-call key for this contract (`keeper_request_snapshot`, `resolve_prediction`; 1 NEAR gas allowance, 0.5 NEAR on testnet), the only secret `.github/workflows/keeper.yml` gets, and a full-access key kept offline by the deployer. A leaked function-call key can spend at most the daily cap, on dates the attacker chooses, plus the remaining allowance. The protocol refuses everything else before execution (`keeper/probe.py` on testnet: `DepositWithFunctionCall`, `MethodNameMismatch`, `ReceiverMismatch`, `RequiresFullAccess`). Tests: `contract/src/tests.rs`, section "keeper budget".

## Reproducible contract build

The deployed wasm (`nearbooks.near`, and the identical `nearbooks-v6`) is `cargo near build reproducible-wasm` output. `contract/Cargo.toml` pins the image by digest (`sourcescan/cargo-near:0.21.1-rust-1.96.0@sha256:ccb22bb4…ee17`), the command (`cargo near build non-reproducible-wasm --locked --no-abi`) and the NEP-330 `repository`. cargo-near builds the committed tree, not the working copy, and embeds the public repository URL (`https://github.com/IhorMuliar/nearbooks`) and commit, readable on chain via `contract_source_metadata`. So the hash belongs to one commit (`source_commit` in `contract/deployment.json`), which anyone can rebuild or verify SourceScan-style. `contract/scripts/repro_build.sh` builds it in a fresh clone. Two separate clones of `30d995d` gave `644a8df3…2cae`, as does each `make verify-deploy`, which compares it with the on-chain `code_hash`. The worker (`worker/build.sh`) builds byte-identically from another directory with the same toolchain, but not in a pinned image. `make verify` reports local builds that differ from `contract/deployment.json`.

## What this does not prove

- The RPC data itself. The worker trusts `archival-rpc.mainnet.fastnear.com` (fallback `rpc.mainnet.near.org`) and checks no state proofs. `sha` commits to the raw responses; check 10 shows they can be refetched byte for byte when no fallback answered (`fb` = 0).
- On-chain quote verification. The contract cannot verify the TDX quote; checks 6-8 are off-chain.
- The token list. It is fixed in the worker (the top 8 on 2026-10-20, `worker/scripts/rank_intents_tokens.py`); changing it changes the hash and needs a timelocked re-pin.

## Design fixes after security reviews

- **Timing griefing**: canonical first blocks; one attested snapshot per date, first valid wins; predictions resolve only from the snapshot for exactly `resolve_after`.
- **Double refund**: the callback returns only the request's own unused storage reserve, never the OutLayer part.
- **Storage griefing**: requesters pay from a 2,000-byte reserve settled by measured `storage_usage`; backfill is bounded by `earliest_date`. On testnet a stored snapshot left the contract's free balance unchanged.
- **Owner blocking a prediction or swapping the worker before a deciding date**: `earliest_date` only moves earlier, and timelocked re-pins never touch a started date.
- **SSRF and spoofed contracts in the readers**: the dashboard and MCP server trust the mainnet RPC they read but fetch only allowlisted HTTPS origins (mainnet and archival RPCs, OutLayer's attestation API), rebuild attestation URLs from the request id and refuse redirects. The dashboard reads only its hard-coded contract.

Tests: `contract/src/tests.rs`, sections "attested snapshots (OutLayer)", "predictions" and "timelock".

## Earlier deployments

`nearbooks-v6` is in `docs/deployment.md`, "Testnet deployment". Before it:

- `nearbooks-v5` (testnet, live): timelocked, no keeper budget, `cargo near build non-reproducible-wasm` (`b9343afe…6b18`). It carries the timelock demo and its own predictions 0-6 (`contract/logs/testnet-attested.log`, `contract/logs/testnet-resolve.log`); `make verify-deploy-testnet` checks its code.
- Superseded: the first attested design, the canonical-block design before the storage fix and the untimelocked v4 ran end to end on testnet and were deleted, balances returned. Logs are in git history (`770990b`, `474734d`, `290311b`).
- Reporter-only prototype `nbproto-owner-1696.testnet`: a reporter key limited to `submit_snapshot` submitted a snapshot; the owner's `submit_snapshot` and the key's `resolve_prediction` were refused (`contract/logs/testnet-run.log`, `contract/logs/testnet-run-redeploy.log`). Reported snapshots never decide predictions.
