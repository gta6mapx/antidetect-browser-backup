#!/usr/bin/env bash
# ==============================================================================
# backup_fleet.sh — back up every anti-detect browser profile session dir with
# restic (https://github.com/restic/restic, BSD-2-Clause).
#
# Each profile gets its own snapshot tagged "profile:<name>", so you can list,
# restore, or forget snapshots per profile later. Unchanged data is deduplicated
# automatically by restic (only new/changed chunks are stored).
#
# ------------------------------------------------------------------------------
# USAGE
#
#   1. One-time setup — initialize the repository (binary is vendored at
#      tools/restic-bin/restic; it is NOT committed to git, see .gitignore):
#
#        export RESTIC_REPOSITORY=/path/to/restic-repo     # local path, sftp:..., s3:..., rclone:..., rest:http(s)://...
#        export RESTIC_PASSWORD='a-strong-passphrase'      # from your password manager / vault
#        tools/restic-bin/restic init
#
#   2. Back up the whole fleet:
#
#        export RESTIC_REPOSITORY=/path/to/restic-repo
#        export RESTIC_PASSWORD='a-strong-passphrase'
#        ./scripts/backup_fleet.sh
#
#   3. Preview what would be backed up, without writing any snapshots:
#
#        ./scripts/backup_fleet.sh --dry-run
#
#   4. Restore one profile later (into a NEW empty dir — never over a live session):
#
#        tools/restic-bin/restic restore --tag "profile:client-acme-01" latest \
#            --target /tmp/restore-client-acme-01
#
#   5. List / verify:
#
#        tools/restic-bin/restic snapshots            # all snapshots, with tags
#        tools/restic-bin/restic snapshots --tag "profile:client-acme-01"
#
# ------------------------------------------------------------------------------
# WHAT IT DOES
#
#   * Resolves the profiles directory exactly like src.paths.profiles_dir():
#         $ANTIDECT_HOME/profiles          (when ANTIDECT_HOME is set — wins)
#         ~/.antidetect-browser/profiles   (legacy dir, when it already exists)
#         ~/.antidetect/profiles           (fresh-install default)
#   * For every immediate subdirectory <name> of that dir, runs:
#         restic backup --tag "profile:<name>" <profiles_dir>/<name>
#   * Prints `restic snapshots` at the end so you can verify what landed.
#
# ------------------------------------------------------------------------------
# ENVIRONMENT (no secrets are ever written to this script or to its logs)
#
#   RESTIC_REPOSITORY   (required) restic repository location. Any backend works.
#   RESTIC_PASSWORD     (required) repository passphrase. restic reads this env
#                       var natively. Prefer `read -s` or a vault export so it
#                       never lands in shell history:
#                         read -rsp "restic password: " RESTIC_PASSWORD; export RESTIC_PASSWORD; echo
#
#   ANTIDECT_HOME       (optional) same override the app honors; changes which
#                       profiles dir is backed up.
#
# EXIT STATUS: 0 when every profile backed up cleanly (or dry-run cleanly),
#              1 on misconfiguration or when any profile backup failed.
# ==============================================================================
set -euo pipefail

# -- argument parsing ---------------------------------------------------------
DRY_RUN=0
usage() {
    sed -n '2,/^# ===/p' "$0" | sed 's/^# \{0,1\}//'
}
for arg in "$@"; do
    case "$arg" in
        --dry-run|-n) DRY_RUN=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "error: unknown argument '$arg' (see --help)" >&2; exit 1 ;;
    esac
done

# -- locate the restic binary -------------------------------------------------
# Prefer the vendored static binary shipped with this repo (tools/restic-bin/),
# fall back to a restic found on PATH.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -x "$REPO_ROOT/tools/restic-bin/restic" ]]; then
    RESTIC_BIN="$REPO_ROOT/tools/restic-bin/restic"
elif command -v restic >/dev/null 2>&1; then
    RESTIC_BIN="$(command -v restic)"
