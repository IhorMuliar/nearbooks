//! NEAR Books oracle: dated metric snapshots plus a registry of falsifiable predictions.
//!
//! Two snapshot series, kept apart:
//! * **Attested** (decides predictions). Anyone may call the payable `request_snapshot(date, block_height)`. The
//!   contract asks OutLayer (`request_execution` on the configured OutLayer contract) to run the owner-PINNED worker
//!   WASM (sha256 in config) inside an Intel TDX enclave. The worker checks that `block_height` is the FIRST NEAR
//!   mainnet block of `date` (UTC) and reads every number at that block, so the observation point is canonical:
//!   whoever triggers it and whenever, the snapshot for a date is the same. OutLayer returns the worker's stdout
//!   through NEAR yield/resume to `on_snapshot_result`, which stores it tagged `Attested` (nonce, OutLayer request
//!   id, pinned wasm hash, mainnet block, sha256 of the exact output bytes, link to the public attestation).
//!   One attested snapshot per date: the first valid one wins, later ones for that date are rejected. Dates may
//!   arrive in any order (a missed day can be backfilled; it is canonical anyway).
//! * **Reported** (informational). One `reporter` account (intended to hold only a function-call access key limited
//!   to `submit_snapshot`) may append one snapshot per date, dates strictly increasing, never in the future. These
//!   carry metrics the worker does not compute yet (revenue, issuance) and never resolve a prediction.
//!
//! Predictions: the `owner` registers them (claim, metric, comparator, threshold, resolve_after) only for a future
//! `resolve_after`; they are never edited or deleted. Anyone can resolve one once the ATTESTED snapshot for
//! `resolve_after` exists, i.e. the state at the first block on/after `resolve_after` 00:00 UTC. That snapshot
//! decides: if it has the metric the prediction is Confirmed or Refuted, if not it is Void (explicit, final).
//! Nobody can choose when the deciding observation is taken or skip it. No deposits, stakes or payouts exist.
//!
//! What the contract does NOT do: it cannot verify a reported number, and it cannot verify the TDX quote on-chain.
//! For attested snapshots it guarantees that the bytes came back from the configured OutLayer contract for a run of
//! the pinned wasm hash, answer this request's nonce, date and block, and parse strictly; the quote itself is checked
//! off-chain (docs/attestation.md). The contract never pays refunds out of its own balance.
//! Metric values are signed integers in micro-units (value x 1e6) so no floats are needed on-chain.

use near_sdk::json_types::{I128, U64};
use near_sdk::serde::Deserialize;
use near_sdk::serde_json::{self, json, Value};
use near_sdk::store::{LookupMap, Vector};
use near_sdk::{env, ext_contract, near, require, AccountId, Gas, NearToken, PanicOnDefault, Promise, PromiseError};

pub const MAX_METRICS_PER_SNAPSHOT: usize = 32;
pub const MAX_KEY_LEN: usize = 64;
pub const MAX_CLAIM_LEN: usize = 280;

/// OutLayer's estimate for the default limits below is about 0.007 NEAR (a real run cost about 0.0015); OutLayer
/// refunds the unused part straight to the caller (`payer_account_id`).
pub const MIN_REQUEST_DEPOSIT: NearToken = NearToken::from_millinear(10);
pub const MIN_REQUEST_GAS: Gas = Gas::from_tgas(250);
pub const OUTLAYER_GAS: Gas = Gas::from_tgas(150);
pub const CALLBACK_GAS: Gas = Gas::from_tgas(40);
/// The worker prints about 390 bytes; OutLayer's on-chain output cap is about 900. Anything longer is rejected.
pub const MAX_WORKER_OUTPUT: usize = 1024;
/// Measured on OutLayer testnet: about 7M instructions and 4 s per run. Time gets the larger margin because the
/// archival RPC rate-limits and the worker backs off.
pub const DEFAULT_MAX_INSTRUCTIONS: u64 = 200_000_000;
pub const DEFAULT_MAX_SECONDS: u64 = 60;
pub const WORKER_FORMAT: &str = "2";
const YOCTO_PER_MICRO_NEAR: u128 = 1_000_000_000_000_000_000;
const MICRO: i128 = 1_000_000;

