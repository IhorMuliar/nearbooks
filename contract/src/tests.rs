use super::*;
use near_sdk::test_utils::VMContextBuilder;
use near_sdk::testing_env;

const OWNER: &str = "owner.testnet";
const REPORTER: &str = "reporter.testnet";
const STRANGER: &str = "stranger.testnet";
const HASH: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
/// 2026-10-20T12:00:00Z
const T_2026_10_20: u64 = 1_792_497_600;

fn acct(s: &str) -> AccountId {
    s.parse().unwrap()
}

/// Sets the caller and the block time (unix seconds).
fn ctx(caller: &str, unix_secs: u64) {
    testing_env!(VMContextBuilder::new().predecessor_account_id(acct(caller)).block_timestamp(unix_secs * 1_000_000_000).build());
}

fn fresh() -> NearBooks {
    ctx(OWNER, T_2026_10_20);
    NearBooks::new(acct(OWNER), acct(REPORTER))
}

fn m(key: &str, v: i128) -> Metric {
    Metric { key: key.to_string(), value: I128(v) }
}

fn submit(c: &mut NearBooks, date: &str, metrics: Vec<Metric>) {
    c.submit_snapshot(date.to_string(), metrics, HASH.to_string());
}

#[test]
fn dates_and_calendar() {
    assert_eq!(date_from_unix_secs(0), "1970-01-01");
    assert_eq!(date_from_unix_secs(T_2026_10_20), "2026-10-20");
    assert_eq!(date_from_unix_secs(1_709_164_800), "2024-02-29");
    assert!(valid_date("2024-02-29") && !valid_date("2026-02-29") && !valid_date("2026-13-01"));
    assert!(!valid_date("2026-1-01") && !valid_date("2026/10/20") && !valid_date("20261020") && !valid_date("2026-10-2x"));
}

#[test]
fn init_stores_config() {
    let c = fresh();
    assert_eq!(c.get_config(), (acct(OWNER), acct(REPORTER)));
    assert_eq!(c.snapshot_count(), 0);
    assert!(c.latest_snapshot().is_none());
}

// ------------------------------------------------------------------ auth

#[test]
#[should_panic(expected = "only the reporter can submit snapshots")]
fn stranger_cannot_submit() {
    let mut c = fresh();
    ctx(STRANGER, T_2026_10_20);
    submit(&mut c, "2026-10-20", vec![m("supply_total_near", 1)]);
}

#[test]
#[should_panic(expected = "only the reporter can submit snapshots")]
fn owner_is_not_the_reporter() {
    let mut c = fresh();
    submit(&mut c, "2026-10-20", vec![m("supply_total_near", 1)]);
}

#[test]
#[should_panic(expected = "only the owner can set the reporter")]
fn only_owner_rotates_reporter() {
    let mut c = fresh();
    ctx(REPORTER, T_2026_10_20);
    c.set_reporter(acct(STRANGER));
}

#[test]
fn rotated_reporter_replaces_old_one() {
    let mut c = fresh();
    c.set_reporter(acct("worker2.testnet"));
    ctx("worker2.testnet", T_2026_10_20);
    submit(&mut c, "2026-10-20", vec![m("supply_total_near", 1)]);
    assert_eq!(c.snapshot_count(), 1);
    assert_eq!(c.latest_snapshot().unwrap().submitted_by, acct("worker2.testnet"));
}

#[test]
#[should_panic(expected = "only the reporter can submit snapshots")]
fn old_reporter_loses_access_after_rotation() {
    let mut c = fresh();
    c.set_reporter(acct("worker2.testnet"));
    ctx(REPORTER, T_2026_10_20);
    submit(&mut c, "2026-10-20", vec![m("supply_total_near", 1)]);
}

#[test]
#[should_panic(expected = "only the owner can add predictions")]
fn reporter_cannot_add_predictions() {
    let mut c = fresh();
    ctx(REPORTER, T_2026_10_20);
    c.add_prediction("x".into(), "k".into(), Comparator::Gt, I128(0), "2026-12-31".into());
}

