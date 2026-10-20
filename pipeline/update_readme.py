"""Rewrite the generated findings block in README.md from verify.py output.  Usage: update_readme.py METRICS README"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify
from common import load_json

metrics, readme = sys.argv[1], sys.argv[2]
with open(readme) as f:
    txt = f.read()
a, b = txt.index(verify.BEGIN), txt.index(verify.END) + len(verify.END)
block = verify.BEGIN + "\n" + verify.markdown(verify.findings(load_json(metrics))) + "\n" + verify.END
with open(readme, "w") as f:
    f.write(txt[:a] + block + txt[b:])
print("README findings block updated")