/// The subset of OutLayer's `request_execution` this contract uses (see app.outlayer.ai/docs/near-integration).
#[ext_contract(ext_outlayer)]
pub trait OutLayer {
    fn request_execution(
        &mut self,
        source: Value,
        resource_limits: Value,
        input_data: Option<String>,
        secrets_ref: Option<Value>,
        response_format: Option<String>,
        payer_account_id: Option<AccountId>,
        params: Option<Value>,
    );
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug, PartialEq)]
pub struct Metric {
    pub key: String,
    /// Value x 1e6, signed.
    pub value: I128,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug)]
pub struct Snapshot {
    /// UTC date `YYYY-MM-DD` the numbers describe.
    pub date: String,
    pub metrics: Vec<Metric>,
    /// Lowercase hex sha256 of the evidence: for `Reported` the metrics.json the numbers came from (not verified
    /// here); for `Attested` the worker's hash of the raw RPC responses it read.
    pub evidence_hash: String,
    /// The reporter for `Reported`; the account that paid for the run for `Attested`.
    pub submitted_by: AccountId,
    pub block_height: u64,
    pub block_timestamp_ms: u64,
    pub source: Source,
}

/// Where a snapshot's numbers came from.
#[near(serializers = [json, borsh])]
#[derive(Clone, Debug, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Source {
    /// Pushed by the reporter account (`submit_snapshot`). Trust = the reporter.
    Reported,
    /// Computed by the pinned worker inside OutLayer's TEE and delivered by the OutLayer contract.
    Attested {
        /// This contract's request nonce. The worker's input was `worker_input(nonce, date, block_height)`, whose
        /// sha256 is the attestation's `input_hash`.
        nonce: u64,
        /// OutLayer request id (the worker's `NEAR_REQUEST_ID`).
        request_id: u64,
        /// sha256 of the worker WASM that was pinned when the run was requested.
        wasm_hash: String,
        /// NEAR mainnet block every number was read at: the first block of `date` (UTC).
        block_height: u64,
        /// Timestamp of that block (ns).
        block_timestamp_ns: U64,
        /// sha256 of the exact output bytes OutLayer returned; must equal the attestation's `output_hash`.
        output_sha256: String,
        /// Public attestation record (TDX quote, worker measurement, wasm_hash, input_hash, output_hash).
        attestation: String,
    },
}

/// OutLayer settings and the pinned worker. Only the owner changes it (`pin_worker`, emits `worker_pinned`).
#[near(serializers = [json, borsh])]
#[derive(Clone, Debug, PartialEq)]
pub struct OutlayerConfig {
    /// `outlayer.testnet` / `outlayer.near`.
    pub outlayer_id: AccountId,
    /// Where OutLayer downloads the worker (FastFS); OutLayer refuses bytes whose sha256 is not `wasm_hash`.
    pub wasm_url: String,
    /// Lowercase hex sha256 of the worker WASM (`worker/build.sh` prints it).
    pub wasm_hash: String,
    /// Base URL of the public attestation API, e.g. `https://testnet-api.outlayer.ai`.
    pub attestation_api: String,
    pub max_instructions: u64,
    pub max_execution_seconds: u64,
}

/// A run in flight, keyed by nonce. Removed by the callback whatever the outcome.
#[near(serializers = [json, borsh])]
#[derive(Clone, Debug)]
pub struct PendingRequest {
    pub requester: AccountId,
    pub outlayer_id: AccountId,
    pub wasm_hash: String,
    /// The UTC date asked for and the claimed first mainnet block of that date.
    pub date: String,
    pub mainnet_block_height: u64,
    /// Block (this chain) the request was made in.
    pub block_height: u64,
}

