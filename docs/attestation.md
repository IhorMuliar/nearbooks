# How to check an attested NEAR Books snapshot

An **attested** snapshot holds NEAR mainnet numbers read by a pinned WASM worker inside an Intel TDX enclave run by OutLayer. The worker's output reached the `nearbooks` contract through OutLayer's on-chain yield/resume, so no reporter key is involved. This page shows how anyone can check a snapshot, and it marks which steps were actually executed. The deployment itself (contract, hashes, transactions) is listed in `docs/deployment.md`, generated from `contract/deployment.json`.

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
   - sends `{"n":"<nonce>","d":"D","b":"B","b28":"B28"}` to `outlayer.testnet` `request_execution`,
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
python3 -I contract/scripts/verify_attestation.py nearbooks-v6.okf-kill-1792497882.testnet 2026-10-20 \
    --wasm worker/res/nearbooks-worker.wasm --dcap "$(command -v dcap-qvl)"   # cargo install dcap-qvl-cli
```

Recorded output without `--wasm` and `--dcap` (network, about 20 s; long hashes shortened to their first and last characters (`bbed939a…7963`), the script prints them in full):

```
$ python3 -I contract/scripts/verify_attestation.py nearbooks-v6.okf-kill-1792497882.testnet 2026-10-20
snapshot 2026-10-20: nonce 0, OutLayer request 5240, mainnet block 220726930 (ts 1792454400548428142)
attestation: https://testnet-api.outlayer.ai/attestations/by-request/5240
PASS  snapshot's attestation link == the allowlisted URL for its request id
PASS  attestation.request_id == snapshot request_id  5240
PASS  attestation.wasm_hash == snapshot wasm_hash  bbed939a…7963
PASS  attestation.executed_wasm_sha256 == wasm_hash
PASS  snapshot wasm_hash == the contract's pin for that date  outlayer.testnet
PASS  wasm_hash is a worker this repo vouches for (contract/known_workers.json)  format 3 (28-day lookback), commit 422a4e6
INFO  current pin: bbed939a…7963 (same); waiting proposal: None
PASS  attestation.input_hash == sha256(contract input)  {"n":"0","d":"2026-10-20","b":"220726930","b28":"216696053"}
PASS  attestation.output_hash == snapshot output_sha256  99a4cf8c…7251
PASS  transaction calls this contract's request_snapshot or keeper_request_snapshot  BWso6e2mQR5Qmj8tCvxbF9J8SHkoqgVnbV3rWXLFfyi4 (keeper_request_snapshot)
PASS  OutLayer receipt returned bytes hashing to output_hash
PASS  stored metrics == attested output (all 13, incl. the 28-day ones recomputed here)  {"bb":"2022180087915366303354191622511",...}
INFO  TDX quote 5006 bytes, version 4, MRTD 7fe60787222bd394..., RTMR3 f6c4087e601b4300... (= worker_measurement)
PASS  quote report_data == sha256(task fields): the quote is bound to this run  ddecfc90…3bc2
PASS  quote MRTD+RTMR0-3 are in worker.outlayer.testnet approved measurements  (110 approved sets)
SKIP  Intel DCAP signature chain (pass --dcap /path/to/dcap-qvl)
13/13 checks pass
```

Append `--wasm worker/res/nearbooks-worker.wasm` (after `make worker`) to also compare your own build, and `--dcap "$(command -v dcap-qvl)"` (`cargo install dcap-qvl-cli`) for Intel's chain. With both, the run ends with `PASS  local worker build sha256 == wasm_hash`, `PASS  Intel DCAP signature chain verifies (dcap-qvl)  TCB status UpToDate` and `15/15 checks pass` (recorded for the same date on `nearbooks-v5`; on `nearbooks-v6` the `--wasm` run printed `14/14 checks pass`, `contract/testnet-keeper.log`). This snapshot was requested by the keeper (see "Keeper budget" below), so the transaction is `keeper_request_snapshot`, signed by its function-call key. The evidence hash `875a2965…7ec0` is the same as for the v5 run of that date.

The script never fetches a URL taken from contract state. It rebuilds the attestation URL from the allowlisted API base and the numeric request id, and it refuses redirects.

| # | Check | Executed? |
|---|---|---|
| 1 | `attestation.wasm_hash` = `executed_wasm_sha256` = the snapshot's `wasm_hash` = the contract's pin for that date = sha256 of a local `make worker` build | yes |
| 2 | that hash is listed in `contract/known_workers.json` (worker builds this repo vouches for) | yes |
| 3 | `attestation.input_hash` = sha256 of `{"n":"0","d":"2026-10-20","b":"220726930","b28":"216696053"}` (nonce 0 on v6, 1 on v5): the run answered exactly this request | yes |
| 4 | `attestation.output_hash` = the snapshot's `output_sha256` | yes |
| 5 | the attested transaction called this contract's `request_snapshot` or `keeper_request_snapshot`; the OutLayer receipt returned bytes with that hash; all 13 stored metrics equal what those bytes say (the 28-day ones recomputed independently) | yes |
| 6 | the quote's `report_data[0:32]` = sha256 of the task fields (layout from OutLayer's `worker/src/tdx_attestation.rs`): the quote is bound to this run | yes |
| 7 | the quote's MRTD and RTMR0-3 are one of the approved sets in `worker.outlayer.testnet` (`get_approved_measurements`) | yes |
| 8 | Intel DCAP signature chain verifies with `dcap-qvl` 0.7.0, TCB status `UpToDate` (collateral from Phala's PCCS) | yes, for the v5 run of 2026-10-20 (`dcap-qvl` is not installed where the v6 run was checked) |
| 9 | B and B28 are the first blocks of their days: `python3 -I worker/scripts/first_block.py 2026-10-20 --pair` prints `220726930 216696053` | yes |
| 10 | the evidence hash reproduces: a local `wasmtime run -S http` of the pinned WASM with the same input printed `sha` `875a2965…7ec0`, equal to the on-chain `evidence_hash` | yes, for 2026-10-20; the v6 keeper run (OutLayer request 5240) printed the same `sha` as the v5 run (request 5168) |
| 11 | the approved measurements correspond to OutLayer's published open-source worker image | **documented only** |
| 12 | only TEE-registered worker keys can resolve OutLayer executions on-chain | **documented only**, read from OutLayer's source |

The dashboard and `get_attested_snapshots(verify=true)` repeat checks 1-5 (without the local build) in the browser or in Python. They mark a snapshot verified only if all of them hold.

## Timelock on re-pinning

The pin (worker hash, worker URL, OutLayer contract, resource limits) is kept as an append-only history:
- `propose_pin` (owner) emits `pin_proposed` with `apply_after_ns` = now + `pin_delay_secs`. The delay is fixed at `new`: 3,600 s on testnet. A mainnet deployment would use several days.
- `apply_pin` (owner, refused before `apply_after_ns`) appends the entry with `from_ns` = the block time and emits `worker_pinned`.
- `cancel_pin_proposal` drops a waiting proposal. A new proposal replaces a waiting one and restarts the clock.
- The first pin applies at once, because no snapshot can exist before it.

**The pin for date D is the entry in force at D 00:00 UTC.** A re-pin applied at time A therefore governs only dates that start at or after A. Every date that started earlier keeps its old pin, even if its snapshot is requested after A. This covers a Pending prediction's resolve date inside the proposal window. So readers get `pin_delay_secs` of public notice before a new worker can observe any date, and a date's observer never changes once that date has begun.

On testnet (`nearbooks-v5`, which stays live for this) a re-pin was proposed on 2026-10-20 at 16:01 UTC. It only changes the time limit from 60 s to 90 s, so `get_pin_for_date` shows which entry governs which date. Applying it right away was refused. What happened after 2026-10-21 00:00 UTC is in `contract/testnet-resolve.log`:
<!-- TIMELOCK_NOTE -->

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

The owner is the contract account (`nearbooks-v6…`; on v5, `nearbooks-v5…`).

| Power | Effect | Limit / visibility |
|---|---|---|
| `propose_pin` / `apply_pin` / `cancel_pin_proposal` | Decides which worker observes future dates | Timelocked (above). Events `pin_proposed`, `worker_pinned`, `pin_proposal_cancelled`. Every snapshot records its `wasm_hash`. The repo publishes the hashes it vouches for in `contract/known_workers.json`. A broken pin only stops new snapshots (liveness): predictions stay Pending, none is decided wrongly. |
| `add_prediction` | Registers claims | Only for a future `resolve_after`. Cannot edit, delete or resolve them. |
| `set_earliest_date` | Widens backfill | Can only move earlier. A Pending prediction's date is always requestable. |
| `set_reporter` | Rotates the reporter | Reported snapshots never resolve predictions. |
| `set_keeper` | Names the one account that may spend the keeper budget, and its daily cap | Cap at most 1 NEAR per UTC day (contract rule). Event `keeper_set`. The owner cannot spend the budget itself. |
| `withdraw_keeper_budget` | Takes unspent budget back to the owner | Only up to the recorded budget. Event `keeper_budget_withdrawn`. |
| `transfer_ownership` | Hands every power above to another account, for example a multisig | Event `owner_transferred`. |
| **Code upgrade through the account's full-access key** | **Can change any rule, including the timelock** | **Not prevented in this prototype.** Check the deployed `code_hash` against the build (`make verify-deploy`). |

The owner cannot write or alter attested snapshots. It cannot move requesters' funds: OutLayer refunds the run part, and the contract only returns a request's own unused storage reserve. It cannot resolve a prediction except by the public rule.

**Mainnet plan (not done).** Deploy with a timelock of days. Then do one of two things:
- delete the contract account's full-access key after deploying, so the code can no longer change,
- or `transfer_ownership` to a multisig and keep upgrades behind it.

Nothing in this repository was deployed to mainnet.

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

Accounting is conservative. OutLayer refunds the unused part of its 0.01 NEAR with a plain transfer to the payer. That was 0.00862 NEAR on 2026-10-20. The contract cannot attribute a plain transfer, so the refund stays in its free balance and is not credited. A failed OutLayer call is not credited either, for the same reason as on the public path. The budget therefore never exceeds what the contract really holds for it.

The keeper account (`nearbooks-keeper…`) has two keys:
- a function-call key: receiver = this contract, methods `keeper_request_snapshot` and `resolve_prediction`, allowance 0.5 NEAR of gas,
- a full-access key, which stays offline with the deployer.

The function-call key is the only secret the scheduled workflow (`.github/workflows/keeper.yml`) gets. If it leaks, the attacker can spend at most:
- the daily cap, on snapshots of dates they choose,
- the remaining gas allowance.

The protocol refuses everything else before execution. `keeper/probe.py` showed this on testnet: `DepositWithFunctionCall`, `MethodNameMismatch`, `ReceiverMismatch` and `RequiresFullAccess`. Contract tests cover the cap, the keeper-only rule, the budget accounting and the refunds (`contract/src/tests.rs`, section "keeper budget").

## Reproducible contract build

The deployed contract (v6) is `cargo near build reproducible-wasm` output. `contract/Cargo.toml` pins the build image by digest (`sourcescan/cargo-near:0.21.1-rust-1.96.0@sha256:ccb22bb4…ee17`), the command (`cargo near build non-reproducible-wasm --locked --no-abi`) and the NEP-330 `repository`. cargo-near builds the committed tree, not the working copy. It embeds the repository URL and the commit in the wasm, readable on chain through `contract_source_metadata`. So the hash belongs to one commit, recorded as `source_commit` in `contract/deployment.json`.

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

- **`nearbooks-v5` (still live).** It runs the timelocked contract without the keeper budget, built with `cargo near build non-reproducible-wasm` (`b9343afe…6b18`). It keeps the re-pin timelock demo across 2026-10-21 00:00 UTC and its own copy of predictions 0-6. Its transactions are listed in the deployment table in `docs/deployment.md`, and its logs are `contract/testnet-attested.log` and `contract/testnet-resolve.log`. `make verify-deploy` checks that it still runs that code.
- **Superseded attested designs.** The first design (tx `FgnM1JhaTnuZUuuhDBRwZLKm2CxX5WZk6cC6Lix6Eo7t`), the canonical-block design before the storage fix (tx `D315fRtRDtk4FJfyLPfWUpTCvJVRfxdrwBZt5khcuX1Y`) and the untimelocked v4 contract (`nearbooks-v4…`, request `CmYRyYdp6ZNeoCLe3ecJDs4VD2aDGZvRr7N6r7edSjGe`) were all run end to end on testnet. Their logs are in git history: commits `770990b`, `474734d`, `290311b`. Those accounts were deleted and their balances returned to the funder. For v4 that was tx `BYQQAY8QnDRiPaLEXYQXFe7oTmdWRciytwJzLdwx7LQt`.
- **Reporter-only prototype.** `nbproto-owner-1696.testnet` (deploy `C3vKYWYEtFPXUgJhX7guN5n1gMFwnsSPr1WG3uDrwUDg`) showed a reporter account holding a function-call key limited to `submit_snapshot`:
  - the reporter's snapshot tx `Fnvem2YpsFJrf6d3vepf16nP38dVa1dQq58jd6aC5bHa` succeeded,
  - the owner's own `submit_snapshot` and the key's attempt at `resolve_prediction` were refused.

  Logs: `contract/testnet-run.log`, `contract/testnet-run-redeploy.log`. Reported snapshots never decide predictions in the current contract.
