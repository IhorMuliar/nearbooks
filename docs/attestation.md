# How to check an attested NEAR Books snapshot

An **attested** snapshot holds NEAR mainnet numbers read by a pinned WASM worker inside an Intel TDX enclave run by OutLayer. The worker's output reached the `nearbooks` contract through OutLayer's on-chain yield/resume, so no reporter key is involved. The live contract is `nearbooks.near` on mainnet; the earlier testnet contract `nearbooks-v6.okf-kill-1792497882.testnet` ran the same code first. This page shows how anyone can check a snapshot, and it marks which steps were actually executed. The deployment itself (contract, hashes, transactions) is listed in `docs/deployment.md`, generated from `contract/deployment.json`.

## What is claimed

For a UTC date D, a snapshot describes two observation points:
- **B**, the first block of D: `prev.timestamp < D 00:00:00 UTC <= B.timestamp`,
- **B28**, the first block of D minus 28 days, by the same rule.

At B the worker reads:
- total supply (block header),
- the balances of `fefundsadmin.sputnik-dao.near`, `buybacks.multisignature.near` and `1csfundsadmin.sputnik-dao.near`,
- an Intents reserves check: `intents.near mt_supply` against `ft_balance_of("intents.near")` for the top 8 tokens by value.

At B28 it reads total supply and the same three balances. From the two observations the contract derives three metrics:
- `net_issuance_near_per_day` = (supply at B - supply at B28) / 28,
- `revenue_wallets_gain_near_28d` = the change in the three wallets' combined balance,
- `revenue_wallets_gain_over_net_issuance` = the ratio of the two growths, omitted when supply did not grow.

The chain of custody:

1. **Request.** Anyone calls `request_snapshot(D, B, B28)`, or the keeper calls `keeper_request_snapshot(D, B, B28)` and the deposit comes from the keeper budget (see "Keeper budget"). D must not be before `earliest_date`, unless a Pending prediction resolves on D. The contract:
   - keeps a storage reserve from the deposit,
   - sends `{"n":"<nonce>","d":"D","b":"B","b28":"B28"}` to `outlayer.near` `request_execution` (`outlayer.testnet` on testnet),
   - names the worker by the sha256 that is **the contract's pin for D**: the pin in force at D 00:00 UTC, see "Timelock",
   - names the caller as `payer_account_id` (for keeper requests: the contract itself).
2. **Run.** An OutLayer TDX worker downloads the WASM and refuses it unless its sha256 equals the pin. The worker refuses unless B and B28 are the first blocks of their days. It reads everything at those blocks from the archival RPC and prints about 565 bytes of JSON.
3. **Store.** OutLayer resumes the yielded promise with those bytes. `on_snapshot_result` accepts them only if all of these hold:
   - the call comes from the contract itself (the promise result of the OutLayer contract the request went to),
   - the pin for D is still the one the request used,
   - the nonce, the date and both blocks match the request,
   - the timestamp falls on D,
   - every field parses strictly (format `"v":"3"`).

   It then stores the snapshot under D with the sha256 of the exact bytes and the attestation URL. It keeps the bytes the snapshot staked (about 1,085) out of the reserve and refunds the rest.
4. **Attest.** OutLayer publishes an attestation for the run. It contains the TDX quote, `wasm_hash`, `input_hash`, `output_hash`, the caller and the transaction. The quote's `report_data` commits to all of them.

## Checks

```
python3 -I contract/scripts/verify_attestation.py nearbooks.near 2026-10-21 \
    --wasm worker/res/nearbooks-worker.wasm --dcap "$(command -v dcap-qvl)"   # cargo install dcap-qvl-cli
```

The network follows the contract id: `*.testnet` reads testnet, its OutLayer register `worker.outlayer.testnet` and `testnet-api.outlayer.ai`. Recorded output (network, about 20 s; `contract/mainnet-attest-check-2026-10-21.log`; long hashes shortened to their first and last characters (`bbed939a…7963`), the script prints them in full):

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

Without `--wasm` and `--dcap` the local-build line is missing, the DCAP line says `SKIP` and the run ends with `13/13 checks pass`. This snapshot was requested by the keeper (see "Keeper budget" below), so the transaction is `keeper_request_snapshot`, signed by its function-call key. Its evidence hash `4c798974…fdefb` equals that of the testnet snapshot of the same date (OutLayer request 5258 on testnet): two separate enclave runs read byte-identical RPC responses. The testnet checks are in `contract/testnet-attest-2026-10-21.log` (13/13) and, for 2026-10-20 on `nearbooks-v5` with `--wasm` and `--dcap`, 15/15.

