"""Print JSON arguments for the contract calls, derived from a metrics.json (no network, no keys).

    python3 make_args.py snapshot    METRICS   -> args for submit_snapshot
    python3 make_args.py prediction  METRICS N -> args for add_prediction (N = 0-based index)
    python3 make_args.py count       METRICS   -> number of predictions
"""
import hashlib, json, sys

kind, path = sys.argv[1], sys.argv[2]
with open(path, "rb") as f:
    raw = f.read()
m = json.loads(raw)
if kind == "snapshot":
    print(json.dumps({"date": m["snapshot"]["date"],
                      "metrics": [{"key": k, "value": str(v)} for k, v in m["snapshot"]["metrics"].items()],
                      "evidence_hash": hashlib.sha256(raw).hexdigest()}))
elif kind == "prediction":
    p = m["predictions"][int(sys.argv[3])]
    print(json.dumps({"claim": p["claim"], "metric": p["metric"], "comparator": p["comparator"],
                      "threshold": str(p["threshold_micro"]), "resolve_after": p["resolve_after"]}))
elif kind == "count":
    print(len(m["predictions"]))
else:
    sys.exit(__doc__)
