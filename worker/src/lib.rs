//! NEAR Books OutLayer worker: reads live NEAR mainnet state over HTTPS JSON-RPC and prints one compact JSON
//! object for the `nearbooks` contract.
//!
//! Everything is read at ONE final block (the `block` call fixes the height, every later query passes
//! `block_id`), so the numbers are mutually consistent:
//! * block height, timestamp and `total_supply` from the block header,
//! * liquid balances (`view_account.amount`) of the three NEAR Intents revenue wallets,
//! * an Intents reserves check: for each token in [`TOKENS`], `intents.near mt_supply("nep141:<token>")`
//!   against `<token>.ft_balance_of("intents.near")`. A token is in deficit when the ledger owes more than the
//!   token contract says intents.near holds.
//!
//! Output (all values are strings, keys sorted, compact; about 370 bytes, OutLayer's on-chain limit is ~900):
//! `{"bb","cs","fe"` wallet balances in yoctoNEAR, `"fb"` responses served by the fallback RPC, `"h"` block height,
//! `"ms"` wall time, `"n"` the caller's nonce echoed back, `"rc"` tokens checked, `"rd"` tokens in deficit, `"rk"`
//! tokens configured, `"rm"` max deficit in parts per million of mt_supply, `"rt"` symbol with the max deficit,
//! `"rid"` OutLayer request id, `"sha"` sha256 of the concatenated raw RPC response bodies in call order,
//! `"sup"` total supply in yoctoNEAR, `"t"` block timestamp (ns), `"v"` format version`}`.
//!
//! The HTTP transport is a trait so the logic runs under `cargo test` on the host with canned responses.

use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

pub const FORMAT_VERSION: &str = "1";
pub const RPC_URLS: [&str; 2] = ["https://free.rpc.fastnear.com", "https://rpc.mainnet.near.org"];
pub const INTENTS: &str = "intents.near";

/// (key in the output, account) for the revenue wallets.
pub const WALLETS: [(&str, &str); 3] = [
    ("fe", "fefundsadmin.sputnik-dao.near"),
    ("bb", "buybacks.multisignature.near"),
    ("cs", "1csfundsadmin.sputnik-dao.near"),
];

/// Top 8 NEP-141 tokens held by intents.near by USD value on 2026-10-20 (mt_supply x 1Click price;
/// `worker/scripts/rank_intents_tokens.py` reproduces the ranking). (short symbol, token contract).
pub const TOKENS: [(&str, &str); 8] = [
    ("wNEAR", "wrap.near"),
    ("ZEC", "zec.omft.near"),
    ("USDC.e", "eth-0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48.omft.near"),
    ("WBTC.e", "eth-0x2260fac5e5542a773aa44fbcfedf7c193bc2c599.omft.near"),
    ("nBTC", "nbtc.bridge.near"),
    ("QTC", "qtc.omft.near"),
    ("ETH", "eth.omft.near"),
    ("USDT.e", "eth-0xdac17f958d2ee523a2206206994597c13d831ec7.omft.near"),
];

/// Minimal HTTPS POST. Returns (status, body).
pub trait Transport {
    fn post(&mut self, url: &str, body: &[u8]) -> Result<(u16, Vec<u8>), String>;
}

/// JSON-RPC client with a sticky primary endpoint and fallback. Keeps every accepted raw body for the hash.
pub struct Rpc<'a, T: Transport> {
    transport: &'a mut T,
    primary: usize,
    hasher: Sha256,
    pub calls: u32,
    pub fallback_hits: u32,
}

impl<'a, T: Transport> Rpc<'a, T> {
    pub fn new(transport: &'a mut T) -> Self {
        Self { transport, primary: 0, hasher: Sha256::new(), calls: 0, fallback_hits: 0 }
    }

    /// Calls `method`; on HTTP errors, unparsable bodies or top-level JSON-RPC errors it tries the other endpoint
    /// (two rounds over both). Returns the `result` object.
    pub fn call(&mut self, method: &str, params: Value) -> Result<Value, String> {
        let body = json!({"jsonrpc": "2.0", "id": "nb", "method": method, "params": params}).to_string();
        let mut last = String::new();
        for attempt in 0..(2 * RPC_URLS.len()) {
            let idx = (self.primary + attempt) % RPC_URLS.len();
            match self.transport.post(RPC_URLS[idx], body.as_bytes()) {
                Ok((200, raw)) => match serde_json::from_slice::<Value>(&raw) {
                    Ok(mut v) => {
                        if let Some(result) = v.get_mut("result").map(Value::take) {
                            self.hasher.update(&raw);
                            self.calls += 1;
                            if idx != 0 {
                                self.fallback_hits += 1;
                            }
                            self.primary = idx;
                            return Ok(result);
                        }
                        last = format!("{} {}: {}", RPC_URLS[idx], method, short(&v.get("error").cloned().unwrap_or(Value::Null).to_string()));
                    }
                    Err(e) => last = format!("{} {}: bad json: {}", RPC_URLS[idx], method, e),
                },
                Ok((status, _)) => last = format!("{} {}: HTTP {}", RPC_URLS[idx], method, status),
                Err(e) => last = format!("{} {}: {}", RPC_URLS[idx], method, e),
            }
        }
        Err(last)
    }