The script never fetches a URL taken from contract state. It rebuilds the attestation URL from the allowlisted API base and the numeric request id, and it refuses redirects.

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

The dashboard and `get_attested_snapshots(verify=true)` repeat checks 1-5 (without the local build) in the browser or in Python. They mark a snapshot verified only if all of them hold.

## Timelock on re-pinning

The pin (worker hash, worker URL, OutLayer contract, resource limits) is kept as an append-only history:
- `propose_pin` (owner) emits `pin_proposed` with `apply_after_ns` = now + `pin_delay_secs`. The delay is fixed at `new`: 86,400 s (24 h) on mainnet, 3,600 s on testnet.
- `apply_pin` (owner, refused before `apply_after_ns`) appends the entry with `from_ns` = the block time and emits `worker_pinned`.
- `cancel_pin_proposal` drops a waiting proposal. A new proposal replaces a waiting one and restarts the clock.
- The first pin applies at once, because no snapshot can exist before it.

**The pin for date D is the entry in force at D 00:00 UTC.** A re-pin applied at time A therefore governs only dates that start at or after A. Every date that started earlier keeps its old pin, even if its snapshot is requested after A. This covers a Pending prediction's resolve date inside the proposal window. So readers get `pin_delay_secs` of public notice before a new worker can observe any date, and a date's observer never changes once that date has begun.

On testnet (`nearbooks-v5`, which stays live for this) a re-pin was proposed on 2026-10-20 at 16:01 UTC. It only changes the time limit from 60 s to 90 s, so `get_pin_for_date` shows which entry governs which date. Applying it right away was refused. What happened after 2026-10-21 00:00 UTC is in `contract/testnet-resolve.log`:
After 2026-10-21 00:00 UTC the owner applied the re-pin (event `worker_pinned`). `get_pin_for_date` then returned the old entry (60 s) for 2026-10-21, because that date began before the apply, and the new entry (90 s) for 2026-10-22. The attested snapshot of 2026-10-21 was requested afterwards and still ran under the 60 s entry (tx `HA7ZpS6v3QnTpXXhxg5np5BKdjXD6CnPWs1n2KRr2VPy`). Predictions 4, 5 and 6 resolved confirmed, refuted and void (txs `24r5UjDCZEEvnrvewEfHagRYT6QLUxPNvH6NPaDPDbU4`, `JBMuF7RLxKRLENZ7R4Sw8cbrsYcXav1SCHfJTMNT8msr`, `5rSX7oh6MFjb6ZSCTEz5UjjRwPUa6UDTkPBUAArZeaeE`), and resolving P1 (due 2026-12-31) was refused. Run log: `contract/testnet-resolve-2026-10-21.log`; `verify_attestation.py nearbooks-v5.okf-kill-1792497882.testnet 2026-10-21`: 13/13 checks pass (`contract/testnet-attest-v5-2026-10-21.log`).

Contract tests:
- `repin_cannot_be_applied_before_the_delay`
- `applied_pin_governs_only_dates_that_start_after_it`
- `pending_prediction_date_inside_the_window_keeps_the_old_pin`
- `repin_while_in_flight_does_not_change_the_pin_for_that_date`
- `a_new_proposal_replaces_the_waiting_one_and_restarts_the_clock`
- `owner_can_cancel_a_proposal`
- `only_the_owner_applies_or_cancels`
- `first_pin_applies_at_once_and_covers_earlier_dates`
- `delay_is_fixed_at_init`

## Owner powers

The owner is the contract account itself: `nearbooks.near` on mainnet (`nearbooks-v6…` and `nearbooks-v5…` on testnet).

| Power | Effect | Limit / visibility |
|---|---|---|
| `propose_pin` / `apply_pin` / `cancel_pin_proposal` | Decides which worker observes future dates | Timelocked (above). Events `pin_proposed`, `worker_pinned`, `pin_proposal_cancelled`. Every snapshot records its `wasm_hash`. The repo publishes the hashes it vouches for in `contract/known_workers.json`. A broken pin only stops new snapshots (liveness): predictions stay Pending, none is decided wrongly. |
| `add_prediction` | Registers claims | Only for a future `resolve_after`. Cannot edit, delete or resolve them. |
| `set_earliest_date` | Widens backfill | Can only move earlier. A Pending prediction's date is always requestable. |
| `set_reporter` | Rotates the reporter | Reported snapshots never resolve predictions. |
| `set_keeper` | Names the one account that may spend the keeper budget, and its daily cap | Cap at most 1 NEAR per UTC day (contract rule). Event `keeper_set`. The owner cannot spend the budget itself. |
| `withdraw_keeper_budget` | Takes unspent budget back to the owner | Only up to the recorded budget. Event `keeper_budget_withdrawn`. |
| `transfer_ownership` | Hands every power above to another account, for example a multisig | Event `owner_transferred`. |
| **Code upgrade through the account's full-access key** | **Can change any rule, including the timelock** | **Possible until that key is deleted**, which is the last step before submission. Until then, check the deployed `code_hash` against the build (`make verify-deploy`, which also lists the account's keys). |

