# How to check an attested NEAR Books snapshot

An **attested** snapshot holds NEAR mainnet numbers that a pinned WASM worker read inside an Intel TDX enclave run by OutLayer. The worker's output reached the `nearbooks` contract through OutLayer's on-chain yield/resume. No reporter key is involved. This page shows how anyone can check one, and it marks which steps were actually executed on 2026-10-20.

## What is claimed

For a UTC date D, the snapshot is mainnet state at **the first block of D**, block B with `prev.timestamp < D 00:00:00 UTC <= B.timestamp`. That state covers:

- total supply (block header),
- the balances of `fefundsadmin.sputnik-dao.near`, `buybacks.multisignature.near` and `1csfundsadmin.sputnik-dao.near`,
- an Intents reserves check: `intents.near mt_supply` against `ft_balance_of("intents.near")` for the top 8 tokens by value.

The chain of custody:

1. `request_snapshot(D, B)` (D not before `earliest_date`, unless a Pending prediction resolves on D) keeps a storage reserve from the deposit and sends the input `{"n":"<nonce>","d":"D","b":"B"}` to `outlayer.testnet` `request_execution`. It names the worker by its sha256 (pinned by the owner in `pin_worker`). The caller is named as `payer_account_id`.
2. An OutLayer TDX worker downloads the WASM and refuses it unless its sha256 equals the pin. The worker checks that B is the first block of D and refuses otherwise. It reads everything at B from the archival RPC and prints about 383 bytes of JSON.
3. OutLayer resumes the yielded promise with those bytes. `on_snapshot_result` accepts them only if all of these hold:
   - the call comes from the contract itself (the promise result of the configured OutLayer contract),
   - the pin and the OutLayer account did not change while the run was in flight,
   - the nonce, the date and the block match what was asked,
   - the timestamp falls on D,
   - every field parses strictly.

   It then stores the snapshot under D, with the sha256 of the exact bytes and the attestation URL. It keeps the bytes it staked out of the reserve and refunds the rest to the requester.
4. OutLayer publishes an attestation for the run. It contains the TDX quote, `wasm_hash`, `input_hash`, `output_hash`, the caller and the transaction. The quote's `report_data` commits to all of those fields.

## Live testnet deployment (run 2026-10-20, log: `contract/testnet-attested.log`)

| Item | Value |
|---|---|
| Contract | `nearbooks-v4.okf-kill-1792497882.testnet`, code sha256 `540d9e7481d0530cfc02e69fced233adf7a5bd58ef23570aaeef20b4def0b707` (= `make wasm`) |
| Pinned worker | sha256 `20102fb3000a8c78ac93389d8f5be759e18b2e34bce5a9f1686854d15abd1e80` (= `make worker`, identical when built from a different directory), FastFS `https://test.fastfs.io/okf-kill-1792497882.testnet/outlayer.testnet/20102fb3….wasm` |
| Deploy + init / `pin_worker` | `4E9iJrxbhqtci5jupCVZg4kdPxi2sEg5xNFmjZGMe98Q` / `2RGJqpXMAHe8y2gQPNmdvYHTmvMKJnuXaK18eQW7ZUhQ` |
| `request_snapshot("2026-10-20", 220726930)` with 0.03 NEAR, stored, OutLayer request 5137. Storage settled: 844 bytes kept (0.00844 NEAR), 0.01156 NEAR of the reserve refunded. The contract's free balance was 629366342860654700000000 yocto before and after. | `CmYRyYdp6ZNeoCLe3ecJDs4VD2aDGZvRr7N6r7edSjGe` |
| `set_earliest_date("2026-10-19")` (moving it forward again was refused) | `DzBN63Ce5oDG9y5Kvb4PfYk6irjCnFfSrw2tLkdoQQoZ` |
| Backfill `request_snapshot("2026-10-19", 220581913)`, stored, request 5138 (refused before the earliest_date move) | `4iXCYdwZ8TfVeC6Buw1bYodcj2M972f273b1gvgodGsd` |
| Wrong block (220726931): the worker refused, the contract returned `rejected: …`, nothing stored, the full 0.02 NEAR reserve refunded | `Kwnry9cjDk76NwMqQ83q2kGS4oBC5gHYvKakCZAnQtW` |
| Refused before any run: 0.01 NEAR (no storage reserve), a date that has not started, a second request for an already attested date, a date before earliest_date, a stranger calling `pin_worker`, a direct call to `on_snapshot_result` | panics in the log |
| Cost per run | OutLayer charged 0.0013 NEAR. Gas came to about 0.0011 NEAR. A stored snapshot's storage costs 0.0084 NEAR. Everything else of the 0.03 NEAR is refunded: by OutLayer (the run part) and by the contract (the unused reserve). |
| Resources per run | about 10.4M instructions and 3.1 s (limits: 200M and 60 s) |