/// What the pinned worker prints (worker/src/lib.rs). Every value is a string; unknown keys are refused.
#[derive(Deserialize)]
#[serde(crate = "near_sdk::serde", deny_unknown_fields)]
struct WorkerOutput {
    bb: String,
    cs: String,
    d: String,
    fb: String,
    fe: String,
    h: String,
    ms: String,
    n: String,
    rc: String,
    rd: String,
    rid: String,
    rk: String,
    rm: String,
    rt: String,
    sha: String,
    sup: String,
    t: String,
    v: String,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Copy, Debug, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Comparator {
    Gt,
    Gte,
    Lt,
    Lte,
}

impl Comparator {
    pub fn holds(self, observed: i128, threshold: i128) -> bool {
        match self {
            Comparator::Gt => observed > threshold,
            Comparator::Gte => observed >= threshold,
            Comparator::Lt => observed < threshold,
            Comparator::Lte => observed <= threshold,
        }
    }
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Copy, Debug, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Pending,
    Confirmed,
    Refuted,
    /// The deciding snapshot did not report the metric. Final.
    Void,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug)]
pub struct Resolution {
    /// Date of the deciding attested snapshot (always `resolve_after`).
    pub snapshot_date: String,
    /// `None` when that snapshot did not report the metric (status Void).
    pub observed: Option<I128>,
    pub resolved_by: AccountId,
    pub block_height: u64,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug)]
pub struct Prediction {
    pub id: u64,
    pub claim: String,
    pub metric: String,
    pub comparator: Comparator,
    /// Value x 1e6, signed.
    pub threshold: I128,
    /// UTC date `YYYY-MM-DD`; the attested snapshot for this date (state at its first mainnet block) decides.
    pub resolve_after: String,
    pub created_date: String,
    pub status: Status,
    pub resolution: Option<Resolution>,
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct NearBooks {
    owner_id: AccountId,
    reporter_id: AccountId,
    /// Reported series, strictly increasing dates.
    snapshots: Vector<Snapshot>,
    predictions: Vector<Prediction>,
    outlayer: Option<OutlayerConfig>,
    pending: LookupMap<u64, PendingRequest>,
    next_nonce: u64,
    /// Attested series: one per date, any arrival order.
    attested: LookupMap<String, Snapshot>,
    /// Dates of `attested` in arrival order (for listing).
    attested_dates: Vector<String>,
}

#[near]
impl NearBooks {
    #[init]
    pub fn new(owner_id: AccountId, reporter_id: AccountId) -> Self {
        Self {
            owner_id,
            reporter_id,
            snapshots: Vector::new(b"s"),
            predictions: Vector::new(b"p"),
            outlayer: None,
            pending: LookupMap::new(b"q"),
            next_nonce: 0,
            attested: LookupMap::new(b"a"),
            attested_dates: Vector::new(b"d"),
        }
    }

    // ---------------------------------------------------------------- admin

    /// Owner only. Rotates the reporter (e.g. when its function-call key is replaced by another worker).
    pub fn set_reporter(&mut self, reporter_id: AccountId) {
        require!(env::predecessor_account_id() == self.owner_id, "only the owner can set the reporter");
        self.reporter_id = reporter_id;
    }

