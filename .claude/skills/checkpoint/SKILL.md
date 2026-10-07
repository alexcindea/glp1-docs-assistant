---
name: checkpoint
description: Close out a step - verify, review, draft the decision and AI-usage notes, and propose commits. Use when the user types /checkpoint.
disable-model-invocation: true
---

We are at a checkpoint. Optional context from me: $ARGUMENTS

1. Delegate to the `verifier` subagent and wait for its report.
2. Delegate to the `reviewer` subagent and wait for its report.
3. Reply with:
   - **Status** — what now works, in two or three lines, and how I can see it
     myself (a command to run or a URL to open).
   - **Checks** — the verifier's summary. If anything failed, stop here and
     propose fixes instead of the rest.
   - **Review** — blockers and should-fix items from the reviewer, most
     important first. Leave nits out unless there are no other findings.
   - **Decision entry** — a draft for docs/decisions.md if this step involved a
     non-obvious choice, in the template already used in that file. Mark it as a
     draft for me to rewrite in my own words. Do not write it to the file.
   - **AI-usage note** — anything you proposed that I rejected or corrected in
     this step, one line each, for docs/ai-usage.md. Do not write it to the file.
   - **Commits** — the changes grouped into small commits, each with a
     conventional commit message. Do not commit until I say so.
   - **Number** — the metric this step produced, if any.
