#!/usr/bin/env bash
# Install or update the deepseek-worker plugin for Claude Code in one step.
#
#   ./scripts/install.sh
#
# The profile says which gateway and model to use. Put it in the repository root, named like
# Team-provider.json, and the script finds it. See example-provider.json. If there is more
# than one, name the one to use: ./scripts/install.sh Team-provider.json
# Run it again at any time to update the plugin, change the profile or change your key.
# For a run with no prompts, set DEEPSEEK_WORKER_API_KEY (and DEEPSEEK_WORKER_SESSION_ID
# if the profile has a session_header) first.

set -euo pipefail

PLUGIN="deepseek-worker@deepseek-worker"
MARKETPLACE="deepseek-worker"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

step() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nInstall failed: %s\n' "$*" >&2; exit 1; }

[ $# -le 1 ] || fail "give at most one provider profile."
if [ $# -eq 1 ]; then
  profile="$1"
else
  # Look for the profile in the repository root, such as Team-provider.json.
  profiles=()
  for file in "$REPO_DIR"/*provider.json; do
    [ -f "$file" ] && [ "$(basename "$file")" != "example-provider.json" ] && profiles+=("$file")
  done
  case "${#profiles[@]}" in
    1) profile="${profiles[0]}" ;;
    0) fail "no provider profile found. Put one in $REPO_DIR, named like Team-provider.json. See example-provider.json." ;;
    *) fail "more than one provider profile in $REPO_DIR. Name the one to use: ./scripts/install.sh $(basename "${profiles[0]}")" ;;
  esac
fi
[ -f "$profile" ] || fail "no file at $profile."

step "Checking requirements"
command -v claude >/dev/null || fail "the Claude Code CLI (claude) is not installed."
command -v python3 >/dev/null || fail "python3 is not installed."
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || fail "python3 must be 3.9 or later."
echo "claude $(claude --version 2>/dev/null | head -n 1), $(python3 --version)"

step "Reading the profile"
# Prints one plugin setting per line, as name=value. Fails with a message if the profile is wrong.
settings_text="$(python3 - "$profile" <<'EOF'
import json, sys

TEXT = ("base_url", "model", "session_header", "help")
NUMBERS = ("context_tokens", "max_output_tokens", "requests_per_minute")

try:
    with open(sys.argv[1], encoding="utf-8") as file:
        profile = json.load(file)
except (OSError, ValueError) as error:
    sys.exit(f"the profile is not valid JSON: {error}")
if not isinstance(profile, dict):
    sys.exit("the profile must be a JSON object.")
if "api_key" in profile:
    sys.exit("the profile must not hold a key. Remove api_key; the installer asks for it.")
unknown = set(profile) - set(TEXT) - set(NUMBERS) - {"name"}
if unknown:
    sys.exit(f"the profile has unknown fields: {', '.join(sorted(unknown))}.")
for field in ("base_url", "model"):
    if not isinstance(profile.get(field), str) or not profile[field].strip():
        sys.exit(f"the profile needs {field}.")
for field in TEXT:
    value = profile.get(field, "")
    if not isinstance(value, str) or "\n" in value:
        sys.exit(f"{field} must be a single line of text.")
    print(f"{field}={value.strip()}")
for field in NUMBERS:
    value = profile.get(field)
    if value is None:
        continue
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        sys.exit(f"{field} must be a whole number above zero.")
    print(f"{field}={value}")
name = profile.get("name", "")
if not isinstance(name, str) or "\n" in name:
    sys.exit("name must be a single line of text.")
print(f"name={name.strip() or profile['base_url'].strip()}")
EOF
)" || fail "see the message above."

settings=()
base_url="" model="" session_header=""
name=""
while IFS= read -r line; do
  case "$line" in
    name=*) name="${line#name=}"; continue ;;
  esac
  settings+=(--config "$line")
  case "$line" in
    base_url=*) base_url="${line#base_url=}" ;;
    model=*) model="${line#model=}" ;;
    session_header=*) session_header="${line#session_header=}" ;;
  esac
done <<< "$settings_text"
echo "Profile: $name"
echo "Gateway: $base_url"
echo "Model:   $model"

step "Your key"
key="${DEEPSEEK_WORKER_API_KEY:-}"
if [ -n "$key" ]; then
  echo "Using the key from DEEPSEEK_WORKER_API_KEY."
else
  [ -t 0 ] || fail "no terminal to ask for the key. Set DEEPSEEK_WORKER_API_KEY and run again."
  printf 'Paste your key (it will not show): '
  read -rs key
  printf '\n'
fi
[ -n "$key" ] || fail "no key given."

session_id=""
if [ -n "$session_header" ]; then
  session_id="${DEEPSEEK_WORKER_SESSION_ID:-}"
  if [ -z "$session_id" ]; then
    email="$(git config --global user.email 2>/dev/null || true)"
    suggested="$(printf '%s' "${email%%@*}" | tr '[:upper:]' '[:lower:]')"
    suggested="${suggested:-$(id -un)}"
    if [ -t 0 ]; then
      printf 'Session id, keeps the prompt cache warm [%s]: ' "$suggested"
      read -r session_id
    fi
    session_id="${session_id:-$suggested}"
  fi
fi

step "Checking the key with the gateway"
# The key goes to Python through the environment, never on a command line.
set +e
DEEPSEEK_WORKER_API_KEY="$key" python3 - "$base_url" "$model" <<'EOF'
import json, os, sys, urllib.error, urllib.request

base_url, model = sys.argv[1].rstrip("/"), sys.argv[2]
request = urllib.request.Request(
    f"{base_url}/v1/messages",
    data=json.dumps({"model": model, "max_tokens": 1024,
                     "messages": [{"role": "user", "content": "Reply with the word ok."}]}).encode(),
    method="POST",
    headers={"Authorization": "Bearer " + os.environ["DEEPSEEK_WORKER_API_KEY"],
             "anthropic-version": "2023-06-01", "content-type": "application/json"})
try:
    urllib.request.urlopen(request, timeout=60).read()
except urllib.error.HTTPError as error:
    print(f"HTTP {error.code}: {error.read().decode(errors='replace')[:300]}")
    sys.exit(2 if error.code in (400, 401, 403, 404) else 3)
except OSError as error:
    print(error)
    sys.exit(3)
EOF
check=$?
set -e
case "$check" in
  0) echo "The key and model work." ;;
  2) fail "the gateway refused the key or the model. Check them against the profile." ;;
  *) echo "Could not confirm the key, as the gateway may be down. Installing anyway." ;;
esac

step "Installing the plugin"
if claude plugin marketplace list 2>/dev/null | grep -q "$MARKETPLACE"; then
  claude plugin marketplace update "$MARKETPLACE"
else
  claude plugin marketplace add "$REPO_DIR"
fi
# Reinstall so the new key and settings always apply.
if claude plugin list 2>/dev/null | grep -q "$PLUGIN"; then
  claude plugin uninstall "$PLUGIN" >/dev/null
fi
# The CLI takes the key as an argument. It is visible to other processes on
# this machine only while this command runs. Claude Code stores it in its
# secure credential store, not in a plain file.
claude plugin install "$PLUGIN" --config "api_key=$key" --config "session_id=$session_id" "${settings[@]}"

step "Checking that the MCP server starts"
status="$(claude mcp list 2>/dev/null | grep "plugin:deepseek-worker" || true)"
case "$status" in
  *Connected*) echo "deepseek-worker is connected." ;;
  *) fail "the deepseek-worker server did not start. Output: ${status:-not listed}" ;;
esac

cat <<EOF

Done. deepseek-worker is installed with the $name profile.

Next, restart Claude Code so it loads the plugin:
  - Desktop app: quit with Cmd+Q and reopen it.
  - Terminal: start a new claude session.
  - VS Code or JetBrains: reload the window.

Later:
  - To update, run git pull, then ./scripts/install.sh again.
  - To change your key or profile, run ./scripts/install.sh again.
EOF
