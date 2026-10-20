"""Show on testnet what the keeper's function-call key cannot do. Every probe is rejected by the network before
execution, so none of them costs anything.

    python3 -I keeper/probe.py CONTRACT KEEPER_ACCOUNT KEY.json
"""
import base64, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keeper  # noqa: E402

contract, account, keyfile = sys.argv[1:4]
with open(keyfile) as f:
    key = keeper.Key(json.load(f)["private_key"])
rpc = keeper.Rpc(keeper.TESTNET_RPCS)


def try_tx(what, receiver, method=None, deposit=0, transfer=None):
    ak = rpc.call("query", {"request_type": "view_access_key", "finality": "final", "account_id": account, "public_key": key.public_str})
    bh = keeper.b58decode(rpc.call("block", {"finality": "final"})["header"]["hash"])
    if transfer is None:
        tx = keeper.function_call_tx(account, key.public, ak["nonce"] + 1, receiver, bh, method, b"{}", 30 * 10**12, deposit)
    else:  # Transaction with one Transfer action (enum index 3)
        tx = (keeper._str(account) + b"\x00" + key.public + (ak["nonce"] + 1).to_bytes(8, "little") + keeper._str(receiver) + bh
              + keeper._u32(1) + b"\x03" + transfer.to_bytes(16, "little"))
    b64, h = keeper.signed_tx(key, tx)
    try:
        r = rpc.call("broadcast_tx_commit", [b64])
        print(f"UNEXPECTED  {what}: accepted, tx {h}, status {json.dumps(r.get('status'))[:200]}")
        return False
    except keeper.RpcError as e:
        err = json.loads(str(e)) if str(e).startswith("{") else {"message": str(e)}
        detail = json.dumps(err.get("data") or err.get("cause") or err)[:220]
        print(f"REFUSED  {what}: {detail}")
        return True


ok = all([
    try_tx("payable request_snapshot with 0.03 NEAR attached", contract, "request_snapshot", deposit=3 * 10**22),
    try_tx("fund_keeper (method not on the key)", contract, "fund_keeper"),
    try_tx("a call to another contract", "outlayer.testnet", "request_execution"),
    try_tx("a plain transfer of 0.1 NEAR", "okf-kill-1792497882.testnet", transfer=10**23),
])
sys.exit(0 if ok else 1)
