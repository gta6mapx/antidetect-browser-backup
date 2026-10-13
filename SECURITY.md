# Security — encryption key model & rotation

## Key model

Sensitive values at rest are encrypted with a single **master Fernet key**
(AES-128-CBC + HMAC, via the `cryptography` package).

| Item | Detail |
|---|---|
| Key file | `<data home>/.master.key` — mode `0600` (owner read/write only) |
| Data home | `ANTIDETECT_HOME` when set; else legacy `~/.antidetect-browser` when it exists; else `~/.antidetect` (see `src/paths.py`) |
| Encrypted values | Proxy auth passwords, stored as `"enc:" + <Fernet token>` in the `proxies` table of `proxies.db` |
| Marker | `enc:` prefix — values without it are treated as legacy plaintext and never passed through decryption |
| Dashboard secrets | The dashboard session-signing secret (`.session.key`) and the users DB (`users.db`, pbkdf2_sha256 password hashes) live in the same data home but are **independent** of the master key |

Key handling rules:

- The master key is created once on first use (`get_or_create_key()`); never
  generate a second one "just in case".
- Never print, log, commit, or paste the key anywhere. The CLI only ever
  reports *counts* and *paths*, never key material.
- Guard `<data home>/.master.key` like the databases themselves: anyone who
  can read both the key file and `proxies.db` can recover every proxy
  password.

## Rotation procedure

Rotate when the key may have been exposed, when rotating staff out, or on a
schedule your policy requires. Rotation has two steps — **re-encrypt first,
replace second** — because the data must be migrated while the old key is
still known.

### Via the CLI (recommended)

```bash
# keys via flags (scripting) …
python -m src.cli key-rotate --old-key '<current key>' --new-key '<new key>'

# … or interactively with hidden prompts (nothing echoed):
python -m src.cli key-rotate
```

The command:

1. Validates both keys are well-formed Fernet keys.
2. Re-encrypts every `enc:` proxy password from old → new (`rotate_key()`),
   reporting the count. Plaintext/NULL values are left alone.
3. Copies the current key file to `.master.key.bak` (same dir, mode 0600).
4. Writes the new key to `.master.key` (mode 0600).

Verify afterwards:

```bash
python -m src.cli proxy list        # DB readable
python -m src.cli profile launch --name <one profile>   # proxy auth still works
```

Keep `.master.key.bak` until you are confident the new key works everywhere,
then delete it — while it exists, the old key can still decrypt the rotated
data.

### Manual procedure (no CLI)

```python
from src.security.crypto import rotate_key, replace_master_key, get_or_create_key
import shutil, os
from src import paths

old = get_or_create_key()          # or read from your vault
new = Fernet.generate_key()        # cryptography.fernet.Fernet

n = rotate_key(old, new)           # 1. re-encrypt all enc: passwords
print("rotated", n)

shutil.copy2(paths.master_key_path(), paths.master_key_path() + ".bak")
os.chmod(paths.master_key_path() + ".bak", 0o600)
replace_master_key(new)            # 2. swap the key file
```

### What can go wrong

| Failure | Consequence | Recovery |
|---|---|---|
| Wrong `--old-key` | `rotate_key` raises `InvalidToken`; rows already rotated keep new ciphertext, the rest are untouched — **re-run with the correct old key** (idempotent) | re-run |
| Key file replaced before `rotate_key` | Old ciphertext becomes undecryptable ("orphaned") | restore from `.master.key.bak` (or your vault), then run `rotate_key` |
| Rotation interrupted between steps | DB partially re-encrypted; key file still old | safe: re-run `rotate_key` with the same keys |
| Lost key, no backup | All `enc:` passwords are unrecoverable; you must re-enter proxy passwords via `proxy add` | re-enter passwords |

### Generating a new key

```bash
python - <<'EOF'
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
EOF
```

Store the new key in your password manager / vault before rotating. If the
key was ever pasted into chat, a ticket, or a log, treat it as exposed:
rotate immediately and delete the exposed copy.
