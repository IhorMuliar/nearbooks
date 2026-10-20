"""Rank the NEP-141 tokens held by intents.near by USD value (stdlib only, network, a few minutes).

    python3 -I worker/scripts/rank_intents_tokens.py [TOP_N]

Value = intents.near mt_supply("nep141:<token>") / 10^decimals x price, with decimals and price from the 1Click
token list (https://1click.chaindefuser.com/v0/tokens). The worker's TOKENS list is the top 8 of this ranking,
taken on 2026-10-20 (wNEAR, ZEC, USDC.e, WBTC.e, nBTC, QTC, ETH, USDT.e). Re-run to see whether it has drifted.
"""
import base64, json, sys, time, urllib.request

RPCS = ["https://free.rpc.fastnear.com", "https://rpc.mainnet.near.org"]


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def mt_supply(token_id):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "query", "params": {
        "request_type": "call_function", "finality": "final", "account_id": "intents.near", "method_name": "mt_supply",
        "args_base64": base64.b64encode(json.dumps({"token_id": token_id}).encode()).decode()}}).encode()
    for attempt in range(6):
        for url in RPCS:
            try:
                req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    res = json.load(r)["result"]
                return None if "error" in res else int(json.loads(bytes(res["result"])))
            except Exception:
                pass
        time.sleep(2 * attempt + 1)  # both endpoints rate-limited: back off
    return None


def main():
    top = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    rows = []
    for t in get("https://1click.chaindefuser.com/v0/tokens"):
        if not t["assetId"].startswith("nep141:") or not t.get("price"):
            continue
        s = mt_supply(t["assetId"])
        if s:
            rows.append((s / 10 ** t["decimals"] * t["price"], t["assetId"][7:], t["symbol"], t["blockchain"]))
        time.sleep(0.3)
    rows.sort(reverse=True)
    for usd, contract, sym, chain in rows[:top]:
        print(f"{usd:>16,.0f}  {sym:<8} {chain:<6} {contract}")


if __name__ == "__main__":
    main()