The owner cannot write or alter attested snapshots. It cannot move requesters' funds: OutLayer refunds the run part, and the contract only returns a request's own unused storage reserve. It cannot resolve a prediction except by the public rule.

**Mainnet.** `nearbooks.near` was deployed on 2026-10-21 with a 24 h timelock, owned by itself. Its full-access key is still on the account. Deleting it, the last step before submission, freezes the code. Because the owner is the contract account itself, it also ends the owner calls (re-pin, new predictions, keeper settings): from then on the contract runs only by its public rules, and anyone can still fund the keeper budget.

## Keeper budget

A NEAR function-call access key cannot attach a deposit (the protocol refuses such a transaction with `DepositWithFunctionCall`). `request_snapshot` is payable, so a bot that holds only such a key cannot call it. The contract (v6) adds a prepaid budget:

| Call | Who | Effect |
|---|---|---|
| `fund_keeper()` | anyone, payable | budget += deposit (event `keeper_funded`) |
| `set_keeper(keeper_id, daily_cap)` | owner | names the keeper (or none) and the cap per UTC day, at most 1 NEAR |
| `keeper_request_snapshot(D, B, B28)` | the keeper only, no deposit | same checks as `request_snapshot`, then draws `min_request_deposit()` from the budget. Refused if that would exceed the day's cap (gross) or the budget. OutLayer's payer is the contract. Event `keeper_spent`. |
| `on_snapshot_result` | the contract | for a keeper request, the unused storage reserve is credited back to the budget, not transferred (`storage_settled` with `"to_budget":true`) |
| `withdraw_keeper_budget(amount)` | owner | sends unspent budget to the owner |
| `get_keeper()` | view | keeper, cap, budget, the day's spending, cost of one request |

Accounting is conservative. OutLayer refunds the unused part of its 0.01 NEAR with a plain transfer to the payer. That was 0.00862 NEAR in the testnet run of 2026-10-20. The contract cannot attribute a plain transfer, so the refund stays in its free balance and is not credited. A failed OutLayer call is not credited either, for the same reason as on the public path. The budget therefore never exceeds what the contract really holds for it.

The keeper account (`keeper.nearbooks.near`; on testnet `nearbooks-keeper…`) has two keys:
- a function-call key: receiver = this contract, methods `keeper_request_snapshot` and `resolve_prediction`, allowance 1 NEAR of gas (0.5 NEAR on testnet),
- a full-access key, which stays offline with the deployer.

The function-call key is the only secret the scheduled workflow (`.github/workflows/keeper.yml`) gets. If it leaks, the attacker can spend at most:
- the daily cap, on snapshots of dates they choose,
- the remaining gas allowance.

The protocol refuses everything else before execution. `keeper/probe.py` showed this on testnet: `DepositWithFunctionCall`, `MethodNameMismatch`, `ReceiverMismatch` and `RequiresFullAccess`. Contract tests cover the cap, the keeper-only rule, the budget accounting and the refunds (`contract/src/tests.rs`, section "keeper budget").

## Reproducible contract build

The deployed contract (`nearbooks.near`, and `nearbooks-v6` on testnet: the same wasm) is `cargo near build reproducible-wasm` output. `contract/Cargo.toml` pins the build image by digest (`sourcescan/cargo-near:0.21.1-rust-1.96.0@sha256:ccb22bb4…ee17`), the command (`cargo near build non-reproducible-wasm --locked --no-abi`) and the NEP-330 `repository`. cargo-near builds the committed tree, not the working copy. It embeds the repository URL and the commit in the wasm, readable on chain through `contract_source_metadata`. So the hash belongs to one commit, recorded as `source_commit` in `contract/deployment.json`.

`contract/scripts/repro_build.sh` clones the repository into a temporary directory, checks out that commit and builds there, so uncommitted changes and later commits cannot change the result. Two builds of `30d995d` from separate clones gave the same sha256 (`644a8df3…2cae`), and so does each `make verify-deploy`, which compares the result with the on-chain `code_hash`. `make wasm` runs the same build into `contract/res/`.

