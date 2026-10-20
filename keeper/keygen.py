"""Create a fresh ed25519 key for the keeper and write it as near-cli credentials JSON (mode 600); print the public key.

    python3 -I keeper/keygen.py ACCOUNT OUT.json

The file is the only secret the keeper needs (NEARBOOKS_KEEPER_KEY = its private_key). Keep it out of the repository.
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keeper  # noqa: E402

account, out = sys.argv[1], sys.argv[2]
if os.path.exists(out):
    raise SystemExit(f"{out} exists; refusing to overwrite a key")
seed = os.urandom(32)
pub = keeper.ed25519_public(seed)
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump({"account_id": account, "public_key": "ed25519:" + keeper.b58encode(pub), "private_key": "ed25519:" + keeper.b58encode(seed + pub)}, f)
print("ed25519:" + keeper.b58encode(pub))
