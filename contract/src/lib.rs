//! NEAR Books oracle: dated metric snapshots plus a registry of falsifiable predictions.
//!
//! * One `reporter` account (intended to hold only a function-call access key limited to
//!   `submit_snapshot`) may append one snapshot per date, dates strictly increasing, never in the future.
//! * The `owner` registers predictions (claim, metric, comparator, threshold, resolve_after). Predictions can
//!   only be registered for a future `resolve_after`, and are never edited or deleted.
//! * Anyone can resolve a prediction once a snapshot dated on/after `resolve_after` exists. The FIRST such
//!   snapshot decides: if it reports the metric the prediction is Confirmed or Refuted, if it does not the
//!   prediction is Void (explicit, final). There is no scan window, so a reporter cannot skip a metric to let a
//!   later snapshot decide, and no prediction can become unresolvable. No deposits, stakes or payouts exist.
//!
//! What the contract does NOT do: it cannot verify that a reported number is true. It only guarantees who
//! reported it, when, that it cannot be rewritten, and that predictions are judged mechanically.
//! Metric values are signed integers in micro-units (value x 1e6) so no floats are needed on-chain.

use near_sdk::json_types::I128;
use near_sdk::store::Vector;
use near_sdk::{env, near, require, AccountId, PanicOnDefault};

pub const MAX_METRICS_PER_SNAPSHOT: usize = 32;
pub const MAX_KEY_LEN: usize = 64;
pub const MAX_CLAIM_LEN: usize = 280;

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
    /// UTC date `YYYY-MM-DD` the numbers describe (the pipeline's `as_of`).
    pub date: String,
    pub metrics: Vec<Metric>,
    /// Lowercase hex sha256 of the metrics.json the numbers came from (supplied by the reporter, not verified here).
    pub evidence_hash: String,
    pub submitted_by: AccountId,
    pub block_height: u64,
    pub block_timestamp_ms: u64,
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
    /// Date of the first snapshot on/after `resolve_after` (the deciding one).
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
    /// UTC date `YYYY-MM-DD`; the first snapshot dated on/after this decides.
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
    snapshots: Vector<Snapshot>,
    predictions: Vector<Prediction>,
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
        }
    }

    // ---------------------------------------------------------------- admin

    /// Owner only. Rotates the reporter (e.g. when its function-call key is replaced by another worker).
    pub fn set_reporter(&mut self, reporter_id: AccountId) {
        require!(env::predecessor_account_id() == self.owner_id, "only the owner can set the reporter");
        self.reporter_id = reporter_id;
    }

    // ------------------------------------------------------------ snapshots

    /// Reporter only. Appends the snapshot for `date`. Dates must strictly increase and may not be in the future.
    pub fn submit_snapshot(&mut self, date: String, metrics: Vec<Metric>, evidence_hash: String) {
        require!(env::predecessor_account_id() == self.reporter_id, "only the reporter can submit snapshots");
        require!(valid_date(&date), "date must be YYYY-MM-DD");
        require!(date <= today(), "snapshot date is in the future");
        if let Some(last) = self.snapshots.len().checked_sub(1).and_then(|i| self.snapshots.get(i)) {
            require!(date > last.date, "snapshot date must be later than the latest stored date");
        }
        require!(!metrics.is_empty() && metrics.len() <= MAX_METRICS_PER_SNAPSHOT, "1..=32 metrics required");
        for (i, m) in metrics.iter().enumerate() {
            require!(
                !m.key.is_empty() && m.key.len() <= MAX_KEY_LEN && m.key.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_'),
                "metric keys must be [a-z0-9_]{1,64}"
            );
            require!(!metrics[..i].iter().any(|o| o.key == m.key), "duplicate metric key");
        }
        require!(
            evidence_hash.len() == 64 && evidence_hash.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "evidence_hash must be 64 lowercase hex chars"
        );
        env::log_str(&format!("EVENT_JSON:{{\"standard\":\"nearbooks\",\"version\":\"1.0.0\",\"event\":\"snapshot\",\"data\":{{\"date\":\"{}\"}}}}", date));
        self.snapshots.push(Snapshot {
            date,
            metrics,
            evidence_hash,
            submitted_by: env::predecessor_account_id(),
            block_height: env::block_height(),
            block_timestamp_ms: env::block_timestamp_ms(),
        });
    }

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
        env::log_str(&format!("EVENT_JSON:{{\"standard\":\"nearbooks\",\"version\":\"1.0.0\",\"event\":\"prediction_added\",\"data\":{{\"id\":{}}}}}", id));
        id
    }

    /// Anyone. Settles the prediction against the FIRST snapshot dated on/after `resolve_after`. If that snapshot
    /// lacks the metric the prediction becomes Void; later snapshots are never consulted.
    pub fn resolve_prediction(&mut self, id: u64) -> Prediction {
        let idx = u32::try_from(id).unwrap_or(u32::MAX);
        let mut p = self.predictions.get(idx).cloned().unwrap_or_else(|| env::panic_str("no such prediction"));
        require!(p.status == Status::Pending, "prediction already resolved");
        let at = self.first_index_on_or_after(&p.resolve_after).unwrap_or_else(|| env::panic_str("no snapshot on/after resolve_after yet"));
        let snap = self.snapshots.get(at).unwrap_or_else(|| env::panic_str("snapshot index out of range"));
        let observed = snap.metrics.iter().find(|m| m.key == p.metric).map(|m| m.value);
        p.status = match observed {
            None => Status::Void,
            Some(v) => {
                let holds = match p.comparator {
                    Comparator::Gt => v.0 > p.threshold.0,
                    Comparator::Gte => v.0 >= p.threshold.0,
                    Comparator::Lt => v.0 < p.threshold.0,
                    Comparator::Lte => v.0 <= p.threshold.0,
                };
                if holds { Status::Confirmed } else { Status::Refuted }
            }
        };
        p.resolution = Some(Resolution { snapshot_date: snap.date.clone(), observed, resolved_by: env::predecessor_account_id(), block_height: env::block_height() });
        self.predictions.replace(idx, p.clone());
        env::log_str(&format!("EVENT_JSON:{{\"standard\":\"nearbooks\",\"version\":\"1.0.0\",\"event\":\"prediction_resolved\",\"data\":{{\"id\":{},\"status\":\"{}\"}}}}", id, match p.status { Status::Confirmed => "confirmed", Status::Refuted => "refuted", _ => "void" }));
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

    pub fn get_config(&self) -> (AccountId, AccountId) {
        (self.owner_id.clone(), self.reporter_id.clone())
    }
}

impl NearBooks {
    /// Binary search over the date-ordered snapshot vector: index of the first snapshot with date >= `date`.
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