## Checks, one command

```
python3 -I contract/scripts/verify_attestation.py nearbooks-v4.okf-kill-1792497882.testnet 2026-10-20 \
    --wasm worker/res/nearbooks-worker.wasm --dcap "$(command -v dcap-qvl)"   # cargo install dcap-qvl-cli
```

Executed on both stored snapshots, each giving `12/12 checks pass`:

| # | Check | Executed? |
|---|---|---|
| 1 | `attestation.wasm_hash` = `executed_wasm_sha256` = the snapshot's `wasm_hash` = sha256 of a local `make worker` build | yes |
| 2 | `attestation.input_hash` = sha256 of `{"n":"1","d":"2026-10-20","b":"220726930"}`, i.e. the run answered exactly this request | yes |
| 3 | `attestation.output_hash` = the snapshot's `output_sha256` (the contract hashed the bytes it stored) | yes |
| 4 | The OutLayer receipt of that transaction returned bytes with that hash, and the stored metrics are what those bytes say. The transaction called this contract's `request_snapshot`. | yes |
| 5 | The quote's `report_data[0:32]` = sha256 of the task fields (task type, id, build target, wasm/input/output hash, block, caller, timestamp, attached_usd; layout from OutLayer's `worker/src/tdx_attestation.rs`). So the quote is bound to this run, not just to some worker. | yes |
| 6 | The quote's MRTD and RTMR0-3 are one of the 108 approved sets in OutLayer's register contract `worker.outlayer.testnet` (`get_approved_measurements`) | yes |
| 7 | The Intel DCAP signature chain verifies with `dcap-qvl` 0.7.0: `Quote verified`, TCB status `UpToDate` | yes. Collateral came from Phala's PCCS (`pccs.phala.network`), not from Intel PCS directly. |
| 8 | B is the first block of D: `python3 -I worker/scripts/first_block.py 2026-10-20` prints `220726930` | yes |
| 9 | Evidence hash reproduces: running the pinned WASM locally (`wasmtime run -S http`) with the same input gave `sha` `02701f60…6dd5`, equal to the on-chain `evidence_hash`, because both runs were served by the archival endpoint with no fallback | yes, for 2026-10-20 |
| 10 | The approved measurements correspond to OutLayer's published open-source worker image (Phala Trust Center or a reproducible dstack image build) | **documented only**, not done |
| 11 | Only TEE-registered worker keys can resolve OutLayer executions on-chain (register contract → access keys scoped to `resolve_execution`) | **documented only**, read from OutLayer's source, not tested |

## What this does not prove

- **The RPC data itself.** The worker trusts `archival-rpc.mainnet.fastnear.com`, falling back to `rpc.mainnet.near.org`. It does not check state proofs. `sha` commits to the raw responses it saw. Check 9 shows those responses can be refetched byte for byte, but if a fallback answered (`fb` > 0) the bytes may differ.
- **On-chain quote verification.** The contract cannot verify the TDX quote. Its on-chain guarantee is weaker: the bytes came from the configured OutLayer contract for the pinned hash. Checks 5-7 are off-chain.
- **The token list.** It is fixed in the worker (the top 8 on 2026-10-20, `worker/scripts/rank_intents_tokens.py`). Changing it changes the hash and needs a re-pin.

## Trust assumptions: what the owner can still do

The owner is the contract account (`nearbooks-v4…`). Its powers, and what each one cannot do:

