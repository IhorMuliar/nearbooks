"""Exact (yoctoNEAR) ledger for one wallet, built from FastNear transfer rows and an RPC balance.

The FastNear rows carry the wallet's own start/end-of-block balance next to each transfer. That lets the
balance be split, without any tolerance, into
    external revenue in + internal transfers in (by source) - outflows (by destination) + non-transfer moves
where "non-transfer moves" are balance changes no transfer row accounts for (transaction fees the wallet
paid when it signed its own transactions, storage refunds, ...). Pure functions; no network.
"""
import collections
from common import YOCTO, day_of


def _sorted(rows):
    return sorted(rows, key=lambda r: (int(r["block_height"]), int(r["transfer_index"])))


def wallet_ledger(rows, wallet, internal_wallets, balance_yocto):
    """rows: FastNear transfer rows for `wallet` (any asset). Returns integer-yocto components and a daily timeline."""
    near = _sorted(r for r in rows if r["account_id"] == wallet and r["asset_id"] == "native:near")
    ext = internal = out = 0
    internal_by_src, out_by_dst = collections.Counter(), collections.Counter()
    blocks = collections.OrderedDict()
    for r in near:
        blocks.setdefault(int(r["block_height"]), []).append(r)
    opening = int(near[0]["start_of_block_balance"] or 0) if near else 0
    prev_eob, steps, timeline, eob_by_height = opening, [], {}, []
    for h, rs in blocks.items():
        amt = sum(int(r["amount"]) for r in rs)
        for r in rs:
            a, other = int(r["amount"]), r["other_account_id"]
            if a > 0 and other in internal_wallets:
                internal += a; internal_by_src[other] += a
            elif a > 0:
                ext += a
            else:
                out -= a; out_by_dst[other] += -a
        sob = int(rs[0]["start_of_block_balance"]) if rs[0]["start_of_block_balance"] else prev_eob
        eob = int(rs[-1]["end_of_block_balance"])
        # balance change in and before this block that no row in this block explains. An outflow the wallet signs
        # itself is debited when its transaction converts to a receipt (a block or so before the row), so the
        # "between rows" and "within block" parts are combined per block.
        step = (sob - prev_eob) + (eob - sob - amt)
        if step:
            steps.append({"height": h, "day": day_of(int(rs[0]["block_timestamp"]) / 1e9), "yocto": step})
        prev_eob = eob
        eob_by_height.append((h, eob))
        timeline[day_of(int(rs[-1]["block_timestamp"]) / 1e9)] = {"external_in": ext, "internal_in": internal, "out": out, "eob": eob}
    tail = balance_yocto - prev_eob                      # moves after the last transfer row, up to the RPC height
    nontransfer = sum(s["yocto"] for s in steps) + tail
    residual = balance_yocto - opening - (ext + internal - out)
    return {"opening": opening, "external_in": ext, "internal_in": internal, "internal_by_source": dict(internal_by_src),
            "out": out, "out_by_destination": dict(out_by_dst), "nontransfer_steps": steps, "tail_after_last_row": tail,
            "nontransfer_total": nontransfer, "balance": balance_yocto, "residual": residual,
            "unexplained": residual - nontransfer, "timeline": timeline, "eob_by_height": eob_by_height, "rows": len(near)}


def balance_at(ledger, height):
    """Wallet balance (FastNear end-of-block column) at the last transfer row at or before `height`."""
    best = None
    for h, eob in ledger["eob_by_height"]:
        if h <= height:
            best = eob
    return best


def cumulative_at(ledger, day, key):
    """Running total of `key` at the end of UTC `day` (last timeline entry on or before it)."""
    v = 0
    for d in sorted(ledger["timeline"]):
        if d <= day:
            v = ledger["timeline"][d][key]
    return v


def cumulative_at_height(ledger_rows, wallet, internal_wallets, height, kind):
    """Sum of yocto amounts of one kind ('external_in' | 'internal_in' | 'out') in rows at or before `height`."""
    tot = 0
    for r in ledger_rows:
        if r["account_id"] != wallet or r["asset_id"] != "native:near" or int(r["block_height"]) > height:
            continue
        a, other = int(r["amount"]), r["other_account_id"]
        k = "out" if a < 0 else ("internal_in" if other in internal_wallets else "external_in")
        if k == kind:
            tot += abs(a)
    return tot


def to_near(y):
    return y / YOCTO