    /// Owner only. Sets the OutLayer contract and pins the worker WASM by sha256. Runs requested before a re-pin are
    /// rejected when they come back (the callback compares against the pin at answer time).
    pub fn pin_worker(
        &mut self,
        outlayer_id: AccountId,
        wasm_url: String,
        wasm_hash: String,
        attestation_api: String,
        max_instructions: Option<u64>,
        max_execution_seconds: Option<u64>,
    ) {
        require!(env::predecessor_account_id() == self.owner_id, "only the owner can pin the worker");
        require!(is_hex64(&wasm_hash), "wasm_hash must be 64 lowercase hex chars");
        require!(wasm_url.starts_with("https://") && wasm_url.len() <= 512, "wasm_url must be an https URL");
        require!(
            attestation_api.starts_with("https://") && attestation_api.len() <= 128 && !attestation_api.ends_with('/'),
            "attestation_api must be an https base URL without a trailing slash"
        );
        let previous = self.outlayer.as_ref().map(|c| c.wasm_hash.clone());
        let cfg = OutlayerConfig {
            outlayer_id,
            wasm_url,
            wasm_hash,
            attestation_api,
            max_instructions: max_instructions.unwrap_or(DEFAULT_MAX_INSTRUCTIONS),
            max_execution_seconds: max_execution_seconds.unwrap_or(DEFAULT_MAX_SECONDS),
        };
        emit("worker_pinned", json!({"outlayer_id": cfg.outlayer_id, "wasm_hash": cfg.wasm_hash, "previous_wasm_hash": previous, "wasm_url": cfg.wasm_url}));
        self.outlayer = Some(cfg);
    }

    pub fn get_outlayer_config(&self) -> Option<OutlayerConfig> {
        self.outlayer.clone()
    }

    pub fn get_config(&self) -> (AccountId, AccountId) {
        (self.owner_id.clone(), self.reporter_id.clone())
    }

    // ------------------------------------------------------ attested (OutLayer)

    /// Anyone. Asks for the attested snapshot of `date` (UTC), observed at `block_height`, which must be the first
    /// NEAR mainnet block of that date (`worker/scripts/first_block.py` finds it; the worker refuses any other).
    /// Attach >= 0.01 NEAR and >= 250 Tgas. The whole deposit goes to OutLayer with the caller as
    /// `payer_account_id`, so OutLayer refunds the unused part to the caller. The result lands in
    /// `on_snapshot_result`; the transaction's final value says whether it was stored.
    #[payable]
    pub fn request_snapshot(&mut self, date: String, block_height: u64) -> Promise {
        let cfg = self.outlayer.clone().unwrap_or_else(|| env::panic_str("the OutLayer worker is not configured"));
        require!(valid_date(&date), "date must be YYYY-MM-DD");
        require!(date <= today(), "that date has not started yet");
        require!(!self.attested.contains_key(&date), "an attested snapshot for that date already exists");
        require!(block_height > 0, "block_height required");
        let deposit = env::attached_deposit();
        require!(deposit >= MIN_REQUEST_DEPOSIT, "attach at least 0.01 NEAR for the OutLayer run (the unused part is refunded)");
        require!(env::prepaid_gas() >= MIN_REQUEST_GAS, "attach at least 250 Tgas");
        let requester = env::predecessor_account_id();
        let nonce = self.next_nonce;
        self.next_nonce += 1;
        let input = worker_input(nonce, &date, block_height);
        self.pending.insert(
            nonce,
            PendingRequest {
                requester: requester.clone(),
                outlayer_id: cfg.outlayer_id.clone(),
                wasm_hash: cfg.wasm_hash.clone(),
                date: date.clone(),
                mainnet_block_height: block_height,
                block_height: env::block_height(),
            },
        );
        emit("snapshot_requested", json!({"nonce": nonce, "date": date, "mainnet_block_height": block_height, "wasm_hash": cfg.wasm_hash, "requester": requester, "deposit": deposit}));
        ext_outlayer::ext(cfg.outlayer_id.clone())
            .with_attached_deposit(deposit)
            .with_static_gas(OUTLAYER_GAS)
            .with_unused_gas_weight(1)
            .request_execution(
                json!({"WasmUrl": {"url": cfg.wasm_url, "hash": cfg.wasm_hash, "build_target": "wasm32-wasip2"}}),
                json!({"max_instructions": cfg.max_instructions, "max_memory_mb": 128, "max_execution_seconds": cfg.max_execution_seconds}),
                Some(input),
                None,
                Some("Json".into()),
                Some(requester),
                None,
            )
            .then(Self::ext(env::current_account_id()).with_static_gas(CALLBACK_GAS).with_unused_gas_weight(0).on_snapshot_result(nonce))
    }

