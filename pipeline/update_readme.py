"""Rewrite the generated blocks in README.md (findings table, cross-source table, sample tables).  Usage: update_readme.py METRICS README"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify
from common import load_json

metrics, readme = sys.argv[1], sys.argv[2]
with open(readme) as f:
    txt = f.read()
for begin, end, body in verify.generated_blocks(load_json(metrics)):
    if begin not in txt:
        print(f"README has no {begin} marker; block skipped"); continue
    a, b = txt.index(begin), txt.index(end) + len(end)
    txt = txt[:a] + begin + "\n" + body + "\n" + end + txt[b:]
with open(readme, "w") as f:
    f.write(txt)
print("README generated blocks updated")
