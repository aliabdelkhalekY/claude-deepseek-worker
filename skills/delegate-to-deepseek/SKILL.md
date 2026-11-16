---
name: delegate-to-deepseek
description: Load BEFORE reading more than 2 files or ~15K characters, BEFORE exploring code (finding usages, call sites, types matching a pattern, mapping a module or folder), BEFORE reviewing any diff or pull request, BEFORE making the same kind of edit in several places, and BEFORE drafting any document, plan, PR description, Slack or Jira message, release notes, work record or sprint-review points. Hands reading, exploration, mechanical edits and drafting to the DeepSeek worker (the `delegate` tool) so the main model spends its tokens on judgement, and says how to brief it and check the result.
---

# Delegating to the DeepSeek worker

The `delegate` tool hands one task to a DeepSeek model on the user's own
gateway. Its purpose is to save the main model's context window and usage.
DeepSeek does the reading and the volume work, and you do the judgement. You
stay responsible for the result.

## Default: DeepSeek reads, you decide

Before reading anything, ask whether DeepSeek could read it for you and return
what you need. If it could, delegate it. Read files yourself only when the task
is smaller than the brief would be, or when you are checking specific lines
DeepSeek pointed you to.

## Delegate

- **Reading.** Any question that needs more than 2 files or ~15K characters.
  Ask for a structured answer with `file:line` references. Then open only the
  lines you need.
- **Code exploration.** Finding where something is defined or used, listing
  call sites, types or files matching a pattern, mapping a module, folder or
  dependency graph. Never sweep folders yourself.
- **Diffs and pull requests.** Ask for a per-file summary and a list of risky
  spots with `file:line`. Look only at the spots that need judgement.
- **Mechanical code edits.** The same change applied in several places, such as
  conformances, annotations, renames, import changes or moving code without
  changing it. You write the exact pattern and the list of files. Then check the
  changed lines.
- **Drafting.** Documents, plans, guides, READMEs, work records, release notes,
  ticket breakdowns, PR descriptions, Slack and Jira text, sprint-review points.
  You edit the draft.
- **Summaries and reshaping.** Long files, logs, transcripts, notes into
  decisions, long text into short points.
- **Boilerplate.** Anything repetitive that follows a clear pattern.

## Keep it yourself

- Design, architecture and decisions, and anything where a mistake is
  expensive. Base them on DeepSeek's summaries, not on whole files.
- Code changes that need judgement: new logic, debugging, choosing how to fix
  something.
- Anything that needs current information from the internet. The worker has no
  internet access.
- Tasks smaller than the brief you would write for them.
- Files that hold secrets. The worker cannot read them.

When unsure whether a reading, exploration or drafting task is large enough,
delegate it. When unsure whether it involves a decision, keep the decision and
still delegate the reading behind it.

## Files outside the project

The worker reads only inside `project_dir`. For inputs elsewhere (session
transcripts, scratchpad files), copy or extract them into a temporary folder
inside the project, delegate, then delete the folder.

## Write a complete brief

The worker has no memory and has not seen this conversation. In `task`, give:

- the goal and who the output is for;
- the format, headings and rough length;
- facts and decisions from this conversation that are not in any file;
- what not to do.

For reading and exploration, ask for `file:line` references on every claim, so
you can check them without reading the whole source.

For mechanical edits, give the exact before and after pattern, the files, and
the rule that nothing else may change.

Put the files it must use in `context_files`. It can find others itself. It
already gets the project's AGENTS.md and CLAUDE.md.

Use a new `output_path`. Set `overwrite` only when replacing that file is the
goal.

## After it returns

1. Read the output file.
2. Check it with targeted reads: open the `file:line` references it cites and
   the lines it changed. Do not re-read its sources in full, because that
   spends the tokens the delegation saved.
3. Fix it, or redo the task yourself if it is wrong in ways a fix cannot cover.
4. Call `record_outcome` with `accepted`, `edited` or `rejected`, and a one-line
   note on what was right or wrong. The log shows over time what to delegate.

## When the gateway is down

The gateway is sometimes down. If `delegate` fails, do not retry it or wait for
the gateway.

- Small task: do it yourself, and tell the user in one line that you did and
  why.
- Large task (heavy reading, a code sweep, a long diff, many edits): stop and
  tell the user in one line that the gateway is down. Ask before doing it
  yourself, because it spends the tokens the user wants to save.
