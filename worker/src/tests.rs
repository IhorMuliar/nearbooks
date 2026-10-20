use super::*;
use std::collections::VecDeque;

/// Canned transport: answers by (url, rpc method/params) with a closure; records every request.
struct Fake<F: FnMut(&str, &Value) -> (u16, String)> {
    answer: F,
    log: VecDeque<(String, Value)>,
}

impl<F: FnMut(&str, &Value) -> (u16, String)> Transport for Fake<F> {
    fn post(&mut self, url: &str, body: &[u8]) -> Result<(u16, Vec<u8>), String> {
        let req: Value = serde_json::from_slice(body).unwrap();
        self.log.push_back((url.to_string(), req.clone()));
        let (status, body) = (self.answer)(url, &req);
        Ok((status, body.into_bytes()))
    }
}

fn view_result(v: &str) -> String {
    let bytes: Vec<String> = format!("\"{}\"", v).bytes().map(|b| b.to_string()).collect();
    format!(r#"{{"jsonrpc":"2.0","id":"nb","result":{{"result":[{}],"logs":[],"block_height":100}}}}"#, bytes.join(","))
}

/// A healthy mainnet: block 100, supply 1.3e33, wallets 1/2/3 NEAR, every token balanced except ZEC (deficit 10 of 1000).
fn healthy(_url: &str, req: &Value) -> (u16, String) {
    let method = req["method"].as_str().unwrap();
    let p = &req["params"];
    let body = match method {
        // block 100 is the first block of 2026-10-20 (midnight = 1792454400 s); block 99 ends 2026-10-19
        "block" if p["block_id"] == 100 => r#"{"jsonrpc":"2.0","id":"nb","result":{"header":{"height":100,"prev_hash":"PREV99","timestamp":1792454400500000000,"timestamp_nanosec":"1792454400500000000","total_supply":"1309481353972991492107702771388774"}}}"#.to_string(),
        "block" if p["block_id"] == "PREV99" => r#"{"jsonrpc":"2.0","id":"nb","result":{"header":{"height":99,"prev_hash":"PREV98","timestamp":1792454399400000000,"timestamp_nanosec":"1792454399400000000","total_supply":"1"}}}"#.to_string(),
        "query" if p["request_type"] == "view_account" => {
            assert_eq!(p["block_id"], 100, "every query must pin the block height");
            let amount = match p["account_id"].as_str().unwrap() {
                "fefundsadmin.sputnik-dao.near" => "1000000000000000000000000",
                "buybacks.multisignature.near" => "2000000000000000000000000",
                "1csfundsadmin.sputnik-dao.near" => "3000000000000000000000000",
                other => panic!("unexpected account {}", other),
            };
            format!(r#"{{"jsonrpc":"2.0","id":"nb","result":{{"amount":"{}","locked":"0"}}}}"#, amount)
        }
        "query" => {
            assert_eq!(p["block_id"], 100);
            let args = String::from_utf8(base64_decode(p["args_base64"].as_str().unwrap())).unwrap();
            let args: Value = serde_json::from_str(&args).unwrap();
            match p["method_name"].as_str().unwrap() {
                "mt_supply" => view_result(if args["token_id"] == "nep141:zec.omft.near" { "1000" } else { "500" }),
                "ft_balance_of" => {
                    assert_eq!(args["account_id"], "intents.near");
                    view_result(if p["account_id"] == "zec.omft.near" { "990" } else { "500" })
                }
                m => panic!("unexpected method {}", m),
            }
        }
        m => panic!("unexpected rpc {}", m),
    };
    (200, body)
}

fn base64_decode(s: &str) -> Vec<u8> {
    const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut bits = 0u32;
    let mut n = 0;
    let mut out = vec![];
    for c in s.bytes().filter(|&c| c != b'=') {
        bits = bits << 6 | T.iter().position(|&t| t == c).unwrap() as u32;
        n += 6;
        if n >= 8 {
            n -= 8;
            out.push((bits >> n) as u8);
        }
    }
    out
}

const INPUT: &str = r#"{"n":"7","d":"2026-10-20","b":"100"}"#;

fn ctx(input: &str) -> Ctx {
    Ctx { input: input.into(), request_id: "5200".into(), elapsed_ms: Box::new(|| 1234), sleep: Box::new(|_| ()) }
}

#[test]
fn full_run_produces_compact_sorted_output_under_the_limit() {
    let mut t = Fake { answer: healthy, log: VecDeque::new() };
    let out = run(&mut t, &ctx(INPUT)).unwrap();
    let v: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v["n"], "7");
    assert_eq!(v["rid"], "5200");
    assert_eq!(v["h"], "100");
    assert_eq!(v["t"], "1792454400500000000");
    assert_eq!(v["d"], "2026-10-20");
    assert_eq!(v["sup"], "1309481353972991492107702771388774");
    assert_eq!((v["fe"].as_str(), v["bb"].as_str(), v["cs"].as_str()), (Some("1000000000000000000000000"), Some("2000000000000000000000000"), Some("3000000000000000000000000")));
    assert_eq!((v["rk"].as_str(), v["rc"].as_str(), v["rd"].as_str(), v["rm"].as_str(), v["rt"].as_str()), (Some("8"), Some("8"), Some("1"), Some("10000"), Some("ZEC")));
    assert_eq!(v["fb"], "0");
    assert_eq!(v["v"], "2");
    // every value is a string, keys are sorted, and re-serialising gives the same bytes (canonical form)
    assert!(v.as_object().unwrap().values().all(Value::is_string));
    assert_eq!(serde_json::to_string(&v).unwrap(), out);
    // 2 blocks + 3 accounts + 8 x 2 token views
    assert_eq!(t.log.len(), 21);
    // worst case: every number at its maximum plausible width still fits the ~900-byte on-chain output
    assert!(out.len() < 600, "output is {} bytes", out.len());
}

#[test]
fn hash_covers_raw_responses_in_order() {
    let mut a = Fake { answer: healthy, log: VecDeque::new() };
    let out_a: Value = serde_json::from_str(&run(&mut a, &ctx(INPUT)).unwrap()).unwrap();
    // recompute: sha256 over the concatenated bodies the fake returned, in call order
    let mut h = Sha256::new();
    for (url, req) in a.log.iter() {
        h.update(healthy(url, req).1.as_bytes());
    }
    assert_eq!(out_a["sha"].as_str().unwrap(), hex(&h.finalize()));
    // a different supply in the header changes the hash
    let mut b = Fake {
        answer: |u: &str, r: &Value| {
            let (s, body) = healthy(u, r);
            (s, body.replace("1309481353972991492107702771388774", "1309481353972991492107702771388775"))
        },
        log: VecDeque::new(),
    };
    let out_b: Value = serde_json::from_str(&run(&mut b, &ctx(INPUT)).unwrap()).unwrap();
    assert_ne!(out_a["sha"], out_b["sha"]);
}

#[test]
fn falls_back_to_second_rpc_and_sticks_to_it() {
    let mut t = Fake {
        answer: |u: &str, r: &Value| if u == RPC_URLS[0] { (429, "Too Many Requests".into()) } else { healthy(u, r) },
        log: VecDeque::new(),
    };
    let v: Value = serde_json::from_str(&run(&mut t, &ctx(INPUT)).unwrap()).unwrap();
    assert_eq!(v["fb"], "21");
    // only the very first call tried the primary; afterwards the fallback is sticky
    assert_eq!(t.log.iter().filter(|(u, _)| u == RPC_URLS[0]).count(), 1);
}

#[test]
fn rpc_error_object_triggers_fallback() {
    let mut t = Fake {
        answer: |u: &str, r: &Value| {
            if u == RPC_URLS[0] && r["method"] == "block" && r["params"]["block_id"] == 100 {
                (200, r#"{"jsonrpc":"2.0","id":"nb","error":{"name":"HANDLER_ERROR","cause":{"name":"UNKNOWN_BLOCK"}}}"#.into())
            } else {
                healthy(u, r)
            }
        },
        log: VecDeque::new(),
    };
    let v: Value = serde_json::from_str(&run(&mut t, &ctx(INPUT)).unwrap()).unwrap();
    assert_eq!(v["h"], "100");
    assert_eq!(v["fb"], "21");
}

#[test]
fn unreadable_token_is_skipped_not_fatal() {
    let mut t = Fake {
        answer: |u: &str, r: &Value| {
            if r["params"]["account_id"] == "qtc.omft.near" {
                (200, r#"{"jsonrpc":"2.0","id":"nb","result":{"error":"wasm execution failed","logs":[]}}"#.into())
            } else {
                healthy(u, r)
            }
        },
        log: VecDeque::new(),
    };
    let v: Value = serde_json::from_str(&run(&mut t, &ctx(INPUT)).unwrap()).unwrap();
    assert_eq!((v["rk"].as_str(), v["rc"].as_str()), (Some("8"), Some("7")));
}

#[test]
fn total_outage_is_an_error() {
    let mut t = Fake { answer: |_: &str, _: &Value| (503, String::new()), log: VecDeque::new() };
    let e = run(&mut t, &ctx(INPUT)).unwrap_err();
    assert!(e.contains("HTTP 503"), "{}", e);
    assert_eq!(t.log.len(), MAX_ATTEMPTS as usize, "bounded retries, then give up");
}

#[test]
fn rate_limits_back_off_then_succeed() {
    use std::cell::{Cell, RefCell};
    use std::rc::Rc;
    let left = Cell::new(3); // the first three requests get HTTP 429 from both endpoints
    let mut t = Fake {
        answer: move |u: &str, r: &Value| if left.get() > 0 { left.set(left.get() - 1); (429, String::new()) } else { healthy(u, r) },
        log: VecDeque::new(),
    };
    let slept = Rc::new(RefCell::new(vec![]));
    let s2 = slept.clone();
    let c = Ctx { input: INPUT.into(), request_id: "1".into(), elapsed_ms: Box::new(|| 0), sleep: Box::new(move |ms| s2.borrow_mut().push(ms)) };
    let v: Value = serde_json::from_str(&run(&mut t, &c).unwrap()).unwrap();
    assert_eq!(v["h"], "100");
    assert_eq!(*slept.borrow(), vec![250, 500, 1000], "exponential backoff between attempts");
}

#[test]
fn block_that_is_not_the_first_of_the_day_is_refused() {
    // D = 2026-10-19: block 100 is after that day's first block (its predecessor is already past midnight of 10-19)
    let mut t = Fake { answer: healthy, log: VecDeque::new() };
    let e = run(&mut t, &ctx(r#"{"n":"7","d":"2026-10-19","b":"100"}"#)).unwrap_err();
    assert!(e.contains("is not the first block of 2026-10-19"), "{}", e);
    // D = 2026-10-21: block 100 is before that midnight
    let e = run(&mut Fake { answer: healthy, log: VecDeque::new() }, &ctx(r#"{"n":"7","d":"2026-10-21","b":"100"}"#)).unwrap_err();
    assert!(e.contains("is not the first block"), "{}", e);
    assert_eq!(t.log.len(), 2, "nothing else is read once the block is refused");
}

#[test]
fn every_read_is_pinned_to_the_requested_block() {
    let mut t = Fake { answer: healthy, log: VecDeque::new() };
    run(&mut t, &ctx(INPUT)).unwrap();
    for (_, req) in t.log.iter().filter(|(_, r)| r["method"] == "query") {
        assert_eq!(req["params"]["block_id"], 100);
        assert!(req["params"].get("finality").is_none());
    }
}

#[test]
fn midnight_matches_known_values() {
    assert_eq!(midnight_ns("1970-01-01"), Some(0));
    assert_eq!(midnight_ns("2026-10-20"), Some(1_792_454_400 * 1_000_000_000));
    assert_eq!(midnight_ns("2024-02-29"), Some(1_709_164_800 * 1_000_000_000));
    assert_eq!(midnight_ns("2026-02-29"), None);
    assert_eq!(midnight_ns("2026-1-01"), None);
}

#[test]
fn request_input_is_validated() {
    assert_eq!(parse_request(INPUT).unwrap(), Request { nonce: "7".into(), date: "2026-10-20".into(), height: 100 });
    assert!(parse_request("").is_err());
    assert!(parse_request(r#"{"n":7,"d":"2026-10-20","b":"100"}"#).is_err());
    assert!(parse_request(r#"{"n":"4\"2","d":"2026-10-20","b":"100"}"#).is_err());
    assert!(parse_request(r#"{"n":"1","d":"2026-13-01","b":"100"}"#).is_err());
    assert!(parse_request(r#"{"n":"1","d":"2026-10-20","b":"-1"}"#).is_err());
    assert!(parse_request(r#"{"n":"1","d":"2026-10-20"}"#).is_err());
}

#[test]
fn base64_matches_reference() {
    assert_eq!(b64(b""), "");
    assert_eq!(b64(b"f"), "Zg==");
    assert_eq!(b64(b"fo"), "Zm8=");
    assert_eq!(b64(b"foo"), "Zm9v");
    assert_eq!(b64(br#"{"account_id":"intents.near"}"#), "eyJhY2NvdW50X2lkIjoiaW50ZW50cy5uZWFyIn0=");
}