// -------------------------------------------------------------- snapshots

#[test]
fn snapshot_roundtrip() {
    let mut c = fresh();
    ctx(REPORTER, T_2026_10_20);
    submit(&mut c, "2026-10-19", vec![m("supply_total_near", 1_309_000_000_000_000), m("net_issuance_near_per_day", 87_855_000_000)]);
    submit(&mut c, "2026-10-20", vec![m("supply_total_near", 1_309_090_000_000_000)]);
    assert_eq!(c.snapshot_count(), 2);
    let s = c.get_snapshot("2026-10-19".into()).unwrap();
    assert_eq!(s.metrics.len(), 2);
    assert_eq!(s.metrics[1], m("net_issuance_near_per_day", 87_855_000_000));
    assert_eq!(s.evidence_hash, HASH);
    assert_eq!(s.submitted_by, acct(REPORTER));
    assert_eq!(s.block_timestamp_ms, T_2026_10_20 * 1000);
    assert_eq!(c.latest_snapshot().unwrap().date, "2026-10-20");
    assert!(c.get_snapshot("2026-10-18".into()).is_none());
    assert!(c.get_snapshot("2026-10-21".into()).is_none());
    assert_eq!(c.list_snapshots(Some(1), Some(10)).len(), 1);
}

#[test]
#[should_panic(expected = "must be later than the latest")]
fn snapshots_cannot_be_overwritten() {
    let mut c = fresh();
    ctx(REPORTER, T_2026_10_20);
    submit(&mut c, "2026-10-20", vec![m("a", 1)]);
    submit(&mut c, "2026-10-20", vec![m("a", 2)]);
}

#[test]
#[should_panic(expected = "must be later than the latest")]
fn snapshots_cannot_go_back_in_time() {
    let mut c = fresh();
    ctx(REPORTER, T_2026_10_20);
    submit(&mut c, "2026-10-20", vec![m("a", 1)]);
    submit(&mut c, "2026-10-19", vec![m("a", 1)]);
}

#[test]
#[should_panic(expected = "in the future")]
fn future_snapshot_rejected() {
    let mut c = fresh();
    ctx(REPORTER, T_2026_10_20);
    submit(&mut c, "2026-10-21", vec![m("a", 1)]);
}

#[test]
#[should_panic(expected = "date must be YYYY-MM-DD")]
fn malformed_date_rejected() {
    let mut c = fresh();
    ctx(REPORTER, T_2026_10_20);
    submit(&mut c, "20-10-2026", vec![m("a", 1)]);
}

#[test]
#[should_panic(expected = "duplicate metric key")]
fn duplicate_metric_keys_rejected() {
    let mut c = fresh();
    ctx(REPORTER, T_2026_10_20);
    submit(&mut c, "2026-10-20", vec![m("a", 1), m("a", 2)]);
}

#[test]
#[should_panic(expected = "metric keys must be")]
fn bad_metric_key_rejected() {
    let mut c = fresh();
    ctx(REPORTER, T_2026_10_20);
    submit(&mut c, "2026-10-20", vec![m("Bad Key", 1)]);
}

#[test]
#[should_panic(expected = "evidence_hash must be 64 lowercase hex chars")]
fn bad_hash_rejected() {
    let mut c = fresh();
    ctx(REPORTER, T_2026_10_20);
    c.submit_snapshot("2026-10-20".into(), vec![m("a", 1)], "abc".into());
}

// ------------------------------------------------------------ predictions

fn add(c: &mut NearBooks, cmp: Comparator, threshold: i128, resolve_after: &str) -> u64 {
    ctx(OWNER, T_2026_10_20);
    c.add_prediction("claim".into(), "supply_total_near".into(), cmp, I128(threshold), resolve_after.into())
}

