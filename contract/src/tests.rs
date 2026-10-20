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
/// Timelock used by `fresh()` (seconds between propose_pin and apply_pin).
const DELAY: u64 = 3_600;

/// The mock resets `storage_usage` with every new context; carry it over so cross-call measurements match the chain.
fn carried_usage() -> u64 {
    env::storage_usage()
}

fn acct(s: &str) -> AccountId {
    s.parse().unwrap()
}

/// Sets the caller and the block time (unix seconds).
fn ctx(caller: &str, unix_secs: u64) {
    testing_env!(VMContextBuilder::new().storage_usage(carried_usage()).clone().current_account_id(acct(CONTRACT)).predecessor_account_id(acct(caller)).block_timestamp(unix_secs * 1_000_000_000).build());
}

fn fresh() -> NearBooks {
    ctx(OWNER, T_2026_10_20);
    NearBooks::new(acct(OWNER), acct(REPORTER), DELAY)
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

/// First mainnet block of 2026-10-20 and of 2026-09-22 (worker/scripts/first_block.py 2026-10-20 --pair) and the
/// real values the worker reads there (mainnet archival RPC), with `n` and `rid` filled in by `out`.
const B_1020: u64 = 220_726_930;
const B_0922: u64 = 216_696_053;
const TS_1020: u64 = 1_792_454_400_548_428_142;
const SUP_1020: &str = "1309428647101414191323016386004184";
const SUP_0922: &str = "1306946432063617123518596881337035";

fn out28(nonce: u64, date: &str, height: u64, ts_ns: u64, sup: &str, height_28: u64, sup_28: &str) -> String {
    format!(
        r#"{{"bb":"2022180087915366303354191622511","bb28":"1742992598429748746718610202638","cs":"270875891412420848813311023","cs28":"270776835252430877653311023","d":"{}","fb":"0","fe":"1916643242806074001541587346866","fe28":"1761656506829824773977686937425","h":"{}","h28":"{}","ms":"3768","n":"{}","rc":"8","rd":"0","rid":"5200","rk":"8","rm":"0","rt":"","sha":"02701f609329428666c42c2ac1054348254954474ddb2f1bfb2feabe4f816dd5","sup":"{}","sup28":"{}","t":"{}","v":"3"}}"#,
        date, height, height_28, nonce, sup, sup_28, ts_ns
    )
}

/// The 28-day block the helpers use: the real one for B_1020, `height - 4_000_000` for synthetic days.
fn b28_for(height: u64) -> u64 {
    if height == B_1020 { B_0922 } else { height - 4_000_000 }
}

fn out(nonce: u64, date: &str, height: u64, ts_ns: u64, sup: &str) -> String {
    out28(nonce, date, height, ts_ns, sup, b28_for(height), SUP_0922)
}

fn worker_output(nonce: u64) -> String {
    out28(nonce, "2026-10-20", B_1020, TS_1020, SUP_1020, B_0922, SUP_0922)
}

fn propose_at(c: &mut NearBooks, outlayer: &str, hash: &str, now: u64) {
    ctx(OWNER, now);
    c.propose_pin(acct(outlayer), format!("https://test.fastfs.io/x/outlayer.testnet/{}.wasm", hash), hash.into(), "https://testnet-api.outlayer.ai".into(), None, None);
}

/// The first pin (applies at once).
fn pin(c: &mut NearBooks, hash: &str) {
    assert!(c.pins.is_empty(), "use repin() for later pins");
    propose_at(c, OUTLAYER, hash, T_2026_10_20);
}

/// A later pin: propose at `now`, apply when the timelock ends. Returns the apply time (unix seconds).
fn repin(c: &mut NearBooks, outlayer: &str, hash: &str, now: u64) -> u64 {
    propose_at(c, outlayer, hash, now);
    ctx(OWNER, now + DELAY);
    c.apply_pin();
    now + DELAY
}

/// 0.02 NEAR at the test VM's byte cost (1e19 yocto per byte).
fn reserve() -> NearToken {
    NearToken::from_yoctonear(10u128.pow(19) * STORAGE_RESERVE_BYTES as u128)
}

/// `caller` calls request_snapshot(date, height) at unix time `now` with `millinear` for OutLayer on top of the
/// storage reserve, and 300 Tgas.
fn request_at(c: &mut NearBooks, caller: &str, millinear: u128, date: &str, height: u64, now: u64) -> u64 {
    testing_env!(VMContextBuilder::new().storage_usage(carried_usage()).clone()
        .current_account_id(acct(CONTRACT))
        .predecessor_account_id(acct(caller))
        .signer_account_id(acct(caller))
        .attached_deposit(NearToken::from_millinear(millinear).saturating_add(reserve()))
        .prepaid_gas(Gas::from_tgas(300))
        .block_timestamp(now * 1_000_000_000)
        .build());
    let _ = c.request_snapshot(date.into(), height, b28_for(height));
    c.next_nonce - 1
}

fn request(c: &mut NearBooks, caller: &str, millinear: u128) -> u64 {
    request_at(c, caller, millinear, "2026-10-20", B_1020, T_2026_10_20)
}

/// The callback as the runtime delivers it: predecessor = this contract, one promise result.
fn deliver_at(c: &mut NearBooks, caller: &str, nonce: u64, result: PromiseResult, now: u64) -> String {
    testing_env!(
        VMContextBuilder::new().storage_usage(carried_usage()).clone().current_account_id(acct(CONTRACT)).predecessor_account_id(acct(caller)).block_timestamp(now * 1_000_000_000).build(),
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
    assert_eq!(*attached_deposit, NearToken::from_millinear(10), "the storage reserve stays in the contract");
    assert_eq!(c.get_pending(0).unwrap().storage_reserve, reserve());
    let a: Value = serde_json::from_slice(args).unwrap();
    assert_eq!(a["source"]["WasmUrl"]["hash"], WASM);
    assert_eq!(a["source"]["WasmUrl"]["build_target"], "wasm32-wasip2");
    assert_eq!(a["input_data"], r#"{"n":"0","d":"2026-10-20","b":"220726930","b28":"216696053"}"#);
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
    // derived from the two observations, 28 days apart
    assert_eq!(get("mainnet_block_height_28d"), Some(B_0922 as i128 * 1_000_000));
    assert_eq!(get("supply_total_near_28d_ago"), Some(1_306_946_432_063_617));
    assert_eq!(get("net_issuance_near_per_day"), Some(88_650_537_064)); // 88,650.5 NEAR/day
    assert_eq!(get("revenue_wallets_gain_near_28d"), Some(434_174_324_518)); // 434,174 NEAR kept by the three wallets
    assert_eq!(get("revenue_wallets_gain_over_net_issuance"), Some(174_914)); // 0.1749: wallets kept 17% of new supply
    assert!(s.metrics.len() <= MAX_METRICS_PER_SNAPSHOT);
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
fn repin_while_in_flight_does_not_change_the_pin_for_that_date() {
    // The run for 2026-10-20 is in flight when a re-pin to another hash and another OutLayer contract is applied.
    // 2026-10-20 started before the re-pin, so it stays observed by the old pin and the run is accepted.
    let mut c = fresh();
    pin(&mut c, WASM);
    let nonce = request(&mut c, STRANGER, 10);
    let applied = repin(&mut c, "other-outlayer.testnet", WASM2, T_2026_10_20);
    assert_eq!(c.get_outlayer_config().unwrap().wasm_hash, WASM2);
    assert_eq!(c.get_pin_for_date("2026-10-20".into()).unwrap().wasm_hash, WASM);
    let r = deliver_at(&mut c, CONTRACT, nonce, ok_bytes(&worker_output(nonce)), applied);
    assert_eq!(r, "stored attested snapshot 2026-10-20");
    let Source::Attested { wasm_hash, .. } = c.get_attested_snapshot("2026-10-20".into()).unwrap().source else { panic!() };
    assert_eq!(wasm_hash, WASM);
}

#[test]
fn corrupted_pin_history_is_still_caught_by_the_callback() {
    // Not reachable through the public API (pins only govern later dates); the callback re-checks anyway.
    let mut c = fresh();
    pin(&mut c, WASM);
    let nonce = request(&mut c, STRANGER, 10);
    let mut e = c.pins.get(0).unwrap().clone();
    e.config.wasm_hash = WASM2.into();
    c.pins.replace(0, e.clone());
    let r = deliver(&mut c, nonce, ok_bytes(&worker_output(nonce)));
    assert_eq!(r, "rejected: the pinned wasm hash for that date changed while the run was in flight");
    let nonce = request(&mut c, STRANGER, 10);
    e.config.outlayer_id = acct("evil-outlayer.testnet");
    c.pins.replace(0, e);
    let r = deliver(&mut c, nonce, ok_bytes(&worker_output(nonce)));
    assert!(r.starts_with("rejected: the OutLayer contract for that date changed"), "{}", r);
    assert_eq!(c.attested_count(), 0);
    assert!(get_logs().iter().any(|l| l.contains("snapshot_rejected")));
}

#[test]
fn malformed_output_is_rejected_and_nothing_is_stored() {
    let good = worker_output(0);
    let cases: Vec<(String, &str)> = vec![
        ("not json".into(), "malformed worker output"),
        ("null".into(), "OutLayer reported the run as failed"),
        (r#""a string""#.into(), "malformed worker output"),
        (good.replace(r#","v":"3""#, ""), "malformed worker output"),                      // missing field
        (good.replace(r#","sup28":"1306"#, r#","sux":"1306"#), "malformed worker output"),  // renamed field
        (good.replace(r#""v":"3""#, r#""v":"3","x":"1""#), "malformed worker output"),     // unknown field
        (good.replace(r#""v":"3""#, r#""v":"2""#), "unsupported worker output version"),
        (good.replace(r#""h28":"216696053""#, r#""h28":"216696054""#), "different 28-day block"),
        (good.replace(r#""sup28":"1306"#, r#""sup28":"0"#).replace(r#""sup28":"0946432063617123518596881337035""#, r#""sup28":"0""#), "total supply is zero"),
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

/// Transfers the callback created: (receiver, amount).
fn transfers() -> Vec<(AccountId, NearToken)> {
    get_created_receipts()
        .into_iter()
        .flat_map(|r| {
            let to = r.receiver_id.clone();
            r.actions.into_iter().filter_map(move |a| match a {
                MockAction::Transfer { deposit, .. } => Some((to.clone(), deposit)),
                _ => None,
            })
        })
        .collect()
}

#[test]
fn failed_outlayer_call_stores_nothing_and_returns_only_the_reserve() {
    let mut c = fresh();
    pin(&mut c, WASM);
    let nonce = request(&mut c, STRANGER, 25);
    let r = deliver(&mut c, nonce, PromiseResult::Failed);
    assert_eq!(r, "rejected: the OutLayer call failed before running; nothing stored");
    assert_eq!(c.attested_count(), 0);
    assert!(c.get_pending(nonce).is_none());
    // the OutLayer part (25 milliNEAR) is NOT re-sent from contract funds; only this request's untouched reserve is
    assert_eq!(transfers(), vec![(acct(STRANGER), reserve())]);
}

#[test]
fn callback_never_sends_more_than_the_requests_own_unused_reserve() {
    // every callback outcome on one contract; the storing one goes last because it fills the only date used
    let mut c = fresh();
    pin(&mut c, WASM);
    for result in [ok_bytes("null"), ok_bytes("garbage"), PromiseResult::Failed, ok_bytes("{}")] {
        let nonce = request(&mut c, STRANGER, 10);
        deliver(&mut c, nonce, result);
        assert_eq!(transfers(), vec![(acct(STRANGER), reserve())], "nothing stored: the full reserve and nothing else");
    }
    let nonce = request(&mut c, STRANGER, 10);
    assert!(deliver(&mut c, nonce, ok_bytes(&worker_output(nonce))).starts_with("stored"));
    let t = transfers();
    assert_eq!(t.len(), 1);
    assert!(t[0].0 == acct(STRANGER) && t[0].1 < reserve(), "{:?}", t);
}

#[test]
fn storage_is_paid_by_the_requester() {
    let mut c = fresh();
    pin(&mut c, WASM);
    ctx(STRANGER, T_2026_10_20);
    let before = env::storage_usage();
    let nonce = request(&mut c, STRANGER, 10);
    assert!(deliver(&mut c, nonce, ok_bytes(&worker_output(nonce))).starts_with("stored"));
    let grown = env::storage_usage() - before;
    println!("attested snapshot storage: {} bytes", grown);
    let staked = NearToken::from_yoctonear(10u128.pow(19) * grown as u128);
    let refunded = transfers()[0].1;
    // the contract kept exactly what the new snapshot stakes, so its free balance is unchanged by the snapshot
    assert_eq!(reserve().saturating_sub(refunded), staked);
    assert!(grown > 500 && grown < STORAGE_RESERVE_BYTES, "snapshot uses {} bytes", grown);
    assert!(get_logs().iter().any(|l| l.contains("storage_settled") && l.contains(&format!(r#""bytes":{}"#, grown))));
}

#[test]
#[should_panic(expected = "attach at least min_request_deposit()")]
fn deposit_without_the_storage_reserve_is_refused() {
    let mut c = fresh();
    pin(&mut c, WASM);
    testing_env!(VMContextBuilder::new().storage_usage(carried_usage()).clone()
        .current_account_id(acct(CONTRACT))
        .predecessor_account_id(acct(STRANGER))
        .attached_deposit(NearToken::from_millinear(10)) // enough for OutLayer, nothing for storage
        .prepaid_gas(Gas::from_tgas(300))
        .block_timestamp(T_2026_10_20 * 1_000_000_000)
        .build());
    let _ = c.request_snapshot("2026-10-20".into(), B_1020, B_0922);
}

#[test]
fn min_request_deposit_is_outlayer_part_plus_reserve() {
    let c = fresh();
    assert_eq!(c.min_request_deposit(), NearToken::from_millinear(10).saturating_add(reserve()));
}

#[test]
#[should_panic(expected = "date is before earliest_date")]
fn date_before_earliest_date_is_refused() {
    let mut c = fresh(); // deployed 2026-10-20: that is the default earliest_date
    assert_eq!(c.get_earliest_date(), "2026-10-20");
    pin(&mut c, WASM);
    request_at(&mut c, STRANGER, 10, "2026-10-19", B_1020 - 1_000, T_2026_10_20);
}

#[test]
fn owner_can_move_earliest_date_earlier() {
    let mut c = fresh();
    pin(&mut c, WASM);
    ctx(OWNER, T_2026_10_20);
    c.set_earliest_date("2026-10-19".into());
    let n = request_at(&mut c, STRANGER, 10, "2026-10-19", B_1020 - 1_000, T_2026_10_20);
    assert_eq!(c.get_pending(n).unwrap().date, "2026-10-19");
}

#[test]
#[should_panic(expected = "earliest_date can only move earlier")]
fn earliest_date_cannot_move_forward() {
    let mut c = fresh();
    ctx(OWNER, T_2026_10_20 + 100 * 86_400);
    // moving it past a prediction's resolve_after would block that prediction; refused outright
    c.set_earliest_date("2027-01-01".into());
}

#[test]
#[should_panic(expected = "earliest_date can only move earlier")]
fn earliest_date_cannot_be_reset_to_the_same_day() {
    let mut c = fresh();
    ctx(OWNER, T_2026_10_20);
    c.set_earliest_date("2026-10-20".into());
}

#[test]
fn pending_resolve_date_is_requestable_even_before_earliest_date() {
    // Construct the state directly (impossible through the public API, which keeps resolve_after > earliest_date),
    // to show the exemption holds on its own: a Pending prediction's date can always be observed.
    let mut c = fresh();
    ctx(OWNER, T_2026_10_20);
    c.add_prediction("x".into(), "supply_total_near".into(), Comparator::Gt, I128(0), "2026-12-31".into());
    c.earliest_date = "2027-06-01".into();
    pin(&mut c, WASM);
    let noon = T_2026_10_20 + DEC31 * 86_400;
    let n = request_at(&mut c, STRANGER, 10, "2026-12-31", B_1020 + 72_000, noon);
    assert_eq!(c.get_pending(n).unwrap().date, "2026-12-31");
    // a date no prediction waits for is still refused
    let other = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| request_at(&mut c, STRANGER, 10, "2026-12-30", B_1020 + 71_000, noon)));
    assert!(other.is_err());
}

#[test]
fn resolving_releases_the_resolve_date_exemption() {
    let mut c = fresh();
    ctx(OWNER, T_2026_10_20);
    c.add_prediction("a".into(), "supply_total_near".into(), Comparator::Gt, I128(0), "2026-12-31".into());
    c.add_prediction("b".into(), "supply_total_near".into(), Comparator::Gt, I128(0), "2026-12-31".into());
    assert_eq!(c.pending_resolve_dates.get("2026-12-31").copied(), Some(2));
    pin(&mut c, WASM);
    attest(&mut c, DEC31, 5);
    ctx(STRANGER, T_2026_10_20 + DEC31 * 86_400);
    c.resolve_prediction(0);
    assert_eq!(c.pending_resolve_dates.get("2026-12-31").copied(), Some(1));
    c.resolve_prediction(1);
    assert!(c.pending_resolve_dates.get("2026-12-31").is_none());
}

#[test]
fn repin_does_not_touch_stored_snapshots_or_their_predictions() {
    let mut c = fresh();
    add(&mut c, Comparator::Gt, 1_000, "2026-12-31");
    pin(&mut c, WASM);
    attest(&mut c, DEC31, 1_001);
    let before = c.get_attested_snapshot("2026-12-31".into()).unwrap();
    repin(&mut c, "other-outlayer.testnet", WASM2, T_2026_10_20 + DEC31 * 86_400);
    assert!(get_logs().iter().any(|l| l.contains("worker_pinned") && l.contains(WASM)), "event names the previous hash");
    let after = c.get_attested_snapshot("2026-12-31".into()).unwrap();
    assert_eq!(after.source, before.source, "still tagged with the hash that produced it");
    ctx(STRANGER, T_2026_10_20 + DEC31 * 86_400);
    assert_eq!(c.resolve_prediction(0).status, Status::Confirmed);
}

#[test]
fn reporter_rotation_emits_an_event() {
    let mut c = fresh();
    c.set_reporter(acct("worker2.testnet"));
    assert!(get_logs().iter().any(|l| l.contains("reporter_set") && l.contains("worker2.testnet")));
}

#[test]
#[should_panic(expected = "only the owner can set earliest_date")]
fn only_owner_moves_earliest_date() {
    let mut c = fresh();
    ctx(STRANGER, T_2026_10_20);
    c.set_earliest_date("2000-01-01".into());
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
#[should_panic(expected = "attach at least min_request_deposit()")]
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
    c.propose_pin(acct(OUTLAYER), "https://a/b.wasm".into(), WASM.into(), "https://testnet-api.outlayer.ai".into(), None, None);
}

#[test]
#[should_panic(expected = "wasm_hash must be 64 lowercase hex chars")]
fn pin_validates_the_hash() {
    let mut c = fresh();
    ctx(OWNER, T_2026_10_20);
    c.propose_pin(acct(OUTLAYER), "https://a/b.wasm".into(), "ABC".into(), "https://testnet-api.outlayer.ai".into(), None, None);
}

#[test]
fn repin_emits_events_with_previous_hash() {
    let mut c = fresh();
    pin(&mut c, WASM);
    propose_at(&mut c, OUTLAYER, WASM2, T_2026_10_20);
    let ev = get_logs().into_iter().find(|l| l.contains("pin_proposed")).unwrap();
    assert!(ev.contains(&format!(r#""previous_wasm_hash":"{}""#, WASM)) && ev.contains(&format!(r#""wasm_hash":"{}""#, WASM2)), "{}", ev);
    assert!(ev.contains(&format!(r#""apply_after_ns":"{}""#, (T_2026_10_20 + DELAY) * 1_000_000_000)), "{}", ev);
    ctx(OWNER, T_2026_10_20 + DELAY);
    c.apply_pin();
    let ev = get_logs().into_iter().find(|l| l.contains("worker_pinned")).unwrap();
    assert!(ev.contains(&format!(r#""previous_wasm_hash":"{}""#, WASM)) && ev.contains(&format!(r#""from_ns":"{}""#, (T_2026_10_20 + DELAY) * 1_000_000_000)), "{}", ev);
    assert_eq!(c.get_outlayer_config().unwrap().wasm_hash, WASM2);
    assert!(c.get_pin_proposal().is_none());
    assert_eq!(c.list_pins().len(), 2);
}

// --------------------------------------------------------------- timelock

#[test]
fn first_pin_applies_at_once_and_covers_earlier_dates() {
    let mut c = fresh();
    assert!(c.get_outlayer_config().is_none() && c.get_pin_for_date("2026-10-20".into()).is_none());
    pin(&mut c, WASM);
    assert!(c.get_pin_proposal().is_none());
    let pins = c.list_pins();
    assert_eq!((pins.len(), pins[0].from_ns), (1, U64(0)));
    assert_eq!(c.get_pin_for_date("2020-01-01".into()).unwrap().wasm_hash, WASM, "backfill before the first pin uses it");
    assert!(get_logs().iter().any(|l| l.contains("worker_pinned") && l.contains(r#""previous_wasm_hash":null"#)));
}

#[test]
fn delay_is_fixed_at_init() {
    ctx(OWNER, T_2026_10_20);
    let c = NearBooks::new(acct(OWNER), acct(REPORTER), 7 * 86_400);
    assert_eq!(c.get_pin_delay_secs(), 7 * 86_400);
    assert_eq!(fresh().get_pin_delay_secs(), DELAY);
}

#[test]
#[should_panic(expected = "pin_delay_secs must be at most 365 days")]
fn absurd_delay_is_refused_at_init() {
    ctx(OWNER, T_2026_10_20);
    NearBooks::new(acct(OWNER), acct(REPORTER), MAX_PIN_DELAY_SECS + 1);
}

#[test]
fn repin_cannot_be_applied_before_the_delay() {
    let mut c = fresh();
    pin(&mut c, WASM);
    propose_at(&mut c, OUTLAYER, WASM2, T_2026_10_20);
    assert_eq!(c.get_pin_proposal().unwrap().apply_after_ns, U64((T_2026_10_20 + DELAY) * 1_000_000_000));
    ctx(OWNER, T_2026_10_20 + DELAY - 1);
    let early = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| c.apply_pin()));
    assert!(early.is_err(), "one second early is refused");
    assert_eq!(c.get_outlayer_config().unwrap().wasm_hash, WASM, "the old pin stays in force");
    // a run requested during the window goes to the old worker
    let n = request_at(&mut c, STRANGER, 10, "2026-10-20", B_1020, T_2026_10_20 + DELAY - 1);
    assert_eq!(c.get_pending(n).unwrap().wasm_hash, WASM);
}

#[test]
#[should_panic(expected = "timelock: the proposed pin can be applied from")]
fn early_apply_names_the_time() {
    let mut c = fresh();
    pin(&mut c, WASM);
    propose_at(&mut c, OUTLAYER, WASM2, T_2026_10_20);
    c.apply_pin();
}

#[test]
fn applied_pin_governs_only_dates_that_start_after_it() {
    // Proposed 2026-10-20 12:00, applied 13:00 (DELAY). 2026-10-20 started before: old pin. 2026-10-21 starts after:
    // new pin.
    let mut c = fresh();
    ctx(OWNER, T_2026_10_20);
    c.set_earliest_date("2026-10-01".into());
    pin(&mut c, WASM);
    let applied = repin(&mut c, OUTLAYER, WASM2, T_2026_10_20);
    let day = |d: &str| c.get_pin_for_date(d.into()).unwrap().wasm_hash;
    assert_eq!((day("2026-10-01"), day("2026-10-20"), day("2026-10-21"), day("2027-05-01")), (WASM.into(), WASM.into(), WASM2.into(), WASM2.into()));
    // requests after the apply: today and a backfilled day still go to the old worker
    for date in ["2026-10-20", "2026-10-05"] {
        let n = request_at(&mut c, STRANGER, 10, date, B_1020, applied);
        assert_eq!(c.get_pending(n).unwrap().wasm_hash, WASM, "{}", date);
    }
    let n = request_at(&mut c, STRANGER, 10, "2026-10-21", B_1020 + 1_000, T_2026_10_20 + 86_400);
    assert_eq!(c.get_pending(n).unwrap().wasm_hash, WASM2);
    let receipts = get_created_receipts();
    let to_outlayer = receipts.iter().find(|r| r.receiver_id == acct(OUTLAYER)).unwrap();
    let MockAction::FunctionCallWeight { args, .. } = &to_outlayer.actions[0] else { panic!() };
    assert_eq!(serde_json::from_slice::<Value>(args).unwrap()["source"]["WasmUrl"]["hash"], WASM2);
}

#[test]
fn pending_prediction_date_inside_the_window_keeps_the_old_pin() {
    // A prediction resolves on 2026-10-21. The owner proposes a re-pin at 23:30 on 2026-10-20 with a one-hour
    // timelock, so the earliest apply is 00:30 on 2026-10-21, after that day started. Even when the deciding snapshot
    // is requested after the apply, it is observed by the old worker, and the prediction resolves from it.
    let mut c = fresh();
    ctx(OWNER, T_2026_10_20);
    c.add_prediction("supply up".into(), "supply_total_near".into(), Comparator::Gt, I128(0), "2026-10-21".into());
    pin(&mut c, WASM);
    let late = T_2026_10_20 + 11 * 3_600 + 1_800; // 2026-10-20 23:30
    let applied = repin(&mut c, OUTLAYER, WASM2, late);
    assert_eq!(date_from_unix_secs(applied), "2026-10-21");
    assert_eq!(c.get_pin_for_date("2026-10-21".into()).unwrap().wasm_hash, WASM);
    let noon = T_2026_10_20 + 86_400;
    let n = request_at(&mut c, STRANGER, 10, "2026-10-21", B_1020 + 1_000, noon);
    assert_eq!(c.get_pending(n).unwrap().wasm_hash, WASM);
    let r = deliver_at(&mut c, CONTRACT, n, ok_bytes(&out(n, "2026-10-21", B_1020 + 1_000, (noon - 43_200) * 1_000_000_000 + 7, SUP_1020)), noon);
    assert!(r.starts_with("stored"), "{}", r);
    let Source::Attested { wasm_hash, .. } = c.get_attested_snapshot("2026-10-21".into()).unwrap().source else { panic!() };
    assert_eq!(wasm_hash, WASM);
    ctx(STRANGER, noon);
    assert_eq!(c.resolve_prediction(0).status, Status::Confirmed);
    // the first date that starts after the apply is the first one the new worker observes
    assert_eq!(c.get_pin_for_date("2026-10-22".into()).unwrap().wasm_hash, WASM2);
}

#[test]
fn a_new_proposal_replaces_the_waiting_one_and_restarts_the_clock() {
    let mut c = fresh();
    pin(&mut c, WASM);
    propose_at(&mut c, OUTLAYER, WASM2, T_2026_10_20);
    let third = "2222222222222222222222222222222222222222222222222222222222222222";
    propose_at(&mut c, OUTLAYER, third, T_2026_10_20 + DELAY - 10);
    assert!(get_logs().iter().any(|l| l.contains("pin_proposed") && l.contains(&format!(r#""replaces_wasm_hash":"{}""#, WASM2))));
    ctx(OWNER, T_2026_10_20 + DELAY);
    assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| c.apply_pin())).is_err(), "the clock restarted");
    ctx(OWNER, T_2026_10_20 + 2 * DELAY - 10);
    c.apply_pin();
    assert_eq!(c.get_outlayer_config().unwrap().wasm_hash, third);
}

#[test]
fn owner_can_cancel_a_proposal() {
    let mut c = fresh();
    pin(&mut c, WASM);
    propose_at(&mut c, OUTLAYER, WASM2, T_2026_10_20);
    c.cancel_pin_proposal();
    assert!(c.get_pin_proposal().is_none());
    assert!(get_logs().iter().any(|l| l.contains("pin_proposal_cancelled")));
    ctx(OWNER, T_2026_10_20 + DELAY);
    assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| c.apply_pin())).is_err(), "nothing to apply");
    assert_eq!(c.get_outlayer_config().unwrap().wasm_hash, WASM);
}

#[test]
fn only_the_owner_applies_or_cancels() {
    let mut c = fresh();
    pin(&mut c, WASM);
    propose_at(&mut c, OUTLAYER, WASM2, T_2026_10_20);
    ctx(STRANGER, T_2026_10_20 + DELAY);
    assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| c.apply_pin())).is_err());
    ctx(STRANGER, T_2026_10_20 + DELAY);
    assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| c.cancel_pin_proposal())).is_err());
    assert!(c.get_pin_proposal().is_some());
}

#[test]
fn ownership_can_be_handed_over() {
    let mut c = fresh();
    ctx(OWNER, T_2026_10_20);
    c.transfer_ownership(acct("multisig.testnet"));
    assert!(get_logs().iter().any(|l| l.contains("owner_transferred") && l.contains("multisig.testnet")));
    assert_eq!(c.get_config().0, acct("multisig.testnet"));
    ctx(OWNER, T_2026_10_20);
    assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| c.transfer_ownership(acct(OWNER)))).is_err(), "the old owner lost the role");
    ctx("multisig.testnet", T_2026_10_20);
    c.propose_pin(acct(OUTLAYER), "https://a/b.wasm".into(), WASM.into(), "https://testnet-api.outlayer.ai".into(), None, None);
    assert_eq!(c.get_outlayer_config().unwrap().wasm_hash, WASM);
}

// --------------------------------------------------------- derived metrics

#[test]
fn ratio_is_omitted_when_supply_did_not_grow() {
    // supply 28 days ago above today's: net issuance is negative, the wallets-over-issuance ratio is undefined
    let mut c = fresh();
    pin(&mut c, WASM);
    let n = request(&mut c, STRANGER, 10);
    let o = out28(n, "2026-10-20", B_1020, TS_1020, SUP_0922, B_0922, SUP_1020);
    assert!(deliver(&mut c, n, ok_bytes(&o)).starts_with("stored"));
    let s = c.get_attested_snapshot("2026-10-20".into()).unwrap();
    let get = |k: &str| s.metrics.iter().find(|m| m.key == k).map(|m| m.value.0);
    assert_eq!(get("net_issuance_near_per_day"), Some(-88_650_537_064));
    assert_eq!(get("revenue_wallets_gain_over_net_issuance"), None);
}

#[test]
#[should_panic(expected = "block_height_28d must be below block_height")]
fn lookback_block_must_be_earlier() {
    let mut c = fresh();
    pin(&mut c, WASM);
    testing_env!(VMContextBuilder::new().storage_usage(carried_usage()).clone()
        .current_account_id(acct(CONTRACT)).predecessor_account_id(acct(STRANGER))
        .attached_deposit(NearToken::from_millinear(10).saturating_add(reserve())).prepaid_gas(Gas::from_tgas(300))
        .block_timestamp(T_2026_10_20 * 1_000_000_000).build());
    let _ = c.request_snapshot("2026-10-20".into(), B_1020, B_1020);
}

#[test]
fn midnight_matches_the_calendar() {
    assert_eq!(midnight_ns("2000-01-01"), Some(946_684_800 * 1_000_000_000));
    assert_eq!(midnight_ns("2026-10-20"), Some(1_792_454_400 * 1_000_000_000));
    assert_eq!(midnight_ns("2024-02-29"), Some(1_709_164_800 * 1_000_000_000));
    assert_eq!(midnight_ns("2026-02-29"), None);
    for d in [10_957u64, 11_016, 11_017, 20_000, 20_746, 40_000] {
        assert_eq!(midnight_ns(&date_from_unix_secs(d * 86_400)), Some(d * 86_400 * 1_000_000_000));
    }
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

// ------------------------------------------------------------ keeper budget

const KEEPER: &str = "keeper.testnet";

fn cost() -> NearToken {
    NearToken::from_millinear(10).saturating_add(reserve())
}

/// Pinned contract with KEEPER allowed `cap_millinear` per day and `fund_millinear` in the budget (funded by STRANGER).
fn with_keeper(cap_millinear: u128, fund_millinear: u128) -> NearBooks {
    let mut c = fresh();
    pin(&mut c, WASM);
    ctx(OWNER, T_2026_10_20);
    c.set_keeper(Some(acct(KEEPER)), NearToken::from_millinear(cap_millinear));
    if fund_millinear > 0 {
        fund(&mut c, STRANGER, fund_millinear);
    }
    c
}

fn fund(c: &mut NearBooks, caller: &str, millinear: u128) -> NearToken {
    testing_env!(VMContextBuilder::new().storage_usage(carried_usage()).clone().current_account_id(acct(CONTRACT)).predecessor_account_id(acct(caller)).attached_deposit(NearToken::from_millinear(millinear)).block_timestamp(T_2026_10_20 * 1_000_000_000).build());
    c.fund_keeper()
}

/// `caller` calls keeper_request_snapshot (no deposit, 300 Tgas) for `date` at unix time `now`; returns the nonce.
fn keeper_request_at(c: &mut NearBooks, caller: &str, date: &str, height: u64, now: u64) -> u64 {
    testing_env!(VMContextBuilder::new().storage_usage(carried_usage()).clone()
        .current_account_id(acct(CONTRACT))
        .predecessor_account_id(acct(caller))
        .signer_account_id(acct(caller))
        .prepaid_gas(Gas::from_tgas(300))
        .block_timestamp(now * 1_000_000_000)
        .build());
    let _ = c.keeper_request_snapshot(date.into(), height, b28_for(height));
    c.next_nonce - 1
}

fn keeper_request(c: &mut NearBooks, caller: &str) -> u64 {
    keeper_request_at(c, caller, "2026-10-20", B_1020, T_2026_10_20)
}

#[test]
fn keeper_request_draws_the_deposit_from_the_budget_and_pays_outlayer_from_the_contract() {
    let mut c = with_keeper(100, 100);
    let nonce = keeper_request(&mut c, KEEPER);
    let receipts = get_created_receipts();
    let to_outlayer = receipts.iter().find(|r| r.receiver_id == acct(OUTLAYER)).expect("a receipt to OutLayer");
    let MockAction::FunctionCallWeight { args, attached_deposit, .. } = &to_outlayer.actions[0] else { panic!("not a function call") };
    assert_eq!(*attached_deposit, NearToken::from_millinear(10));
    let a: Value = serde_json::from_slice(args).unwrap();
    assert_eq!(a["payer_account_id"], CONTRACT, "OutLayer's refund comes back to the contract, not to the keeper");
    let p = c.get_pending(nonce).unwrap();
    assert!(p.from_budget && p.requester == acct(KEEPER) && p.storage_reserve == reserve());
    let k = c.get_keeper();
    assert_eq!(k.budget, NearToken::from_millinear(100).saturating_sub(cost()));
    assert_eq!((k.spent_today, k.request_cost, k.day.as_str()), (cost(), cost(), "2026-10-20"));
    assert!(get_logs().iter().any(|l| l.contains("keeper_spent")));
}

#[test]
#[should_panic(expected = "only the keeper can spend the keeper budget")]
fn a_stranger_cannot_spend_the_keeper_budget() {
    let mut c = with_keeper(100, 100);
    keeper_request(&mut c, STRANGER);
}

#[test]
#[should_panic(expected = "only the keeper can spend the keeper budget")]
fn the_owner_cannot_spend_the_keeper_budget() {
    let mut c = with_keeper(100, 100);
    keeper_request(&mut c, OWNER);
}

#[test]
#[should_panic(expected = "only the keeper can spend the keeper budget")]
fn keeper_path_is_off_until_a_keeper_is_set() {
    let mut c = fresh();
    pin(&mut c, WASM);
    fund(&mut c, STRANGER, 100);
    keeper_request(&mut c, KEEPER);
}

#[test]
#[should_panic(expected = "keeper_request_snapshot takes no deposit")]
fn keeper_path_takes_no_deposit() {
    let mut c = with_keeper(100, 100);
    testing_env!(VMContextBuilder::new().storage_usage(carried_usage()).clone().current_account_id(acct(CONTRACT)).predecessor_account_id(acct(KEEPER))
        .attached_deposit(cost()).prepaid_gas(Gas::from_tgas(300)).block_timestamp(T_2026_10_20 * 1_000_000_000).build());
    let _ = c.keeper_request_snapshot("2026-10-20".into(), B_1020, B_0922);
}

#[test]
fn daily_cap_is_enforced_and_resets_the_next_utc_day() {
    // cap 70 milliNEAR = two requests of 30; budget for four
    let mut c = with_keeper(70, 120);
    keeper_request(&mut c, KEEPER);
    keeper_request(&mut c, KEEPER);
    assert_eq!(c.get_keeper().spent_today, cost().saturating_mul(2));
    let third = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| keeper_request(&mut c, KEEPER)));
    assert!(third.is_err(), "a third request on the same day exceeds the cap");
    // the refused call changed nothing
    assert_eq!(c.get_keeper().budget, NearToken::from_millinear(120).saturating_sub(cost().saturating_mul(2)));
    // the next UTC day starts a new allowance
    let next = T_2026_10_20 + 86_400;
    ctx(KEEPER, next);
    assert_eq!(c.get_keeper().spent_today, NearToken::from_yoctonear(0));
    keeper_request_at(&mut c, KEEPER, "2026-10-21", B_1020 + 1_000, next);
    assert_eq!((c.get_keeper().day.as_str(), c.get_keeper().spent_today), ("2026-10-21", cost()));
}

#[test]
#[should_panic(expected = "keeper daily cap reached")]
fn a_cap_below_one_request_allows_nothing() {
    let mut c = with_keeper(29, 100);
    keeper_request(&mut c, KEEPER);
}

#[test]
#[should_panic(expected = "keeper budget too low")]
fn keeper_cannot_spend_more_than_the_budget() {
    let mut c = with_keeper(100, 29);
    keeper_request(&mut c, KEEPER);
}

#[test]
#[should_panic(expected = "daily_cap must be at most 1 NEAR")]
fn owner_cannot_set_a_cap_above_the_hard_limit() {
    let mut c = fresh();
    c.set_keeper(Some(acct(KEEPER)), NearToken::from_millinear(1_001));
}

#[test]
#[should_panic(expected = "only the owner can set the keeper")]
fn only_the_owner_names_the_keeper() {
    let mut c = fresh();
    ctx(STRANGER, T_2026_10_20);
    c.set_keeper(Some(acct(STRANGER)), NearToken::from_millinear(100));
}

#[test]
fn unused_storage_reserve_returns_to_the_budget_not_to_the_keeper() {
    let mut c = with_keeper(100, 100);
    let start = c.get_keeper().budget;
    ctx(STRANGER, T_2026_10_20);
    let before = env::storage_usage();
    let nonce = keeper_request(&mut c, KEEPER);
    assert!(deliver(&mut c, nonce, ok_bytes(&worker_output(nonce))).starts_with("stored"));
    assert!(transfers().is_empty(), "nothing is sent anywhere: {:?}", transfers());
    let staked = NearToken::from_yoctonear(10u128.pow(19) * (env::storage_usage() - before) as u128);
    // budget = start - 0.01 NEAR sent to OutLayer - what the stored snapshot stakes
    assert_eq!(c.get_keeper().budget, start.saturating_sub(NearToken::from_millinear(10)).saturating_sub(staked));
    // the cap counts gross spending: the credited reserve does not reopen today's allowance
    assert_eq!(c.get_keeper().spent_today, cost());
    assert!(get_logs().iter().any(|l| l.contains("storage_settled") && l.contains(r#""to_budget":true"#)));
}

#[test]
fn rejected_keeper_run_returns_the_whole_reserve_to_the_budget() {
    let mut c = with_keeper(100, 100);
    for result in [ok_bytes("null"), PromiseResult::Failed, ok_bytes("garbage")] {
        let start = c.get_keeper().budget;
        let nonce = keeper_request(&mut c, KEEPER);
        assert!(deliver(&mut c, nonce, result).starts_with("rejected"));
        assert!(transfers().is_empty());
        // only the OutLayer part is gone from the ledger (OutLayer refunds its unused part to the contract's free balance)
        assert_eq!(c.get_keeper().budget, start.saturating_sub(NearToken::from_millinear(10)));
        assert_eq!(c.attested_count(), 0);
    }
}

#[test]
fn keeper_snapshot_decides_predictions_like_any_attested_one() {
    let mut c = with_keeper(100, 100);
    ctx(OWNER, T_2026_10_20 - 86_400);
    let id = c.add_prediction("x".into(), "supply_total_near".into(), Comparator::Gt, I128(0), "2026-10-20".into());
    let nonce = keeper_request(&mut c, KEEPER);
    assert!(deliver(&mut c, nonce, ok_bytes(&worker_output(nonce))).starts_with("stored"));
    assert_eq!(c.get_attested_snapshot("2026-10-20".into()).unwrap().submitted_by, acct(KEEPER));
    ctx(KEEPER, T_2026_10_20);
    assert_eq!(c.resolve_prediction(id).status, Status::Confirmed);
}

#[test]
#[should_panic(expected = "an attested snapshot for that date already exists")]
fn keeper_cannot_pay_for_a_date_that_is_already_attested() {
    let mut c = with_keeper(100, 100);
    let nonce = request(&mut c, STRANGER, 10);
    assert!(deliver(&mut c, nonce, ok_bytes(&worker_output(nonce))).starts_with("stored"));
    keeper_request(&mut c, KEEPER);
}

#[test]
fn budget_accounting_fund_and_withdraw() {
    let mut c = with_keeper(100, 0);
    assert_eq!(fund(&mut c, STRANGER, 40), NearToken::from_millinear(40));
    assert_eq!(fund(&mut c, OWNER, 60), NearToken::from_millinear(100));
    assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| fund(&mut c, STRANGER, 0))).is_err(), "zero deposit refused");
    ctx(STRANGER, T_2026_10_20);
    assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| c.withdraw_keeper_budget(NearToken::from_millinear(1)))).is_err(), "only the owner withdraws");
    ctx(OWNER, T_2026_10_20);
    assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| c.withdraw_keeper_budget(NearToken::from_millinear(101)))).is_err(), "not more than the budget");
    ctx(OWNER, T_2026_10_20);
    let _ = c.withdraw_keeper_budget(NearToken::from_millinear(30));
    assert_eq!(transfers(), vec![(acct(OWNER), NearToken::from_millinear(30))]);
    assert_eq!(c.get_keeper().budget, NearToken::from_millinear(70));
}

#[test]
fn public_payable_path_is_unchanged_by_the_keeper() {
    let mut c = with_keeper(100, 100);
    let nonce = request(&mut c, STRANGER, 10);
    assert!(!c.get_pending(nonce).unwrap().from_budget);
    assert!(deliver(&mut c, nonce, ok_bytes(&worker_output(nonce))).starts_with("stored"));
    let t = transfers();
    assert!(t.len() == 1 && t[0].0 == acct(STRANGER), "{:?}", t);
    assert_eq!(c.get_keeper().budget, NearToken::from_millinear(100));
    assert_eq!(c.get_keeper().spent_today, NearToken::from_yoctonear(0));
}