    /// Callback of `request_snapshot` (private: only this contract can invoke it, and the promise result it reads is
    /// the one returned by the OutLayer contract the request was sent to). Never panics after the pending entry is
    /// removed, so a rejected or failed run leaves no trace except the event; returns what happened.
    /// It never sends NEAR: refunds are OutLayer's (to `payer_account_id`).
    #[private]
    pub fn on_snapshot_result(&mut self, nonce: u64) -> String {
        // Same check `#[private]` generates for the wasm export, repeated so it also holds in unit tests.
        require!(env::predecessor_account_id() == env::current_account_id(), "only this contract can deliver OutLayer results");
        let p = self.pending.remove(&nonce).unwrap_or_else(|| env::panic_str("unknown request nonce"));
        let outcome = match env::promise_result_checked(0, MAX_WORKER_OUTPUT) {
            // request_execution itself was refused, so no run happened. The protocol returns the attached deposit to
            // this contract; how much arrived cannot be proven here, so nothing is sent from the contract's balance.
            Err(PromiseError::Failed) => Err("the OutLayer call failed before running; nothing stored".to_string()),
            Err(PromiseError::TooLong(n)) => Err(format!("worker output too long ({} bytes)", n)),
            Err(_) => Err("unreadable OutLayer result".to_string()),
            Ok(bytes) => self.accept_attested(nonce, &p, &bytes),
        };
        match outcome {
            Ok(date) => {
                emit("snapshot", json!({"date": date, "source": "attested", "nonce": nonce}));
                format!("stored attested snapshot {}", date)
            }
            Err(reason) => {
                emit("snapshot_rejected", json!({"nonce": nonce, "date": p.date, "reason": reason}));
                format!("rejected: {}", reason)
            }
        }
    }

    pub fn get_pending(&self, nonce: u64) -> Option<PendingRequest> {
        self.pending.get(&nonce).cloned()
    }

    pub fn attested_count(&self) -> u32 {
        self.attested_dates.len()
    }

    pub fn get_attested_snapshot(&self, date: String) -> Option<Snapshot> {
        self.attested.get(&date).cloned()
    }

    /// Attested snapshots in arrival order.
    pub fn list_attested_snapshots(&self, from_index: Option<u32>, limit: Option<u32>) -> Vec<Snapshot> {
        let from = from_index.unwrap_or(0);
        let limit = limit.unwrap_or(30).min(100);
        self.attested_dates.iter().skip(from as usize).take(limit as usize).filter_map(|d| self.attested.get(d).cloned()).collect()
    }

    // ------------------------------------------------------ reported series

    /// Reporter only. Appends the reported snapshot for `date`. Dates must strictly increase and may not be in the
    /// future. Reported snapshots never resolve predictions.
    pub fn submit_snapshot(&mut self, date: String, metrics: Vec<Metric>, evidence_hash: String) {
        require!(env::predecessor_account_id() == self.reporter_id, "only the reporter can submit snapshots");
        if let Err(e) = check_snapshot(&date, &metrics, &evidence_hash) {
            env::panic_str(e);
        }
        if let Some(last) = self.snapshots.len().checked_sub(1).and_then(|i| self.snapshots.get(i)) {
            require!(date > last.date, "snapshot date must be later than the latest stored date");
        }
        emit("snapshot", json!({"date": date, "source": "reported"}));
        self.snapshots.push(Snapshot {
            date,
            metrics,
            evidence_hash,
            submitted_by: env::predecessor_account_id(),
            block_height: env::block_height(),
            block_timestamp_ms: env::block_timestamp_ms(),
            source: Source::Reported,
        });
    }

