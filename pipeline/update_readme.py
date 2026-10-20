"""Rewrite the generated blocks in README.md, docs/findings.md and docs/deployment.md (summary, key findings, findings
table, cross-source table, sample tables, verify and test output, testnet deployment, attested snapshots,
predictions). Every occurrence of a block is rewritten.  Usage: update_readme.py METRICS README"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify
from common import load_json

metrics, readme = sys.argv[1], sys.argv[2]
blocks = verify.generated_blocks(load_json(metrics))
placed = set()
for path in verify.doc_paths(readme):
    with open(path) as f:
        txt = f.read()
    for begin, end, body in blocks:
        i = txt.find(begin)
        while i >= 0:
            j = txt.index(end, i) + len(end)
            new = begin + "\n" + body + "\n" + end
            txt = txt[:i] + new + txt[j:]
            placed.add(begin)
            i = txt.find(begin, i + len(new))
    with open(path, "w") as f:
        f.write(txt)
for begin, _, _ in blocks:
    if begin not in placed:
        print(f"no {begin} marker in README.md or docs/{{{','.join(verify.DOCS)}}}; block skipped")
print(f"generated blocks updated ({len(placed)} of {len(blocks)})")
