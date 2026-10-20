use super::*;
use near_sdk::mock::MockAction;
use near_sdk::test_utils::{get_created_receipts, get_logs, VMContextBuilder};
use near_sdk::serde_json::{self, Value};
use near_sdk::{testing_env, Gas, NearToken, PromiseResult};

const OWNER: &str = "owner.testnet";
const REPORTER: &str = "reporter.testnet";
const STRANGER: &str = "stranger.testnet";
const CONTRACT: &str = "nearbooks.testnet";
const OUTLAYER: &str = "outlayer.testnet";
const WASM: &str = "746273ae673a657a26f94f5b548c8906d79f2422987e0ffded04e4a83d813eb1";
const WASM2: &str = "1111111111111111111111111111111111111111111111111111111111111111";
const HASH: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
/// 2026-10-20T12:00:00Z
const T_2026_10_20: u64 = 1_792_497_600;

fn acct(s: &str) -> AccountId {
    s.parse().unwrap()
}

/// Sets the caller and the block time (unix seconds).
fn ctx(caller: &str, unix_secs: u64) {
    testing_env!(VMContextBuilder::new().current_account_id(acct(CONTRACT)).predecessor_account_id(acct(caller)).block_timestamp(unix_secs * 1_000_000_000).build());
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

// ------------------------------------------------- attested snapshots (OutLayer)

/// First mainnet block of 2026-10-20 (worker/scripts/first_block.py) and its real worker output (local wasmtime
/// run against mainnet archival RPC), with `n` and `rid` filled in by `out`.
const B_1020: u64 = 220_726_930;
const TS_1020: u64 = 1_792_454_400_548_428_142;
const SUP_1020: &str = "1309428647101414191323016386004184";

fn out(nonce: u64, date: &str, height: u64, ts_ns: u64, sup: &str) -> String {
    format!(
        r#"{{"bb":"2022180087915366303354191622511","cs":"270875891412420848813311023","d":"{}","fb":"0","fe":"1916643242806074001541587346866","h":"{}","ms":"3768","n":"{}","rc":"8","rd":"0","rid":"5200","rk":"8","rm":"0","rt":"","sha":"02701f609329428666c42c2ac1054348254954474ddb2f1bfb2feabe4f816dd5","sup":"{}","t":"{}","v":"2"}}"#,
        date, height, nonce, sup, ts_ns
    )
}

fn worker_output(nonce: u64) -> String {
    out(nonce, "2026-10-20", B_1020, TS_1020, SUP_1020)
}

fn pin(c: &mut NearBooks, hash: &str) {
    ctx(OWNER, T_2026_10_20);
    c.pin_worker(acct(OUTLAYER), format!("https://test.fastfs.io/x/outlayer.testnet/{}.wasm", hash), hash.into(), "https://testnet-api.outlayer.ai".into(), None, None);
}

/// `caller` calls request_snapshot(date, height) at unix time `now` with a deposit (milliNEAR) and 300 Tgas.
fn request_at(c: &mut NearBooks, caller: &str, millinear: u128, date: &str, height: u64, now: u64) -> u64 {
    testing_env!(VMContextBuilder::new()
        .current_account_id(acct(CONTRACT))
        .predecessor_account_id(acct(caller))
        .signer_account_id(acct(caller))
        .attached_deposit(NearToken::from_millinear(millinear))
        .prepaid_gas(Gas::from_tgas(300))
        .block_timestamp(now * 1_000_000_000)
        .build());
    let _ = c.request_snapshot(date.into(), height);
    c.next_nonce - 1
}

fn request(c: &mut NearBooks, caller: &str, millinear: u128) -> u64 {
    request_at(c, caller, millinear, "2026-10-20", B_1020, T_2026_10_20)
}

/// The callback as the runtime delivers it: predecessor = this contract, one promise result.
fn deliver_at(c: &mut NearBooks, caller: &str, nonce: u64, result: PromiseResult, now: u64) -> String {
    testing_env!(
        VMContextBuilder::new().current_account_id(acct(CONTRACT)).predecessor_account_id(acct(caller)).block_timestamp(now * 1_000_000_000).build(),
        near_sdk::test_vm_config(),
        near_sdk::RuntimeFeesConfig::test(),
        Default::default(),
        vec![result]
    );
    c.on_snapshot_result(nonce)
}

fn deliver(c: &mut NearBooks, nonce: u64, result: PromiseResult) -> String {
    deliver_at(c, CONTRACT, nonce, result, T_2026_10_20)
}

fn ok_bytes(s: &str) -> PromiseResult {
    PromiseResult::Successful(s.as_bytes().to_vec())
}

/// Stores the attested snapshot for day 2026-10-20 + `k` with total supply `supply_micro` (micro-NEAR).
fn attest(c: &mut NearBooks, k: u64, supply_micro: u128) -> String {
    let noon = T_2026_10_20 + k * 86_400;
    let date = date_from_unix_secs(noon);
    let height = B_1020 + k * 1_000;
    let nonce = request_at(c, STRANGER, 10, &date, height, noon);
    let sup = (supply_micro * 1_000_000_000_000_000_000).to_string();
    let r = deliver_at(c, CONTRACT, nonce, ok_bytes(&out(nonce, &date, height, (noon - 43_200) * 1_000_000_000 + 7, &sup)), noon);
    assert!(r.starts_with("stored"), "{}", r);
    date
}

fn supply(s: &Snapshot) -> i128 {
    s.metrics.iter().find(|m| m.key == "supply_total_near").unwrap().value.0
}

#[test]
fn request_snapshot_calls_outlayer_with_the_pinned_hash_date_and_block() {
    let mut c = fresh();
    pin(&mut c, WASM);
    let nonce = request(&mut c, STRANGER, 10);
    assert_eq!(nonce, 0);
    let p = c.get_pending(0).unwrap();
    assert_eq!((p.requester, p.wasm_hash.as_str(), p.date.as_str(), p.mainnet_block_height), (acct(STRANGER), WASM, "2026-10-20", B_1020));
    let receipts = get_created_receipts();
    let to_outlayer = receipts.iter().find(|r| r.receiver_id == acct(OUTLAYER)).expect("a receipt to OutLayer");
    let MockAction::FunctionCallWeight { method_name, args, attached_deposit, .. } = &to_outlayer.actions[0] else { panic!("not a function call") };
    assert_eq!(method_name, b"request_execution");
    assert_eq!(*attached_deposit, NearToken::from_millinear(10));
    let a: Value = serde_json::from_slice(args).unwrap();
    assert_eq!(a["source"]["WasmUrl"]["hash"], WASM);
    assert_eq!(a["source"]["WasmUrl"]["build_target"], "wasm32-wasip2");
    assert_eq!(a["input_data"], r#"{"n":"0","d":"2026-10-20","b":"220726930"}"#);
    assert_eq!(a["response_format"], "Json");
    assert_eq!(a["payer_account_id"], STRANGER, "OutLayer refunds the unused deposit to the caller");
    assert_eq!(a["resource_limits"]["max_instructions"], DEFAULT_MAX_INSTRUCTIONS);
    let callback = receipts.iter().find(|r| r.receiver_id == acct(CONTRACT)).expect("a callback receipt");
    let MockAction::FunctionCallWeight { method_name, args, .. } = &callback.actions[0] else { panic!() };
    assert_eq!((method_name.as_slice(), args.as_slice()), (b"on_snapshot_result".as_slice(), br#"{"nonce":0}"#.as_slice()));
}

#[test]
fn attested_snapshot_is_stored_with_source_tag() {
    let mut c = fresh();
    pin(&mut c, WASM);
    let nonce = request(&mut c, STRANGER, 10);
    let o = worker_output(nonce);
    assert_eq!(deliver(&mut c, nonce, ok_bytes(&o)), "stored attested snapshot 2026-10-20");
    assert_eq!((c.attested_count(), c.snapshot_count()), (1, 0), "attested and reported series are separate");
    assert!(c.get_pending(nonce).is_none(), "pending entry is consumed");
    let s = c.get_attested_snapshot("2026-10-20".into()).unwrap();
    assert_eq!(s.date, "2026-10-20");
    assert_eq!(s.submitted_by, acct(STRANGER));
    assert_eq!(s.evidence_hash, "02701f609329428666c42c2ac1054348254954474ddb2f1bfb2feabe4f816dd5");
    assert_eq!(
        s.source,
        Source::Attested {
            nonce: 0,
            request_id: 5200,
            wasm_hash: WASM.into(),
            block_height: B_1020,
            block_timestamp_ns: U64(TS_1020),
            output_sha256: hex(&env::sha256(o.as_bytes())),
            attestation: "https://testnet-api.outlayer.ai/attestations/by-request/5200".into(),
        }
    );
    let get = |k: &str| s.metrics.iter().find(|m| m.key == k).map(|m| m.value.0);
    assert_eq!(get("supply_total_near"), Some(1_309_428_647_101_414)); // yocto / 1e18 = micro-NEAR
    assert_eq!(get("buyback_wallet_near"), Some(2_022_180_087_915));
    assert_eq!(get("fefundsadmin_wallet_near"), Some(1_916_643_242_806));
    assert_eq!(get("onecsfundsadmin_wallet_near"), Some(270_875_891));
    assert_eq!(get("intents_reserves_tokens_checked"), Some(8_000_000));
    assert_eq!(get("intents_reserves_tokens_in_deficit"), Some(0));
    assert_eq!(get("mainnet_block_height"), Some(B_1020 as i128 * 1_000_000));
    let v = serde_json::to_value(&s).unwrap();
    assert_eq!(v["source"]["attested"]["request_id"], 5200);
    assert_eq!(v["source"]["attested"]["block_timestamp_ns"], TS_1020.to_string());
    assert_eq!(c.list_attested_snapshots(None, None).len(), 1);
    assert!(get_logs().iter().any(|l| l.contains(r#""event":"snapshot""#) && l.contains(r#""source":"attested""#)));
}

#[test]
fn reporter_path_is_tagged_reported() {
    let mut c = fresh();
    ctx(REPORTER, T_2026_10_20);
    submit(&mut c, "2026-10-20", vec![m("supply_total_near", 1)]);
    let s = c.latest_snapshot().unwrap();
    assert_eq!(s.source, Source::Reported);
    assert_eq!(serde_json::to_value(&s).unwrap()["source"], "reported");
    assert_eq!(c.attested_count(), 0);
}

#[test]
#[should_panic(expected = "only this contract can deliver OutLayer results")]
fn callback_from_outlayer_account_directly_is_rejected() {
    let mut c = fresh();
    pin(&mut c, WASM);
    let nonce = request(&mut c, STRANGER, 10);
    // OutLayer (or anyone) calling the callback directly, with a perfectly valid payload, must fail.
    deliver_at(&mut c, OUTLAYER, nonce, ok_bytes(&worker_output(nonce)), T_2026_10_20);
}

#[test]
#[should_panic(expected = "only this contract can deliver OutLayer results")]
fn callback_from_stranger_is_rejected() {
    let mut c = fresh();
    pin(&mut c, WASM);
    let nonce = request(&mut c, STRANGER, 10);
    deliver_at(&mut c, STRANGER, nonce, ok_bytes(&worker_output(nonce)), T_2026_10_20);
}

#[test]
fn wrong_wasm_hash_is_rejected() {
    let mut c = fresh();
    pin(&mut c, WASM);
    let nonce = request(&mut c, STRANGER, 10);
    pin(&mut c, WASM2); // owner re-pins while the run is in flight: the old run no longer matches the pin
    let r = deliver(&mut c, nonce, ok_bytes(&worker_output(nonce)));
    assert_eq!(r, "rejected: the pinned wasm hash changed while the run was in flight");
    assert_eq!(c.attested_count(), 0);
    assert!(c.get_pending(nonce).is_none());
    assert!(get_logs().iter().any(|l| l.contains("snapshot_rejected")));
}

#[test]
fn changed_outlayer_contract_is_rejected() {
    let mut c = fresh();
    pin(&mut c, WASM);
    let nonce = request(&mut c, STRANGER, 10);
    ctx(OWNER, T_2026_10_20);
    c.pin_worker(acct("evil-outlayer.testnet"), "https://x.example/w.wasm".into(), WASM.into(), "https://testnet-api.outlayer.ai".into(), None, None);
    let r = deliver(&mut c, nonce, ok_bytes(&worker_output(nonce)));
    assert!(r.starts_with("rejected: the configured OutLayer contract changed"), "{}", r);
    assert_eq!(c.attested_count(), 0);
}

#[test]
fn malformed_output_is_rejected_and_nothing_is_stored() {
    let good = worker_output(0);
    let cases: Vec<(String, &str)> = vec![
        ("not json".into(), "malformed worker output"),
        ("null".into(), "OutLayer reported the run as failed"),
        (r#""a string""#.into(), "malformed worker output"),
        (good.replace(r#","v":"2""#, ""), "malformed worker output"),                      // missing field
        (good.replace(r#""v":"2""#, r#""v":"2","x":"1""#), "malformed worker output"),     // unknown field
        (good.replace(r#""v":"2""#, r#""v":"1""#), "unsupported worker output version"),
        (good.replace(r#""n":"0""#, r#""n":"9""#), "nonce mismatch"),                      // answer for another request
        (good.replace(r#""d":"2026-10-20""#, r#""d":"2026-10-19""#), "different date"),
        (good.replace(r#""h":"220726930""#, r#""h":"220726931""#), "different block"),     // not the block asked for
        (good.replace(&TS_1020.to_string(), "1792454399000000000"), "not on the requested date"),
        (good.replace(r#""sup":"1309"#, r#""sup":"-1309"#), "sup must be a decimal string"),
        (good.replace(r#""rid":"5200""#, r#""rid":"""#), "rid must be a decimal string"),
        (good.replace(r#""rc":"8""#, r#""rc":"9""#), "inconsistent reserves fields"),
        (good.replace(r#""sha":"0270"#, r#""sha":"027F"#), "evidence_hash must be 64 lowercase hex chars"),
    ];
    for (payload, want) in cases {
        let mut c = fresh();
        pin(&mut c, WASM);
        let nonce = request(&mut c, STRANGER, 10);
        assert_eq!(nonce, 0);
        let r = deliver(&mut c, nonce, ok_bytes(&payload));
        assert!(r.starts_with("rejected:") && r.contains(want), "payload {} -> {} (wanted {})", payload, r, want);
        assert_eq!(c.attested_count(), 0, "{}", payload);
        assert!(c.get_pending(nonce).is_none());
    }
}

#[test]
fn oversized_output_is_rejected() {
    let mut c = fresh();
    pin(&mut c, WASM);
    let nonce = request(&mut c, STRANGER, 10);
    let r = deliver(&mut c, nonce, ok_bytes(&"x".repeat(MAX_WORKER_OUTPUT + 1)));
    assert!(r.contains("too long"), "{}", r);
    assert_eq!(c.attested_count(), 0);
}

#[test]
fn failed_outlayer_call_stores_nothing_and_sends_no_contract_funds() {
    let mut c = fresh();
    pin(&mut c, WASM);
    let nonce = request(&mut c, STRANGER, 25);
    let r = deliver(&mut c, nonce, PromiseResult::Failed);
    assert_eq!(r, "rejected: the OutLayer call failed before running; nothing stored");
    assert_eq!(c.attested_count(), 0);
    assert!(c.get_pending(nonce).is_none());
    // the callback creates no receipt at all: no transfer out of the contract's own balance
    assert!(get_created_receipts().is_empty(), "{:?}", get_created_receipts().iter().map(|r| r.receiver_id.to_string()).collect::<Vec<_>>());
}

#[test]
fn no_callback_outcome_ever_transfers_funds() {
    // every callback outcome on one contract; the storing one goes last because it fills the only date used
    let mut c = fresh();
    pin(&mut c, WASM);
    for result in [ok_bytes("null"), ok_bytes("garbage"), PromiseResult::Failed, ok_bytes("{}")] {
        let nonce = request(&mut c, STRANGER, 10);
        deliver(&mut c, nonce, result);
        assert!(get_created_receipts().is_empty());
    }
    let nonce = request(&mut c, STRANGER, 10);
    assert!(deliver(&mut c, nonce, ok_bytes(&worker_output(nonce))).starts_with("stored"));
    assert!(get_created_receipts().is_empty());
}

#[test]
fn one_attested_snapshot_per_date_first_valid_wins() {
    let mut c = fresh();
    pin(&mut c, WASM);
    // two runs for the same date are in flight at once (requesting is permissionless)
    let n0 = request(&mut c, STRANGER, 10);
    let n1 = request(&mut c, OWNER, 10);
    assert!(deliver(&mut c, n1, ok_bytes(&worker_output(n1))).starts_with("stored"));
    let r = deliver(&mut c, n0, ok_bytes(&worker_output(n0)));
    assert_eq!(r, "rejected: an attested snapshot for that date already exists");
    assert_eq!(c.attested_count(), 1);
    assert_eq!(c.get_attested_snapshot("2026-10-20".into()).unwrap().submitted_by, acct(OWNER));
}

#[test]
#[should_panic(expected = "an attested snapshot for that date already exists")]
fn request_for_an_attested_date_is_refused_up_front() {
    let mut c = fresh();
    pin(&mut c, WASM);
    let n = request(&mut c, STRANGER, 10);
    deliver(&mut c, n, ok_bytes(&worker_output(n)));
    request(&mut c, STRANGER, 10);
}

#[test]
#[should_panic(expected = "that date has not started yet")]
fn request_for_a_future_date_is_refused() {
    let mut c = fresh();
    pin(&mut c, WASM);
    request_at(&mut c, STRANGER, 10, "2026-10-21", B_1020, T_2026_10_20);
}

#[test]
#[should_panic(expected = "date must be YYYY-MM-DD")]
fn request_with_a_bad_date_is_refused() {
    let mut c = fresh();
    pin(&mut c, WASM);
    request_at(&mut c, STRANGER, 10, "20-10-2026", B_1020, T_2026_10_20);
}

#[test]
fn dates_can_be_backfilled_in_any_order() {
    let mut c = fresh();
    pin(&mut c, WASM);
    attest(&mut c, 2, 300);
    attest(&mut c, 0, 100); // an earlier, missed day: canonical, so filling it later changes nothing it could bias
    attest(&mut c, 1, 200);
    assert_eq!(c.attested_count(), 3);
    let dates: Vec<String> = c.list_attested_snapshots(None, None).into_iter().map(|s| s.date).collect();
    assert_eq!(dates, vec!["2026-10-22", "2026-10-20", "2026-10-21"]);
    assert_eq!(supply(&c.get_attested_snapshot("2026-10-21".into()).unwrap()), 200);
}

#[test]
fn reported_and_attested_series_do_not_block_each_other() {
    let mut c = fresh();
    ctx(REPORTER, T_2026_10_20);
    submit(&mut c, "2026-10-20", vec![m("supply_total_near", 1)]);
    pin(&mut c, WASM);
    let n = request(&mut c, STRANGER, 10);
    assert!(deliver(&mut c, n, ok_bytes(&worker_output(n))).starts_with("stored"));
    assert_eq!((c.snapshot_count(), c.attested_count()), (1, 1));
    assert_eq!(c.latest_snapshot().unwrap().source, Source::Reported);
}

#[test]
fn each_nonce_is_answered_once() {
    let mut c = fresh();
    pin(&mut c, WASM);
    let n0 = request(&mut c, STRANGER, 10);
    let n1 = request(&mut c, STRANGER, 10);
    assert_eq!((n0, n1), (0, 1));
    // the answer to request 0 delivered for request 1 is refused (the nonce is inside the attested output)
    let r = deliver(&mut c, n1, ok_bytes(&worker_output(n0)));
    assert!(r.contains("nonce mismatch"), "{}", r);
    assert!(deliver(&mut c, n0, ok_bytes(&worker_output(n0))).starts_with("stored"));
}

#[test]
#[should_panic(expected = "unknown request nonce")]
fn unknown_nonce_panics() {
    let mut c = fresh();
    pin(&mut c, WASM);
    deliver(&mut c, 3, ok_bytes(&worker_output(3)));
}

#[test]
#[should_panic(expected = "the OutLayer worker is not configured")]
fn request_needs_a_pinned_worker() {
    let mut c = fresh();
    request(&mut c, STRANGER, 10);
}

#[test]
#[should_panic(expected = "attach at least 0.01 NEAR")]
fn request_needs_a_deposit() {
    let mut c = fresh();
    pin(&mut c, WASM);
    request(&mut c, STRANGER, 9);
}

#[test]
#[should_panic(expected = "only the owner can pin the worker")]
fn only_owner_pins_the_worker() {
    let mut c = fresh();
    ctx(REPORTER, T_2026_10_20);
    c.pin_worker(acct(OUTLAYER), "https://a/b.wasm".into(), WASM.into(), "https://testnet-api.outlayer.ai".into(), None, None);
}

#[test]
#[should_panic(expected = "wasm_hash must be 64 lowercase hex chars")]
fn pin_validates_the_hash() {
    let mut c = fresh();
    ctx(OWNER, T_2026_10_20);
    c.pin_worker(acct(OUTLAYER), "https://a/b.wasm".into(), "ABC".into(), "https://testnet-api.outlayer.ai".into(), None, None);
}

#[test]
fn repin_emits_event_with_previous_hash() {
    let mut c = fresh();
    pin(&mut c, WASM);
    pin(&mut c, WASM2);
    let logs = get_logs();
    let ev = logs.iter().find(|l| l.contains("worker_pinned")).unwrap();
    assert!(ev.contains(&format!(r#""previous_wasm_hash":"{}""#, WASM)) && ev.contains(&format!(r#""wasm_hash":"{}""#, WASM2)), "{}", ev);
    assert_eq!(c.get_outlayer_config().unwrap().wasm_hash, WASM2);
}

// ------------------------------------------------------------ predictions

fn add(c: &mut NearBooks, cmp: Comparator, threshold: i128, resolve_after: &str) -> u64 {
    ctx(OWNER, T_2026_10_20);
    c.add_prediction("claim".into(), "supply_total_near".into(), cmp, I128(threshold), resolve_after.into())
}

/// 2026-12-31 is day 72 after 2026-10-20.
const DEC31: u64 = 72;

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
fn anyone_resolves_confirmed_from_the_attested_snapshot() {
    let mut c = fresh();
    add(&mut c, Comparator::Gt, 1_000, "2026-12-31");
    pin(&mut c, WASM);
    assert_eq!(attest(&mut c, DEC31, 1_001), "2026-12-31");
    ctx(STRANGER, T_2026_10_20 + DEC31 * 86_400);
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
    pin(&mut c, WASM);
    attest(&mut c, DEC31, 500);
    ctx(STRANGER, T_2026_10_20 + DEC31 * 86_400);
    let got: Vec<Status> = (0..4).map(|i| c.resolve_prediction(i).status).collect();
    assert_eq!(got, vec![Status::Refuted, Status::Confirmed, Status::Refuted, Status::Confirmed]);
}

#[test]
fn negative_values_compare_correctly() {
    assert!(Comparator::Lt.holds(-11, -10));
    assert!(!Comparator::Gt.holds(-11, -10));
    assert!(Comparator::Gte.holds(-10, -10) && Comparator::Lte.holds(-10, -10));
}

#[test]
fn the_snapshot_for_resolve_after_decides_not_neighbours() {
    let mut c = fresh();
    add(&mut c, Comparator::Gt, 10, "2026-12-31");
    pin(&mut c, WASM);
    attest(&mut c, DEC31 - 1, 1_000); // the day before: ignored
    attest(&mut c, DEC31, 5); // the deciding day: 5 <= 10
    attest(&mut c, DEC31 + 1, 9_999); // later days cannot change the outcome
    ctx(STRANGER, T_2026_10_20 + (DEC31 + 1) * 86_400);
    let p = c.resolve_prediction(0);
    assert_eq!(p.status, Status::Refuted);
    let r = p.resolution.unwrap();
    assert_eq!((r.snapshot_date.as_str(), r.observed), ("2026-12-31", Some(I128(5))));
}

#[test]
fn a_later_snapshot_cannot_stand_in_for_the_missing_deciding_one() {
    // Griefing attempt: skip 2026-12-31 and attest 2027-01-01 first, hoping it decides. It does not; the prediction
    // waits for the canonical 2026-12-31 snapshot, which anyone can still backfill.
    let mut c = fresh();
    add(&mut c, Comparator::Gt, 10, "2026-12-31");
    pin(&mut c, WASM);
    attest(&mut c, DEC31 + 1, 9_999);
    ctx(STRANGER, T_2026_10_20 + (DEC31 + 1) * 86_400);
    let early = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| c.resolve_prediction(0)));
    assert!(early.is_err(), "must not resolve from 2027-01-01");
    attest(&mut c, DEC31, 5);
    ctx(STRANGER, T_2026_10_20 + (DEC31 + 1) * 86_400);
    let p = c.resolve_prediction(0);
    assert_eq!((p.status, p.resolution.unwrap().snapshot_date.as_str()), (Status::Refuted, "2026-12-31"));
}

#[test]
#[should_panic(expected = "no attested snapshot for resolve_after yet")]
fn reported_snapshots_never_resolve_predictions() {
    let mut c = fresh();
    add(&mut c, Comparator::Gt, 1, "2026-12-31");
    ctx(REPORTER, T_2026_10_20 + DEC31 * 86_400);
    submit(&mut c, "2026-12-31", vec![m("supply_total_near", 5)]);
    ctx(STRANGER, T_2026_10_20 + DEC31 * 86_400);
    c.resolve_prediction(0);
}

#[test]
#[should_panic(expected = "no attested snapshot for resolve_after yet")]
fn cannot_resolve_before_the_deciding_snapshot_exists() {
    let mut c = fresh();
    add(&mut c, Comparator::Gt, 1, "2026-12-31");
    pin(&mut c, WASM);
    let n = request(&mut c, STRANGER, 10);
    deliver(&mut c, n, ok_bytes(&worker_output(n)));
    ctx(STRANGER, T_2026_10_20);
    c.resolve_prediction(0);
}

#[test]
fn metric_the_worker_does_not_report_voids() {
    let mut c = fresh();
    ctx(OWNER, T_2026_10_20);
    c.add_prediction("revenue".into(), "revenue_ytd_usd".into(), Comparator::Gt, I128(0), "2026-12-31".into());
    pin(&mut c, WASM);
    attest(&mut c, DEC31, 1);
    ctx(STRANGER, T_2026_10_20 + DEC31 * 86_400);
    let p = c.resolve_prediction(0);
    assert_eq!(p.status, Status::Void);
    assert_eq!(p.resolution.unwrap().observed, None);
    assert_eq!(c.get_prediction(0).unwrap().status, Status::Void);
}

#[test]
#[should_panic(expected = "prediction already resolved")]
fn void_is_final() {
    let mut c = fresh();
    ctx(OWNER, T_2026_10_20);
    c.add_prediction("revenue".into(), "revenue_ytd_usd".into(), Comparator::Gt, I128(0), "2026-12-31".into());
    pin(&mut c, WASM);
    attest(&mut c, DEC31, 1);
    ctx(STRANGER, T_2026_10_20 + DEC31 * 86_400);
    c.resolve_prediction(0);
    c.resolve_prediction(0);
}

#[test]
fn resolution_works_with_many_snapshots() {
    // 200 daily attested snapshots (supply = day index); the deciding one is day 100.
    let mut c = fresh();
    let target = date_from_unix_secs(T_2026_10_20 + 100 * 86_400);
    ctx(OWNER, T_2026_10_20);
    c.add_prediction("gte".into(), "supply_total_near".into(), Comparator::Gte, I128(100), target.clone());
    c.add_prediction("gt".into(), "supply_total_near".into(), Comparator::Gt, I128(100), target.clone());
    pin(&mut c, WASM);
    for d in 1..=200u64 {
        attest(&mut c, d, d as u128);
    }
    assert_eq!(c.attested_count(), 200);
    ctx(STRANGER, T_2026_10_20 + 200 * 86_400);
    let (p0, p1) = (c.resolve_prediction(0), c.resolve_prediction(1));
    assert_eq!((p0.status, p1.status), (Status::Confirmed, Status::Refuted));
    let r = p0.resolution.unwrap();
    assert_eq!((r.snapshot_date, r.observed), (target.clone(), Some(I128(100))));
}

#[test]
#[should_panic(expected = "prediction already resolved")]
fn resolution_is_final() {
    let mut c = fresh();
    add(&mut c, Comparator::Gt, 1, "2026-12-31");
    pin(&mut c, WASM);
    attest(&mut c, DEC31, 5);
    ctx(STRANGER, T_2026_10_20 + DEC31 * 86_400);
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
    let v = serde_json::to_value(c.get_prediction(0).unwrap()).unwrap();
    assert_eq!(v["comparator"], "gte");
    assert_eq!(v["status"], "pending");
    assert_eq!(v["threshold"], "7"); // i128 is a decimal string in JSON
    let args: Vec<Metric> = serde_json::from_str(r#"[{"key":"a","value":"-5"}]"#).unwrap();
    assert_eq!(args[0].value.0, -5);
}