    /// Reported series only (see `attested_count` for the other).
    pub fn snapshot_count(&self) -> u32 {
        self.snapshots.len()
    }

    pub fn latest_snapshot(&self) -> Option<Snapshot> {
        self.snapshots.len().checked_sub(1).and_then(|i| self.snapshots.get(i)).cloned()
    }

    pub fn get_snapshot(&self, date: String) -> Option<Snapshot> {
        let i = self.first_index_on_or_after(&date)?;
        self.snapshots.get(i).filter(|s| s.date == date).cloned()
    }

    pub fn list_snapshots(&self, from_index: Option<u32>, limit: Option<u32>) -> Vec<Snapshot> {
        let from = from_index.unwrap_or(0);
        let limit = limit.unwrap_or(30).min(100);
        self.snapshots.iter().skip(from as usize).take(limit as usize).cloned().collect()
    }

    // ---------------------------------------------------------- predictions

    /// Owner only. `resolve_after` must be strictly after today (block date), so a prediction can never be
    /// registered once its answer is already knowable.
    pub fn add_prediction(&mut self, claim: String, metric: String, comparator: Comparator, threshold: I128, resolve_after: String) -> u64 {
        require!(env::predecessor_account_id() == self.owner_id, "only the owner can add predictions");
        require!(!claim.is_empty() && claim.len() <= MAX_CLAIM_LEN, "claim must be 1..=280 bytes");
        require!(!metric.is_empty() && metric.len() <= MAX_KEY_LEN, "metric key required");
        require!(valid_date(&resolve_after), "resolve_after must be YYYY-MM-DD");
        let created = today();
        require!(resolve_after > created, "resolve_after must be in the future");
        let id = self.predictions.len() as u64;
        self.predictions.push(Prediction {
            id,
            claim,
            metric,
            comparator,
            threshold,
            resolve_after,
            created_date: created,
            status: Status::Pending,
            resolution: None,
        });
        emit("prediction_added", json!({"id": id}));
        id
    }

    /// Anyone. Settles the prediction against the ATTESTED snapshot for `resolve_after` (mainnet state at the first
    /// block on/after `resolve_after` 00:00 UTC). If it lacks the metric the prediction becomes Void. No other
    /// snapshot, attested or reported, is ever consulted.
    pub fn resolve_prediction(&mut self, id: u64) -> Prediction {
        let idx = u32::try_from(id).unwrap_or(u32::MAX);
        let mut p = self.predictions.get(idx).cloned().unwrap_or_else(|| env::panic_str("no such prediction"));
        require!(p.status == Status::Pending, "prediction already resolved");
        let snap = self
            .attested
            .get(&p.resolve_after)
            .unwrap_or_else(|| env::panic_str("no attested snapshot for resolve_after yet (anyone can request it)"));
        let observed = snap.metrics.iter().find(|m| m.key == p.metric).map(|m| m.value);
        p.status = match observed {
            None => Status::Void,
            Some(v) if p.comparator.holds(v.0, p.threshold.0) => Status::Confirmed,
            Some(_) => Status::Refuted,
        };
        p.resolution = Some(Resolution { snapshot_date: snap.date.clone(), observed, resolved_by: env::predecessor_account_id(), block_height: env::block_height() });
        self.predictions.replace(idx, p.clone());
        emit("prediction_resolved", json!({"id": id, "status": p.status}));
        p
    }

    pub fn get_prediction(&self, id: u64) -> Option<Prediction> {
        self.predictions.get(u32::try_from(id).ok()?).cloned()
    }

