# deepseek-worker

A Claude Code plugin. Claude stays in charge and hands simple tasks, such as
drafting documents, to a DeepSeek model on your own gateway. Any gateway that
serves the Anthropic Messages API (`POST /v1/messages`) works.

## How it works

- **MCP server** (`server.py`) with two tools:
  - `delegate(task, project_dir, output_path, context_files, overwrite)` runs
    one task on DeepSeek and returns a short summary and the output path.
  - `record_outcome(delegation_id, outcome, note)` records whether Claude
    accepted, edited or rejected the result.
- **Worker** (`deepseek_worker/worker.py`): a short agent loop on DeepSeek.
  It has no memory between tasks. Each task gets the project's `AGENTS.md`
  and `CLAUDE.md`, the brief, and any `context_files`.
- **Sandbox** (`deepseek_worker/sandbox.py`): the worker can list, search and
  read files in the project, but not `.git` or files that may hold secrets
  (`.env`, `*.pem`, `*.key` and similar). It writes only to `output_path`, and
  it will not replace an existing file unless `overwrite` is true.
- **Skill** (`skills/delegate-to-deepseek`): tells Claude what to delegate,
  what to keep, and how to brief the worker.
- **Log**: one JSON line per delegation and per outcome, at
  `~/.local/state/deepseek-worker/delegations.jsonl`. It stays on your machine.

It uses only the Python standard library and runs on the system `python3`
(3.9 or later). There is nothing to install.

## How it treats the gateway

- Each worker step is one request. The server keeps to `requests_per_minute`
  from your profile. Running delegations share that limit. The limit is per
  Claude Code process, not shared across processes.
- No web search. The worker has no internet access.
- `502`, `503`, `504` and network errors get three quick retries (after 1, 2
  and 4 seconds). If those fail, or a request times out, the server skips the
  gateway for 2 minutes, so later tasks fail at once. Claude then does the task
  itself. A timeout is not retried, because each try could wait the full
  timeout.
- `429` is retried with backoff. `401` and `403` are reported with what to do,
  plus the profile's `help` text.

## Provider profile

A profile is a small JSON file that says which gateway and model to use. It
holds no key, so a team can share one in chat. Copy
[example-provider.json](example-provider.json) to start one:

| Field | Required | Meaning |
|---|---|---|
| `name` | no | A label shown by the installer. |
| `base_url` | yes | The gateway's Anthropic API base, without `/v1/messages`. |
| `model` | yes | The model name as the gateway knows it. |
| `context_tokens` | no | The model's context window. Default 128000. |
| `max_output_tokens` | no | Output tokens per request. Default 32768. |
| `requests_per_minute` | no | The rate this plugin keeps to. Default 50. Set it below your key's limit. |
| `session_header` | no | A header the gateway uses to keep your requests on one engine, so its prompt cache stays warm. If set, the installer asks for a session id. |
| `help` | no | Added to key and rate-limit errors, such as where to ask for help. |

Files whose names end in `provider.json`, such as `Team-provider.json`, are
ignored by git, except `example-provider.json`. So your profile can sit in the
repository root without being committed.

## Install

You need the Claude Code CLI (`claude`), `python3` 3.9 or later, a provider
profile and your key for that gateway.

1. Clone this repository.
2. Put the profile in the repository root. Keep its name as it is, ending in
   `provider.json`.
3. Run:

       ./scripts/install.sh

The script finds the profile on its own. If the root holds more than one, name
the one to use: `./scripts/install.sh Team-provider.json`.

The script:

1. checks the requirements and the profile;
2. asks for your key, without showing it, and for a session id if the profile
   has a `session_header`;
3. checks the key and model with the gateway. It stops if the gateway refuses
   them, and goes on with a warning if the gateway is down;
4. installs the plugin with the profile's settings, or reinstalls it if it is
   already there;
5. checks that the MCP server starts.

Then quit and reopen Claude Code. In the desktop app, use Cmd+Q.

Run the script again at any time to update the plugin after `git pull`, to
change the profile or to change your key. To run it with no prompts, set
`DEEPSEEK_WORKER_API_KEY` (and `DEEPSEEK_WORKER_SESSION_ID` if needed) first.

Your key is kept in Claude Code's secure credential store, not in a plain file
or in this repository. The plugin does not change which model Claude Code
itself uses.

## Other settings

All optional. They are read from the environment that Claude Code starts the
server with.

| Variable | Default |
|---|---|
| `DEEPSEEK_WORKER_MAX_TURNS` | `40` |
| `DEEPSEEK_WORKER_REQUEST_TIMEOUT_SECONDS` | `300` |
| `DEEPSEEK_WORKER_OUTAGE_PAUSE_SECONDS` | `120` |
| `DEEPSEEK_WORKER_LOG` | `~/.local/state/deepseek-worker/delegations.jsonl` |

## Tests

    python3 -m unittest discover -s tests -t .

The tests use a fake gateway. They do not need a key or a network.

To check a real gateway handles tool calls across several turns:

    export DEEPSEEK_WORKER_API_KEY=... DEEPSEEK_WORKER_BASE_URL=... DEEPSEEK_WORKER_MODEL=...
    python3 scripts/gateway_test.py

## Unknowns

- How good DeepSeek's documents are compared with Claude's. The log will show.
- What a gateway's `input_tokens` counts. On one vLLM gateway the numbers
  looked too low, possibly because cached tokens are left out. The log does
  not use them.