    /// `call_function` view at `height`, returning the method's JSON return value.
    pub fn view(&mut self, height: u64, account: &str, method: &str, args: &Value) -> Result<Value, String> {
        let r = self.call(
            "query",
            json!({"request_type": "call_function", "block_id": height, "account_id": account,
                   "method_name": method, "args_base64": b64(args.to_string().as_bytes())}),
        )?;
        if let Some(e) = r.get("error") {
            return Err(format!("{}.{}: {}", account, method, short(&e.to_string())));
        }
        let bytes: Vec<u8> = r
            .get("result")
            .and_then(Value::as_array)
            .ok_or_else(|| format!("{}.{}: no result bytes", account, method))?
            .iter()
            .map(|b| b.as_u64().and_then(|x| u8::try_from(x).ok()).ok_or("bad byte"))
            .collect::<Result<_, _>>()?;
        serde_json::from_slice(&bytes).map_err(|e| format!("{}.{}: {}", account, method, e))
    }

    pub fn digest_hex(&self) -> String {
        hex(&self.hasher.clone().finalize())
    }
}

/// Context the OutLayer host passes in: the caller's input and the request id (`NEAR_REQUEST_ID`).
pub struct Ctx {
    pub input: String,
    pub request_id: String,
    pub elapsed_ms: Box<dyn Fn() -> u64>,
}

/// Runs the whole job and returns the output JSON (compact, sorted keys).
pub fn run<T: Transport>(transport: &mut T, ctx: &Ctx) -> Result<String, String> {
    let nonce = parse_nonce(&ctx.input)?;
    let mut rpc = Rpc::new(transport);

    let block = rpc.call("block", json!({"finality": "final"}))?;
    let header = block.get("header").ok_or("block: no header")?;
    let height = header.get("height").and_then(Value::as_u64).ok_or("block: no height")?;
    let ts = header.get("timestamp_nanosec").and_then(Value::as_str).map(str::to_string)
        .or_else(|| header.get("timestamp").and_then(Value::as_u64).map(|t| t.to_string()))
        .ok_or("block: no timestamp")?;
    let supply = digits(header.get("total_supply"), "total_supply")?;

    let mut out = Map::new();
    for (key, account) in WALLETS {
        let acc = rpc.call("query", json!({"request_type": "view_account", "block_id": height, "account_id": account}))?;
        out.insert(key.into(), Value::String(digits(acc.get("amount"), account)?));
    }

    let (mut checked, mut in_deficit, mut max_ppm, mut max_sym) = (0u32, 0u32, 0u128, "");
    for (sym, token) in TOKENS {
        // A token that cannot be read is skipped (rc < rk shows it); the job still reports the rest.
        let supply_v = rpc.view(height, INTENTS, "mt_supply", &json!({"token_id": format!("nep141:{}", token)}));
        let bal_v = rpc.view(height, token, "ft_balance_of", &json!({"account_id": INTENTS}));
        let (Ok(s), Ok(b)) = (supply_v, bal_v) else { continue };
        let (Some(s), Some(b)) = (u128_str(&s), u128_str(&b)) else { continue };
        checked += 1;
        if s > b {
            in_deficit += 1;
            let ppm = (s - b).saturating_mul(1_000_000) / s.max(1);
            let ppm = ppm.max(1); // any deficit is at least 1 ppm so it never rounds to "no deficit"
            if ppm > max_ppm {
                max_ppm = ppm;
                max_sym = sym;
            }
        }
    }

    out.insert("fb".into(), rpc.fallback_hits.to_string().into());
    out.insert("h".into(), height.to_string().into());
    out.insert("ms".into(), (ctx.elapsed_ms)().to_string().into());
    out.insert("n".into(), nonce.into());
    out.insert("rc".into(), checked.to_string().into());
    out.insert("rd".into(), in_deficit.to_string().into());
    out.insert("rk".into(), TOKENS.len().to_string().into());
    out.insert("rm".into(), max_ppm.to_string().into());
    out.insert("rt".into(), max_sym.into());
    out.insert("rid".into(), ctx.request_id.clone().into());
    out.insert("sha".into(), rpc.digest_hex().into());
    out.insert("sup".into(), supply.into());
    out.insert("t".into(), ts.into());
    out.insert("v".into(), FORMAT_VERSION.into());
    // serde_json's default Map is a BTreeMap, so keys come out sorted and the bytes are canonical.
    Ok(Value::Object(out).to_string())
}

/// Input is `{"n":"<decimal nonce>"}` (the contract's request nonce); empty input means nonce "0" (local runs).
pub fn parse_nonce(input: &str) -> Result<String, String> {
    if input.trim().is_empty() {
        return Ok("0".into());
    }
    let v: Value = serde_json::from_str(input).map_err(|e| format!("input: {}", e))?;
    let n = v.get("n").and_then(Value::as_str).ok_or("input: missing n")?;
    if n.is_empty() || n.len() > 20 || !n.bytes().all(|c| c.is_ascii_digit()) {
        return Err("input: n must be a decimal string".into());
    }
    Ok(n.into())
}

fn digits(v: Option<&Value>, what: &str) -> Result<String, String> {
    let s = v.and_then(Value::as_str).ok_or_else(|| format!("{}: missing", what))?;
    if s.is_empty() || !s.bytes().all(|c| c.is_ascii_digit()) {
        return Err(format!("{}: not a decimal string", what));
    }
    Ok(s.into())
}

fn u128_str(v: &Value) -> Option<u128> {
    v.as_str()?.parse().ok()
}

fn short(s: &str) -> String {
    s.chars().take(160).collect()
}

pub fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{:02x}", x)).collect()
}

pub fn b64(input: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity((input.len() + 2) / 3 * 4);
    for chunk in input.chunks(3) {
        let n = (chunk[0] as u32) << 16 | (*chunk.get(1).unwrap_or(&0) as u32) << 8 | *chunk.get(2).unwrap_or(&0) as u32;
        for i in 0..4 {
            if i <= chunk.len() {
                out.push(T[(n >> (18 - 6 * i) & 63) as usize] as char);
            } else {
                out.push('=');
            }
        }
    }
    out
}

#[cfg(test)]
mod tests;