    pub fn list_predictions(&self, from_index: Option<u32>, limit: Option<u32>) -> Vec<Prediction> {
        let from = from_index.unwrap_or(0);
        let limit = limit.unwrap_or(30).min(100);
        self.predictions.iter().skip(from as usize).take(limit as usize).cloned().collect()
    }
}

impl NearBooks {
    /// Validates the worker output for request `nonce` and stores it. Err = nothing stored.
    fn accept_attested(&mut self, nonce: u64, p: &PendingRequest, bytes: &[u8]) -> Result<String, String> {
        let cfg = self.outlayer.clone().ok_or("the OutLayer worker is no longer configured")?;
        if p.outlayer_id != cfg.outlayer_id {
            return Err("the configured OutLayer contract changed while the run was in flight".into());
        }
        if p.wasm_hash != cfg.wasm_hash {
            return Err("the pinned wasm hash changed while the run was in flight".into());
        }
        if bytes == b"null" {
            return Err("OutLayer reported the run as failed (it refunds the unused deposit to the requester)".into());
        }
        let o: WorkerOutput = serde_json::from_slice(bytes).map_err(|e| format!("malformed worker output: {}", e))?;
        if o.v != WORKER_FORMAT {
            return Err("unsupported worker output version".into());
        }
        if o.n != nonce.to_string() {
            return Err("worker output answers a different request (nonce mismatch)".into());
        }
        if o.d != p.date {
            return Err("worker output is for a different date".into());
        }
        let height = dec_u64(&o.h, "h")?;
        if height != p.mainnet_block_height {
            return Err("worker output is for a different block".into());
        }
        let ts_ns = dec_u64(&o.t, "t")?;
        if date_from_unix_secs(ts_ns / 1_000_000_000) != p.date {
            return Err("block timestamp is not on the requested date".into());
        }
        let request_id = dec_u64(&o.rid, "rid")?;
        let supply = dec_u128(&o.sup, "sup")?;
        let (fe, bb, cs) = (dec_u128(&o.fe, "fe")?, dec_u128(&o.bb, "bb")?, dec_u128(&o.cs, "cs")?);
        let (rk, rc, rd, rm) = (dec_u64(&o.rk, "rk")?, dec_u64(&o.rc, "rc")?, dec_u64(&o.rd, "rd")?, dec_u64(&o.rm, "rm")?);
        dec_u64(&o.fb, "fb")?;
        dec_u64(&o.ms, "ms")?;
        if rc > rk || rd > rc || o.rt.len() > 16 {
            return Err("inconsistent reserves fields".into());
        }
        if supply == 0 {
            return Err("total supply is zero".into());
        }
        let micro_near = |y: u128| I128((y / YOCTO_PER_MICRO_NEAR) as i128);
        let count = |c: u64| I128(c as i128 * MICRO);
        let metrics = vec![
            Metric { key: "supply_total_near".into(), value: micro_near(supply) },
            Metric { key: "buyback_wallet_near".into(), value: micro_near(bb) },
            Metric { key: "fefundsadmin_wallet_near".into(), value: micro_near(fe) },
            Metric { key: "onecsfundsadmin_wallet_near".into(), value: micro_near(cs) },
            Metric { key: "intents_reserves_tokens_checked".into(), value: count(rc) },
            Metric { key: "intents_reserves_tokens_in_deficit".into(), value: count(rd) },
            Metric { key: "intents_reserves_max_deficit_ppm".into(), value: count(rm) },
            Metric { key: "mainnet_block_height".into(), value: count(height) },
        ];
        check_snapshot(&p.date, &metrics, &o.sha).map_err(String::from)?;
        if self.attested.contains_key(&p.date) {
            return Err("an attested snapshot for that date already exists".into());
        }
        self.attested_dates.push(p.date.clone());
        self.attested.insert(
            p.date.clone(),
            Snapshot {
                date: p.date.clone(),
                metrics,
                evidence_hash: o.sha,
                submitted_by: p.requester.clone(),
                block_height: env::block_height(),
                block_timestamp_ms: env::block_timestamp_ms(),
                source: Source::Attested {
                    nonce,
                    request_id,
                    wasm_hash: p.wasm_hash.clone(),
                    block_height: height,
                    block_timestamp_ns: U64(ts_ns),
                    output_sha256: hex(&env::sha256(bytes)),
                    attestation: format!("{}/attestations/by-request/{}", cfg.attestation_api, request_id),
                },
            },
        );
        Ok(p.date.clone())
    }

