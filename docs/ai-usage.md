# How I used AI tools

Running log, newest first. Becomes the "How you used AI tools" section of the README.

## Setup

- Claude Code with a project `CLAUDE.md` (rules, stack, phases), project settings
  (permissions, a format-and-lint hook), two subagents (`reviewer`, `verifier`)
  and three commands (`/start-phase`, `/checkpoint`, `/decision`).
- Split: Claude Code writes boilerplate and plumbing; I write chunking, fusion,
  citation mapping, refusal logic and eval metrics by hand.
- The gold eval set is written by me before the build and is write-protected
  for Claude in the project settings, so the model can't tune the test to pass.

## Log

Template: `YYYY-MM-DD · what I asked · what it proposed · what I kept or changed, and why`