| Power | Effect | Limit / visibility |
|---|---|---|
| `pin_worker` (worker hash, URL, OutLayer contract, limits) | Decides which code produces future attested snapshots. A malicious pin could produce false numbers for dates not yet attested, including a prediction's deciding date. | Emits `worker_pinned` with the previous hash. Every snapshot stores the `wasm_hash` that produced it, and the verifier compares it with a local build of this repo. Runs in flight when the pin changes are rejected. Stored snapshots are immutable and are never touched by a re-pin (test `repin_does_not_touch_stored_snapshots_or_their_predictions`). **Remaining trust:** no timelock, so readers must check the hash of the snapshot that decided a prediction. |
| Pinning a broken worker or a wrong OutLayer account | Stops new snapshots (a liveness issue): predictions stay Pending, none is decided wrongly | visible on-chain |
| `set_earliest_date` | Can only move it earlier: it widens backfill and never shuts a date out | A Pending prediction's `resolve_after` is always requestable regardless (`pending_resolve_date_is_requestable_even_before_earliest_date`). Emits `earliest_date_set`. |
| `add_prediction` | Registers claims, only for a future `resolve_after` | Cannot edit, delete or resolve predictions |
| `set_reporter` | Rotates who may append reported snapshots | Reported snapshots never resolve predictions. Emits `reporter_set`. |
| Upgrading the contract code (full-access key on the account) | Could change any rule | Not prevented in this prototype. Verify the deployed `code_hash` against `make wasm`. Removing the account's full-access keys would lock the code. |

The owner cannot write or alter attested snapshots, cannot move funds of requesters (OutLayer refunds the run part; the contract only returns a request's own unused storage reserve), and cannot resolve a prediction except by the same public rule as anyone else.

## Design fixes after security reviews (2026-10-20)

- **Timing griefing.** The first version let anyone trigger a snapshot "now". Snapshots shared one date-ordered series with the reporter. So a caller could choose when the deciding first snapshot on or after a prediction's `resolve_after` was observed. Now:
  - The observation point is canonical: the first block of the requested day, which the worker enforces.
  - Attested snapshots are their own series, one per date. The first valid one wins. A missed day can be backfilled, since the result is the same whenever it is fetched.
  - Predictions resolve only from the attested snapshot for exactly `resolve_after`. The test `a_later_snapshot_cannot_stand_in_for_the_missing_deciding_one` covers this.
  - Reported snapshots stay in their own series and never decide a prediction. A missing metric still gives an explicit, final Void.
- **Double refund.** The first version transferred the deposit back to the caller from the contract's own balance when the OutLayer call failed. It assumed the deposit had returned to the contract, which the callback cannot prove. Now the contract never re-sends the OutLayer part. OutLayer refunds its unused part straight to the caller (`payer_account_id`) on success, failure and timeout. The only NEAR the callback sends is the unused part of that request's own storage reserve, which never left the contract. Tests: `failed_outlayer_call_stores_nothing_and_returns_only_the_reserve` and `callback_never_sends_more_than_the_requests_own_unused_reserve`. The one remaining case: if OutLayer's `request_execution` itself panics, NEAR returns the OutLayer part to this contract, and it stays there. The 0.01 NEAR OutLayer part is above OutLayer's 0.007 NEAR estimate for the configured limits, so that should not happen in normal use.
- **Storage-staking griefing.** Permissionless requests for any past date could have made the contract pay storage for thousands of backfilled snapshots. Now:
  - The requester pays. `request_snapshot` needs `min_request_deposit()`: 0.01 NEAR for OutLayer plus a 2,000-byte storage reserve (0.02 NEAR). The reserve stays in the contract.
  - The callback measures the bytes the request leaves behind (`storage_usage` with the pending entry freed) and refunds the unused reserve to the requester. A snapshot needing more than the reserve is undone.
  - Backfill is bounded by `earliest_date` (default: the deployment date).
  - Tests: `storage_is_paid_by_the_requester` (reserve minus refund equals the bytes staked, so the free balance is unchanged; on testnet the free balance was identical before and after), `deposit_without_the_storage_reserve_is_refused`, `date_before_earliest_date_is_refused`.
- **Owner blocking a prediction via earliest_date.** Now `set_earliest_date` only moves earlier (`earliest_date_cannot_move_forward`). Dates with Pending predictions are always requestable (`pending_resolve_date_is_requestable_even_before_earliest_date`).

Superseded designs were also run end to end on testnet (first design: tx `FgnM1JhaTnuZUuuhDBRwZLKm2CxX5WZk6cC6Lix6Eo7t`; canonical-block design before the storage fix: tx `D315fRtRDtk4FJfyLPfWUpTCvJVRfxdrwBZt5khcuX1Y`). Their logs are in git history (commits `770990b`, `474734d`). Those throwaway accounts were deleted afterwards and their balances returned to the funder.