    /// Binary search over the date-ordered reported vector: index of the first snapshot with date >= `date`.
    fn first_index_on_or_after(&self, date: &str) -> Option<u32> {
        let (mut lo, mut hi) = (0u32, self.snapshots.len());
        while lo < hi {
            let mid = lo + (hi - lo) / 2;
            if self.snapshots.get(mid)?.date.as_str() < date {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }
        (lo < self.snapshots.len()).then_some(lo)
    }
}

/// Shared by both series: date format, not in the future, metric keys, evidence hash.
fn check_snapshot(date: &str, metrics: &[Metric], evidence_hash: &str) -> Result<(), &'static str> {
    if !valid_date(date) {
        return Err("date must be YYYY-MM-DD");
    }
    if date > today().as_str() {
        return Err("snapshot date is in the future");
    }
    if metrics.is_empty() || metrics.len() > MAX_METRICS_PER_SNAPSHOT {
        return Err("1..=32 metrics required");
    }
    for (i, m) in metrics.iter().enumerate() {
        if m.key.is_empty() || m.key.len() > MAX_KEY_LEN || !m.key.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_') {
            return Err("metric keys must be [a-z0-9_]{1,64}");
        }
        if metrics[..i].iter().any(|o| o.key == m.key) {
            return Err("duplicate metric key");
        }
    }
    if !is_hex64(evidence_hash) {
        return Err("evidence_hash must be 64 lowercase hex chars");
    }
    Ok(())
}

/// Input the worker receives. The nonce is echoed back so an answer cannot be replayed onto another request; date
/// and block fix the observation point. Its sha256 is the attestation's `input_hash`.
pub fn worker_input(nonce: u64, date: &str, block_height: u64) -> String {
    format!("{{\"n\":\"{}\",\"d\":\"{}\",\"b\":\"{}\"}}", nonce, date, block_height)
}

fn emit(event: &str, data: Value) {
    env::log_str(&format!("EVENT_JSON:{}", json!({"standard": "nearbooks", "version": "1.1.0", "event": event, "data": data})));
}

pub fn is_hex64(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{:02x}", x)).collect()
}

fn dec_u64(s: &str, what: &str) -> Result<u64, String> {
    dec_u128(s, what).and_then(|v| u64::try_from(v).map_err(|_| format!("{} out of range", what)))
}

fn dec_u128(s: &str, what: &str) -> Result<u128, String> {
    if s.is_empty() || s.len() > 39 || !s.bytes().all(|b| b.is_ascii_digit()) {
        return Err(format!("{} must be a decimal string", what));
    }
    s.parse().map_err(|_| format!("{} out of range", what))
}

/// Strict `YYYY-MM-DD` with a real calendar day.
pub fn valid_date(s: &str) -> bool {
    let b = s.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' {
        return false;
    }
    let num = |r: std::ops::Range<usize>| s.get(r).filter(|t| t.bytes().all(|c| c.is_ascii_digit())).and_then(|t| t.parse::<u32>().ok());
    let (Some(y), Some(m), Some(d)) = (num(0..4), num(5..7), num(8..10)) else { return false };
    let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    let dim = match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => if leap { 29 } else { 28 },
        _ => return false,
    };
    y >= 2000 && d >= 1 && d <= dim
}

/// UTC date of the current block as `YYYY-MM-DD`.
pub fn today() -> String {
    date_from_unix_secs(env::block_timestamp() / 1_000_000_000)
}

/// Civil-from-days (Howard Hinnant's algorithm).
pub fn date_from_unix_secs(secs: u64) -> String {
    let z = (secs / 86_400) as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{:04}-{:02}-{:02}", y, m, d)
}

#[cfg(test)]
mod tests;
