# scripts/box.sh — sourced by every script that reaches the box: sets SERVER from scripts/box.conf (SHARE-01).
# box.conf is gitignored (the box's address stays out of the repo); copy box.conf.example and fill it in.
#
# SERVER is ALWAYS set here, never inherited: a SERVER left in the caller's shell must not redirect a deploy (cold
# review of PR #38, Oliver/Orly — it sent 76 ssh calls to whatever host the variable named). The one override is the
# explicit BOX_SERVER_OVERRIDE, which only the card scripts' rehearsal test sets.
_box_conf="$(CDPATH= cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/box.conf"
SERVER=""
if [[ -n "${BOX_SERVER_OVERRIDE:-}" ]]; then
  SERVER="$BOX_SERVER_OVERRIDE"
else
  if [[ ! -f "$_box_conf" ]]; then
    echo "box.sh: $_box_conf is missing — cp scripts/box.conf.example scripts/box.conf and set SERVER" >&2
    exit 2
  fi
  # shellcheck source=/dev/null
  builtin source "$_box_conf"   # builtin: an exported function named source can't intercept it
fi
if [[ -z "${SERVER:-}" ]]; then
  echo "box.sh: SERVER is empty in $_box_conf" >&2
  exit 2
fi
# box.conf copied but not filled in: refuse here, before anything tries to reach a host that doesn't exist (2026-10-11).
# The placeholder is read from box.conf.example itself, so the two can't drift (Toby, PR #39).
# If the example is missing or reworded this check is skipped; deploy.sh's reachability probe still stops the run.
_box_placeholder="$(sed -n 's/^SERVER="\(.*\)"$/\1/p' "${_box_conf%.conf}.conf.example" 2>/dev/null || true)"
if [[ -n "$_box_placeholder" && "$SERVER" == "$_box_placeholder" ]]; then
  echo "box.sh: SERVER in $_box_conf is still the example placeholder — set it to the box" >&2
  exit 2
fi