#[test]
fn add_and_list_predictions() {
    let mut c = fresh();
    assert_eq!(add(&mut c, Comparator::Gt, 100, "2026-12-31"), 0);
    assert_eq!(add(&mut c, Comparator::Lte, 5, "2027-01-31"), 1);
    let p = c.get_prediction(1).unwrap();
    assert_eq!((p.status, p.comparator, p.created_date.as_str()), (Status::Pending, Comparator::Lte, "2026-10-20"));
    assert_eq!(c.list_predictions(None, None).len(), 2);
    assert!(c.get_prediction(2).is_none());
}

#[test]
#[should_panic(expected = "resolve_after must be in the future")]
fn prediction_for_today_or_past_rejected() {
    let mut c = fresh();
    add(&mut c, Comparator::Gt, 1, "2026-10-20");
}

#[test]
#[should_panic(expected = "no snapshot on/after resolve_after yet")]
fn cannot_resolve_without_a_late_enough_snapshot() {
    let mut c = fresh();
    add(&mut c, Comparator::Gt, 1, "2026-12-31");
    ctx(REPORTER, T_2026_10_20);
    submit(&mut c, "2026-10-20", vec![m("supply_total_near", 5)]);
    ctx(STRANGER, T_2026_10_20);
    c.resolve_prediction(0);
}

fn december() -> u64 {
    T_2026_10_20 + 72 * 86_400 // 2026-12-31
}

#[test]
fn anyone_resolves_confirmed() {
    let mut c = fresh();
    add(&mut c, Comparator::Gt, 1_000, "2026-12-31");
    ctx(REPORTER, december());
    submit(&mut c, "2026-12-31", vec![m("supply_total_near", 1_001)]);
    ctx(STRANGER, december());
    let p = c.resolve_prediction(0);
    assert_eq!(p.status, Status::Confirmed);
    let r = p.resolution.unwrap();
    assert_eq!((r.snapshot_date.as_str(), r.observed, r.resolved_by), ("2026-12-31", Some(I128(1_001)), acct(STRANGER)));
    assert_eq!(c.get_prediction(0).unwrap().status, Status::Confirmed);
}

#[test]
fn comparators_are_strict_where_they_say_so() {
    // observed == threshold: gt/lt refute, gte/lte confirm
    let mut c = fresh();
    for cmp in [Comparator::Gt, Comparator::Gte, Comparator::Lt, Comparator::Lte] {
        add(&mut c, cmp, 500, "2026-12-31");
    }
    ctx(REPORTER, december());
    submit(&mut c, "2026-12-31", vec![m("supply_total_near", 500)]);
    ctx(STRANGER, december());
    let got: Vec<Status> = (0..4).map(|i| c.resolve_prediction(i).status).collect();
    assert_eq!(got, vec![Status::Refuted, Status::Confirmed, Status::Refuted, Status::Confirmed]);
}

#[test]
fn negative_values_compare_correctly() {
    let mut c = fresh();
    add(&mut c, Comparator::Lt, -10, "2026-12-31");
    ctx(REPORTER, december());
    submit(&mut c, "2026-12-31", vec![m("supply_total_near", -11)]);
    ctx(STRANGER, december());
    assert_eq!(c.resolve_prediction(0).status, Status::Confirmed);
}

#[test]
fn first_eligible_snapshot_decides_even_if_later_ones_differ() {
    let mut c = fresh();
    add(&mut c, Comparator::Gt, 10, "2026-12-31");
    ctx(REPORTER, december() - 86_400);
    submit(&mut c, "2026-12-30", vec![m("supply_total_near", 1_000)]); // before resolve_after: ignored
    ctx(REPORTER, december());
    submit(&mut c, "2026-12-31", vec![m("supply_total_near", 5)]); // first eligible: 5 <= 10
    ctx(REPORTER, december() + 86_400);
    submit(&mut c, "2027-01-01", vec![m("supply_total_near", 9_999)]); // later snapshots cannot change the outcome
    ctx(STRANGER, december() + 86_400);
    let p = c.resolve_prediction(0);
    assert_eq!(p.status, Status::Refuted);
    let r = p.resolution.unwrap();
    assert_eq!((r.snapshot_date.as_str(), r.observed), ("2026-12-31", Some(I128(5))));
}

