# deepseek-worker FAQ

### 1. What does the plugin do, step by step?
Claude calls the `delegate` tool on the plugin's local MCP server with a brief, project directory, output path and optional context files. The server refuses an existing output file unless `overwrite` is true, loads `AGENTS.md` and `CLAUDE.md`, and pre-reads your `context_files`. The worker then runs a tool loop (40 requests by default) that lists, reads and searches files, and writes its result. Claude gets back a short summary, status and output path, not the content, and is told to read the file and record an outcome. Tasks share no memory.

### 2. Which files can the worker read, and which is it blocked from?
Only files inside the `project_dir` you pass: symlinks are followed and anything outside the project is rejected. Everything under `.git` is blocked, as are names matching `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.keystore`, `*.jks`, `id_rsa*`, `id_ed25519*` and `id_ecdsa*`, case-insensitively. Listing and searching also skip folders like `node_modules`, `.venv`, `dist` and `build`. The block is by filename only, so a secrets file with an ordinary name could still be read.

### 3. Where can it write, and can it overwrite or delete my files?
Only to the single `output_path` given to `delegate`, relative to the project and under the same path checks as reads. `write_output` creates missing parent folders and writes text; calling it again replaces that one file. If the file exists and `overwrite` is false, the delegation fails before any request is sent. No tool deletes or renames anything.

### 4. What data leaves my machine, and where does it go?
The brief, `AGENTS.md`, `CLAUDE.md`, your `context_files` and every file the worker reads are sent to your gateway in `POST /v1/messages` requests. Your key travels in an `Authorization` header, plus a session id header if your profile names one. Nothing else leaves the machine: there is no web search, and the log stays local.

### 5. What happens if the gateway is down or slow?
`502`, `503`, `504` and network errors get 3 quick retries after 1, 2 and 4 seconds. If they all fail, the gateway is skipped for 120 seconds (`DEEPSEEK_WORKER_OUTAGE_PAUSE_SECONDS`), so later tasks fail at once and Claude does the task itself. A timeout (300 seconds by default) is not retried, but it does start that pause. `429` is retried up to 5 times with `Retry-After` or backoff capped at 30 seconds, and requests are kept under `requests_per_minute` (default 50).

### 6. Does it change which model Claude Code uses, or my Claude usage?
No. The plugin only adds an MCP server; Claude Code's own model is untouched. A delegation still costs some Claude tokens, since Claude writes the brief and checks the result, but the volume work happens on your gateway.

### 7. What is logged, and where?
One JSON line per delegation and one per outcome, in `~/.local/state/deepseek-worker/delegations.jsonl` (`DEEPSEEK_WORKER_LOG` overrides the path). A delegation line holds the id, time, project, the brief cut to 300 characters, output path, request count, status, number of files read, duration and error; an outcome line holds the id, time, verdict and note. It stays on your machine; a failed write warns on stderr and the delegation still returns.

### 8. How do I update or remove it?
Run `git pull`, then `./scripts/install.sh` again: it refreshes the marketplace, reinstalls the plugin and applies a changed profile or key. To remove it, run `claude plugin uninstall deepseek-worker@deepseek-worker`, then `claude plugin marketplace remove deepseek-worker`, and restart Claude Code. Nothing here deletes the log, so remove `~/.local/state/deepseek-worker/delegations.jsonl` yourself.

### 9. Can I change what goes to DeepSeek and what stays with Claude?
Yes. The easiest way is to add your own rules to `~/.claude/CLAUDE.md` (all projects) or a project's `CLAUDE.md`, such as "never delegate anything in payments/" or "always delegate writing unit tests". Claude follows them over the skill's defaults, and the repository stays untouched, so `git pull` stays clean. You can also edit the "Delegate" and "Keep it yourself" lists in `skills/delegate-to-deepseek/SKILL.md`, but Claude Code runs its own copy of the plugin, so run `./scripts/install.sh` again afterwards; `git pull` may then ask you to commit or stash your edits. Restart Claude Code either way.
