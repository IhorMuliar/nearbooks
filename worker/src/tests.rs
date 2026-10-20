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
        "block" => r#"{"jsonrpc":"2.0","id":"nb","result":{"header":{"height":100,"timestamp":1792497600000000000,"timestamp_nanosec":"1792497600000000000","total_supply":"1309481353972991492107702771388774"}}}"#.to_string(),
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

fn ctx(input: &str) -> Ctx {
    Ctx { input: input.into(), request_id: "5200".into(), elapsed_ms: Box::new(|| 1234) }
}

#[test]
fn full_run_produces_compact_sorted_output_under_the_limit() {
    let mut t = Fake { answer: healthy, log: VecDeque::new() };
    let out = run(&mut t, &ctx(r#"{"n":"7"}"#)).unwrap();
    let v: Value = serde_json::from_str(&out).unwrap();
    assert_eq!(v["n"], "7");
    assert_eq!(v["rid"], "5200");
    assert_eq!(v["h"], "100");
    assert_eq!(v["t"], "1792497600000000000");
    assert_eq!(v["sup"], "1309481353972991492107702771388774");
    assert_eq!((v["fe"].as_str(), v["bb"].as_str(), v["cs"].as_str()), (Some("1000000000000000000000000"), Some("2000000000000000000000000"), Some("3000000000000000000000000")));
    assert_eq!((v["rk"].as_str(), v["rc"].as_str(), v["rd"].as_str(), v["rm"].as_str(), v["rt"].as_str()), (Some("8"), Some("8"), Some("1"), Some("10000"), Some("ZEC")));
    assert_eq!(v["fb"], "0");
    assert_eq!(v["v"], "1");
    // every value is a string, keys are sorted, and re-serialising gives the same bytes (canonical form)
    assert!(v.as_object().unwrap().values().all(Value::is_string));
    assert_eq!(serde_json::to_string(&v).unwrap(), out);
    // 1 block + 3 accounts + 8 x 2 token views
    assert_eq!(t.log.len(), 20);
    // worst case: every number at its maximum plausible width still fits the ~900-byte on-chain output
    assert!(out.len() < 600, "output is {} bytes", out.len());
}

#[test]
fn hash_covers_raw_responses_in_order() {
    let mut a = Fake { answer: healthy, log: VecDeque::new() };
    let out_a: Value = serde_json::from_str(&run(&mut a, &ctx("")).unwrap()).unwrap();
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
    let out_b: Value = serde_json::from_str(&run(&mut b, &ctx("")).unwrap()).unwrap();
    assert_ne!(out_a["sha"], out_b["sha"]);
}

#[test]
fn falls_back_to_second_rpc_and_sticks_to_it() {
    let mut t = Fake {
        answer: |u: &str, r: &Value| if u == RPC_URLS[0] { (429, "Too Many Requests".into()) } else { healthy(u, r) },
        log: VecDeque::new(),
    };
    let v: Value = serde_json::from_str(&run(&mut t, &ctx("")).unwrap()).unwrap();
    assert_eq!(v["fb"], "20");
    // only the very first call tried the primary; afterwards the fallback is sticky
    assert_eq!(t.log.iter().filter(|(u, _)| u == RPC_URLS[0]).count(), 1);
}

#[test]
fn rpc_error_object_triggers_fallback() {
    let mut t = Fake {
        answer: |u: &str, r: &Value| {
            if u == RPC_URLS[0] && r["method"] == "block" {
                (200, r#"{"jsonrpc":"2.0","id":"nb","error":{"name":"HANDLER_ERROR","cause":{"name":"UNKNOWN_BLOCK"}}}"#.into())
            } else {
                healthy(u, r)
            }
        },
        log: VecDeque::new(),
    };
    let v: Value = serde_json::from_str(&run(&mut t, &ctx("")).unwrap()).unwrap();
    assert_eq!(v["h"], "100");
    assert_eq!(v["fb"], "20");
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
    let v: Value = serde_json::from_str(&run(&mut t, &ctx("")).unwrap()).unwrap();
    assert_eq!((v["rk"].as_str(), v["rc"].as_str()), (Some("8"), Some("7")));
}

#[test]
fn total_outage_is_an_error() {
    let mut t = Fake { answer: |_: &str, _: &Value| (503, String::new()), log: VecDeque::new() };
    let e = run(&mut t, &ctx("")).unwrap_err();
    assert!(e.contains("HTTP 503"), "{}", e);
    assert_eq!(t.log.len(), 4, "two rounds over both endpoints, then give up");
}

#[test]
fn nonce_input_is_validated() {
    assert_eq!(parse_nonce("").unwrap(), "0");
    assert_eq!(parse_nonce(r#"{"n":"42"}"#).unwrap(), "42");
    assert!(parse_nonce(r#"{"n":42}"#).is_err());
    assert!(parse_nonce(r#"{"n":"4\"2"}"#).is_err());
    assert!(parse_nonce("not json").is_err());
}

#[test]
fn base64_matches_reference() {
    assert_eq!(b64(b""), "");
    assert_eq!(b64(b"f"), "Zg==");
    assert_eq!(b64(b"fo"), "Zm8=");
    assert_eq!(b64(b"foo"), "Zm9v");
    assert_eq!(b64(br#"{"account_id":"intents.near"}"#), "eyJhY2NvdW50X2lkIjoiaW50ZW50cy5uZWFyIn0=");
}