#[test]
fn missing_metric_in_first_eligible_snapshot_voids_and_later_snapshots_do_not_rescue() {
    let mut c = fresh();
    add(&mut c, Comparator::Gt, 10, "2026-12-31");
    ctx(REPORTER, december());
    submit(&mut c, "2026-12-31", vec![m("other_metric", 99)]); // lacks the metric
    ctx(REPORTER, december() + 86_400);
    submit(&mut c, "2027-01-01", vec![m("supply_total_near", 9_999)]); // would have confirmed; must not be consulted
    ctx(STRANGER, december() + 86_400);
    let p = c.resolve_prediction(0);
    assert_eq!(p.status, Status::Void);
    let r = p.resolution.unwrap();
    assert_eq!((r.snapshot_date.as_str(), r.observed), ("2026-12-31", None));
    assert_eq!(c.get_prediction(0).unwrap().status, Status::Void);
}

#[test]
#[should_panic(expected = "prediction already resolved")]
fn void_is_final() {
    let mut c = fresh();
    add(&mut c, Comparator::Gt, 10, "2026-12-31");
    ctx(REPORTER, december());
    submit(&mut c, "2026-12-31", vec![m("other_metric", 99)]);
    ctx(STRANGER, december());
    c.resolve_prediction(0);
    c.resolve_prediction(0);
}

#[test]
fn resolution_works_with_many_snapshots() {
    // 200 daily snapshots (value = day index); the deciding one is the 100th, far past any fixed scan window.
    // A later snapshot (value 101+) would flip the Gt prediction, so this also proves the first eligible one decides.
    let mut c = fresh();
    let target = date_from_unix_secs(T_2026_10_20 + 100 * 86_400);
    ctx(OWNER, T_2026_10_20);
    c.add_prediction("gte".into(), "supply_total_near".into(), Comparator::Gte, I128(100), target.clone());
    c.add_prediction("gt".into(), "supply_total_near".into(), Comparator::Gt, I128(100), target.clone());
    for d in 1..=200u64 {
        let ts = T_2026_10_20 + d * 86_400;
        ctx(REPORTER, ts);
        submit(&mut c, &date_from_unix_secs(ts), vec![m("supply_total_near", d as i128)]);
    }
    assert_eq!(c.snapshot_count(), 200);
    ctx(STRANGER, T_2026_10_20 + 200 * 86_400);
    let (p0, p1) = (c.resolve_prediction(0), c.resolve_prediction(1));
    assert_eq!((p0.status, p1.status), (Status::Confirmed, Status::Refuted));
    let r = p0.resolution.unwrap();
    assert_eq!((r.snapshot_date, r.observed), (target.clone(), Some(I128(100))));
    assert_eq!(c.get_snapshot(target).unwrap().metrics[0].value.0, 100);
}

#[test]
#[should_panic(expected = "prediction already resolved")]
fn resolution_is_final() {
    let mut c = fresh();
    add(&mut c, Comparator::Gt, 1, "2026-12-31");
    ctx(REPORTER, december());
    submit(&mut c, "2026-12-31", vec![m("supply_total_near", 5)]);
    ctx(STRANGER, december());
    c.resolve_prediction(0);
    c.resolve_prediction(0);
}

#[test]
#[should_panic(expected = "no such prediction")]
fn resolving_unknown_id_fails() {
    let mut c = fresh();
    c.resolve_prediction(7);
}

#[test]
fn json_shapes_are_stable() {
    let mut c = fresh();
    add(&mut c, Comparator::Gte, 7, "2026-12-31");
    let v = near_sdk::serde_json::to_value(c.get_prediction(0).unwrap()).unwrap();
    assert_eq!(v["comparator"], "gte");
    assert_eq!(v["status"], "pending");
    assert_eq!(v["threshold"], "7"); // i128 is a decimal string in JSON
    let args: Vec<Metric> = near_sdk::serde_json::from_str(r#"[{"key":"a","value":"-5"}]"#).unwrap();
    assert_eq!(args[0].value.0, -5);
}
