---
name: start-phase
description: Start a build phase from CLAUDE.md with a plan that waits for approval. Use when the user types /start-phase followed by a phase number.
disable-model-invocation: true
---

Start phase $ARGUMENTS of the build.

1. Read CLAUDE.md, docs/decisions.md and the phase list. If the
   requested phase is not the next one, say so and stop.
2. Explore only the code relevant to this phase. Do not write or edit any file yet.
3. Reply with a plan in exactly this shape:

   **Scope** — 3–5 concrete bullets for this phase only.
   **Out of scope** — the adjacent things this phase could drift into.
   **Design questions** — the 2–4 decisions that are expensive to change later,
   each with your recommendation, the main alternative, and the trade-off.
   **Who writes what** — which tasks you will implement (boilerplate, plumbing)
   and which are mine to write by hand per CLAUDE.md, with what you will give me
   for those (an explanation, an interface, tests to make pass).
   **Tasks** — numbered, each 30–90 minutes, each ending in something that runs.
   **Checkpoints** — where you will stop and show me something working.
   **Done when** — acceptance checks I can run myself, phrased as observable behaviour.
   **Number to measure** — the one metric this phase should produce.
   **Risks** — the two or three things most likely to go wrong.

4. Stop and wait for my approval. After approval, implement only up to the first
   checkpoint, then stop and show the result.
