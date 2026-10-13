# profile-sync-daemon (psd) — RAM caching for antidetect profiles

Upstream: <https://github.com/graysky2/profile-sync-daemon> — **MIT license** (verified in the repo's `LICENSE` file, Oct 2026).

## What it is

profile-sync-daemon (psd) is a tiny Linux-only pseudo-daemon that moves browser
profile directories into **tmpfs (RAM)** and keeps them synced back to disk
with rsync. On modern versions (v7+) it uses **fuse-overlayfs**: the profile
on disk is the read-only lower dir, live changes go to an in-RAM upper dir,
and an **hourly resync** (plus shutdown sync) writes changes back to disk.
Crash-recovery snapshots are taken automatically on ungraceful shutdowns.

## Why it matters at 1000 profiles

Disk I/O is the #1 scaling pain when many persistent Camoufox profiles launch
concurrently: each profile hammers SQLite databases (places, cookies,
sessionstore), favicons, cache and local storage — mostly small random writes.
1000 profiles doing that at once on one SSD/HDD is what stalls launches and
kills drives. With psd, all that chatter happens in RAM; only compacted hourly
rsyncs hit the disk. Launch latency drops and the SSD lives longer.

## Why it's docs + a generator, not code

psd is a **Linux-only system service** (runs as a systemd *user* service,
requires a root-installed package + files under `/usr/share/psd`). It cannot
be vendored or launched from inside the Python app. So this project documents
the setup and ships a config generator; the server admin runs the steps once.

## ⚠️ BIG NOTE — Linux server ONLY, NOT for Windows

This section applies **only to a Linux server** (headless VPS / bare metal
running systemd). It does **NOT apply to Husnain's Windows PC** — psd does not
exist for Windows and must never be attempted there. Windows users get zero
benefit from this doc; ignore it entirely.

## Server setup (Debian/Ubuntu)

```bash
# 1. Install (Ubuntu: 'profile-sync-daemon' is in Universe; Arch: extra)
sudo apt install -y profile-sync-daemon fuse-overlayfs fuse3

# 2. Generate the custom browser definition from your profiles
python3 tools/gen_psd_config.py > /tmp/psd-antidetect

# 3. Install it as a psd "browser" definition (needs root — one time)
sudo install -m 644 /tmp/psd-antidetect /usr/share/psd/browsers/antidetect

# 4. Edit ~/.config/psd/psd.conf and tell psd to manage it:
#       BROWSERS=(antidetect)
#    (leave other entries if you also want psd to manage your regular browsers)

# 5. Preview BEFORE starting (browsers must be closed):
psd p

# 6. Enable + start the systemd user service (resync timer starts with it)
systemctl --user enable --now psd.service
```

On other distros (Arch/Gentoo/NixOS) install from the official repo package;
from source: `make && sudo make install`. Deps: bash ≥ 4.0, coreutils,
findutils, fuse3, fuse-overlayfs, kmod, rsync, systemd.

## RAM sizing (important at scale)

psd stores live profiles under `/run/user/<uid>/psd` (tmpfs), which defaults
to **10% of physical RAM**. Measure first:

```bash
psd p   # shows per-profile "profile size" — sum them, add 30% headroom
```

If the sum exceeds 10% of RAM, raise the limit in `/etc/systemd/logind.conf`
(`RuntimeDirectorySize=…`, e.g. `50%` or a fixed size), then reboot or restart
`systemd-logind`. tmpfs only consumes what is actually used — the number is a
ceiling, not an allocation.

## Lifecycle notes

- **Add/remove profiles:** regenerate + reinstall the definition, then
  `systemctl --user restart psd.service`. psd discovers nothing by itself —
  the definition file is the source of truth.
- **Start/stop with browsers closed.** psd refuses to start while a managed
  browser is running (our custom entry skips the process check, so *you* must
  ensure no Camoufox instance is up before starting/restarting psd).
- **Resync frequency:** the bundled timer resyncs hourly; tighten it with
  `systemctl --user edit psd-resync.timer` (e.g. `OnCalendar=*:0/15`).
- **Crash snapshots:** `psd c` deletes accumulated `-backup-crashrecovery-*`
  dirs when you no longer need them.
- psd **does not support symlinked profile dirs** — our profile dirs are real
  directories, so this is fine. Do not move them to symlinks while psd runs.
- Do not use on eCryptFS-encrypted homes (upstream warns of corruption risk).

## What this does NOT do

- No Windows support — see the big note above.
- Not integrated into the Python app or dashboard; it's an ops-level,
  run-once server setup for the 1000-profile scale lane.