The worker (`worker/build.sh`) builds byte-identically from another directory with the same toolchain, but it is not built in a pinned image. `make verify` reports a local contract or worker build that differs from `contract/deployment.json`.

The URL in the metadata (`https://github.com/IhorMuliar/nearbooks`) has not been published yet. Until it is, a third party needs a copy of this repository's history to rebuild, and SourceScan-style verification is not possible.

## What this does not prove

- **The RPC data itself.** The worker trusts `archival-rpc.mainnet.fastnear.com` (fallback `rpc.mainnet.near.org`) and does not check state proofs. `sha` commits to the raw responses. Check 10 shows they can be refetched byte for byte when no fallback answered (`fb` = 0).
- **On-chain quote verification.** The contract cannot verify the TDX quote. Checks 6-8 are off-chain.
- **The token list.** It is fixed in the worker (the top 8 on 2026-10-20, `worker/scripts/rank_intents_tokens.py`). Changing it changes the hash and needs a timelocked re-pin.

## Design fixes after security reviews

- **Timing griefing.** The observation point is canonical: the first block of the requested day (and of 28 days earlier), which the worker enforces. Attested snapshots form their own series, one per date, and the first valid one wins. Predictions resolve only from the attested snapshot for exactly `resolve_after`. Test: `a_later_snapshot_cannot_stand_in_for_the_missing_deciding_one`.
- **Double refund.** The contract never re-sends the OutLayer part of a deposit. The callback only returns the unused part of that request's own storage reserve. Tests: `failed_outlayer_call_stores_nothing_and_returns_only_the_reserve` and `callback_never_sends_more_than_the_requests_own_unused_reserve`.
- **Storage griefing.** The requester pays storage from a 2,000-byte reserve, settled by measured `storage_usage`. Backfill is bounded by `earliest_date`. Test: `storage_is_paid_by_the_requester`. On testnet the contract's free balance was identical before and after a stored snapshot.
- **Owner blocking a prediction.** `earliest_date` only moves earlier, and Pending resolve dates are always requestable.
- **Owner swapping the worker before a deciding date.** Re-pins are timelocked, and a re-pin never touches a date that has started (this milestone).
- **SSRF and spoofed contracts in the readers.** The dashboard and MCP server fetch only allowlisted HTTPS origins. They rebuild attestation URLs from the numeric request id and refuse redirects. The dashboard reads only the official contract and requires a known-good worker hash, the pin for the date and a transaction to that contract before it shows "verified".

## Earlier deployments

- **`nearbooks-v6` on testnet (still live).** The same wasm and worker as mainnet, exercised end to end first: the keeper's key refusals, the rejection paths and the three demo predictions resolved one per outcome. Record: `contract/deployment-testnet.json`; `make verify-deploy-testnet` checks it against testnet.
- **`nearbooks-v5` (still live).** It runs the timelocked contract without the keeper budget, built with `cargo near build non-reproducible-wasm` (`b9343afe…6b18`). It keeps the re-pin timelock demo across 2026-10-21 00:00 UTC and its own copy of predictions 0-6. Its transactions are listed in the deployment table in `docs/deployment.md`, and its logs are `contract/testnet-attested.log` and `contract/testnet-resolve.log`. `make verify-deploy` checks that it still runs that code.
- **Superseded attested designs.** The first design (tx `FgnM1JhaTnuZUuuhDBRwZLKm2CxX5WZk6cC6Lix6Eo7t`), the canonical-block design before the storage fix (tx `D315fRtRDtk4FJfyLPfWUpTCvJVRfxdrwBZt5khcuX1Y`) and the untimelocked v4 contract (`nearbooks-v4…`, request `CmYRyYdp6ZNeoCLe3ecJDs4VD2aDGZvRr7N6r7edSjGe`) were all run end to end on testnet. Their logs are in git history: commits `770990b`, `474734d`, `290311b`. Those accounts were deleted and their balances returned to the funder. For v4 that was tx `BYQQAY8QnDRiPaLEXYQXFe7oTmdWRciytwJzLdwx7LQt`.
- **Reporter-only prototype.** `nbproto-owner-1696.testnet` (deploy `C3vKYWYEtFPXUgJhX7guN5n1gMFwnsSPr1WG3uDrwUDg`) showed a reporter account holding a function-call key limited to `submit_snapshot`:
  - the reporter's snapshot tx `Fnvem2YpsFJrf6d3vepf16nP38dVa1dQq58jd6aC5bHa` succeeded,
  - the owner's own `submit_snapshot` and the key's attempt at `resolve_prediction` were refused.

  Logs: `contract/testnet-run.log`, `contract/testnet-run-redeploy.log`. Reported snapshots never decide predictions in the current contract.