else
    echo "error: restic binary not found." >&2
    echo "  Expected the vendored binary at $REPO_ROOT/tools/restic-bin/restic" >&2
    echo "  or a 'restic' on PATH. Download it from https://github.com/restic/restic/releases" >&2
    exit 1
fi

# -- environment --------------------------------------------------------------
if [[ -z "${RESTIC_REPOSITORY:-}" ]]; then
    echo "error: RESTIC_REPOSITORY is not set (e.g. export RESTIC_REPOSITORY=/mnt/backups/restic-repo)" >&2
    exit 1
fi
if [[ -z "${RESTIC_PASSWORD:-}" ]]; then
    echo "error: RESTIC_PASSWORD is not set (restic needs the repository passphrase)" >&2
    echo "  Hint: read -rsp 'restic password: ' RESTIC_PASSWORD; export RESTIC_PASSWORD; echo" >&2
    exit 1
fi

# -- resolve the profiles dir exactly like src.paths.profiles_dir() ------------
# Resolution order (mirrors src/paths.py):
#   1. $ANTIDECT_HOME when set and non-empty — wins unconditionally.
#   2. ~/.antidetect-browser when that directory already exists (legacy).
#   3. ~/.antidetect otherwise (fresh installs).
if [[ -n "${ANTIDECT_HOME:-}" ]]; then
    DATA_HOME="$ANTIDECT_HOME"
elif [[ -d "$HOME/.antidetect-browser" ]]; then
    DATA_HOME="$HOME/.antidetect-browser"
else
    DATA_HOME="$HOME/.antidetect"
fi
PROFILES_DIR="$DATA_HOME/profiles"

if [[ ! -d "$PROFILES_DIR" ]]; then
    echo "error: profiles dir not found: $PROFILES_DIR" >&2
    echo "  (resolved from ANTIDECT_HOME=${ANTIDECT_HOME:-(unset)}; create a profile first or point ANTIDECT_HOME at your data home)" >&2
    exit 1
fi

echo "== fleet backup =="
echo "restic:       $RESTIC_BIN ($("$RESTIC_BIN" version | head -n1))"
echo "repository:   $RESTIC_REPOSITORY"
echo "profiles dir: $PROFILES_DIR"
[[ $DRY_RUN -eq 1 ]] && echo "mode:         DRY-RUN (no snapshots will be written)"
echo

# -- back up each profile dir --------------------------------------------------
FAILED=0
BACKED_UP=0
while IFS= read -r -d '' dir; do
    name="$(basename "$dir")"
    BACKED_UP=$((BACKED_UP + 1))
    echo "--- backing up profile '$name' ---"
    if [[ $DRY_RUN -eq 1 ]]; then
        if "$RESTIC_BIN" backup --dry-run --tag "profile:$name" "$dir"; then
            echo "    [dry-run ok] profile:$name"
        else
            echo "    [dry-run FAILED] profile:$name" >&2
            FAILED=$((FAILED + 1))
        fi
    else
        if "$RESTIC_BIN" backup --tag "profile:$name" "$dir"; then
            echo "    [ok] profile:$name"
        else
            echo "    [FAILED] profile:$name" >&2
            FAILED=$((FAILED + 1))
        fi
    fi
    echo
done < <(find "$PROFILES_DIR" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)

if [[ $BACKED_UP -eq 0 ]]; then
    echo "warning: no profile directories found under $PROFILES_DIR — nothing to back up."
fi

# -- final listing -------------------------------------------------------------
echo "== snapshots in $RESTIC_REPOSITORY =="
if ! "$RESTIC_BIN" snapshots; then
    echo "warning: 'restic snapshots' failed (repo may not be initialized yet — run 'restic init' first)" >&2
fi
echo

# -- summary -------------------------------------------------------------------
if [[ $FAILED -gt 0 ]]; then
    echo "RESULT: $FAILED of $BACKED_UP profile backup(s) FAILED." >&2
    exit 1
fi
if [[ $DRY_RUN -eq 1 ]]; then
    echo "RESULT: dry-run clean for $BACKED_UP profile(s); no snapshots written."
else
    echo "RESULT: $BACKED_UP profile(s) backed up successfully."
fi
