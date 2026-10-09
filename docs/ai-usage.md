# How I used AI tools

Running log, newest first. Becomes the "How you used AI tools" section of the README.

## Setup

- Claude Code with a project `CLAUDE.md` (rules, stack, phases), project settings
  (permissions, a format-and-lint hook), two subagents (`reviewer`, `verifier`)
  and three commands (`/start-phase`, `/checkpoint`, `/decision`).
- Split: Claude Code writes all the code. I set the rules and the plan, make the
  decisions and review. Until 2026-10-09 the plan was for me to write the core
  logic (chunking, fusion, citation mapping, refusal, eval metrics) by hand; from
  phase 3 on Claude writes it too and explains each design so I can defend it.
- The gold eval set (36 questions, `evals/gold.jsonl`) was drafted by Claude before
  the build, then every quote was checked against the PDF pages: automatically
  where the page has text, by rendering and reading the page where it is scanned
  (`evals/draft-review.md`). It was write-protected for Claude during the build;
  I lifted that to let Claude create the file, so the git history of
  `evals/gold.jsonl` is the record that it is not changed to make the app pass.
- The entries below are drafted by Claude from each session and kept by me; I
  make the decisions they record.

## Log

Template: `YYYY-MM-DD · what I asked · what it proposed · what I kept or changed, and why`

- 2026-10-09 · first CI run of phase 4 · two failures local runs never showed: the runner ran
  out of disk building images that now carry PyTorch and the models, and a vector search
  limited to one document returned nothing, because the approximate HNSW index can leave a
  vector unreachable (Claude's earlier fix, iterative scans, doesn't help with that) · filtered
  searches are now exact over the chosen documents' chunks; CI frees disk space first.
- 2026-10-09 · Claude flagged gold item q004 as too strict: it accepted only one sentence
  ("The pen should be discarded 30 days after first use"), while the same label says it twice
  more (patient information p19, pen instructions p22), and retrieval was finding those · I
  agreed and had Claude change it, the only edit to `evals/gold.jsonl` since it was written.
  The gold format gained `"match": "any"` (each passage answers on its own; the default "all"
  still needs every passage, as comparisons do). Fused recall@10 went from 0.76 to 0.79 from
  this correction alone, not from better retrieval.
- 2026-10-09 · handed over my own documentation tasks · Claude finalised these entries and the
  decision records, and recommended not committing my local `.claude/settings.json` changes
  (they removed the rules that keep Claude from reading `.env` and editing the gold set, and
  added one-off rules with my local paths) or the superseded gold drafts · kept that advice.
- 2026-10-09 · checkpoint review of phase 4 by the reviewer subagent · it found that vector
  search compared vectors from whatever model made them (a corpus embedded by the fake embedder
  would be ranked by noise), that reusing vectors ignored the model revision, that a failed
  model load left the API "ready" with every search failing, and that Claude's refactor left
  the CI model cache keyed on the wrong file · all fixed before committing. It also claimed
  pgvector returns numpy arrays; Claude checked at runtime and it returns lists.
- 2026-10-09 · a reworked test failed in about 2 of 16 full runs · the HNSW index is
  approximate, so a crowded test database can cost a row or two · the test now checks the two
  index settings directly, which is deterministic, and no longer demands exactly 60 rows.
- 2026-10-09 · before measuring recall, checked that every gold passage exists in some chunk ·
  one didn't: Docling had read a table caption ("Table 1: Adverse Reactions ...") as a heading, so
  Claude's phase 3 chunker kept it only as a section title, never as citeable text, and filed the
  notes under the table as a section named after it · fixed in the structure step (a caption-like
  heading right before a table becomes its caption), with a test; 37 of 37 passages now findable.
- 2026-10-09 · Claude's phase 4 plan assumed vector + BM25 fusion beats either path · measured
  with bge-small it doesn't: fused recall@10 0.52 vs BM25 alone 0.61, because chunks both paths
  rank in the middle push out correct hits only BM25 found · the bake-off was changed to score
  each model *fused* with BM25, not alone; with Qwen3 fusion does beat BM25 (0.71 vs 0.61), so
  I chose Qwen3 despite the slower ingest.
- 2026-10-09 · Claude wrote tests for the HNSW vector-index limits (too few rows, document
  filter) · they passed even with the fix removed, because a small test table never uses the
  index · Claude checked by deleting the fix, then made the tests force the index; now they fail
  without it (41 of 60 rows; nothing for a filtered search).

- 2026-10-09 · ran ingestion on all 18 PDFs before trusting the chunker · the real documents
  showed what the unit tests didn't: a table whose Markdown was 68,000 characters (Docling
  repeats merged cells per column), EMA study summaries framed as one giant table row, and a
  scanned table with no readable text that crashed re-chunking (the "keep the old chunks"
  safety net held) · all fixed, each with a test built from the real case.
- 2026-10-09 · the reviewer flagged that a heading with no body could vanish · Claude's first
  fix kept every lone heading, which turned 430 page banners and labels into noise chunks;
  measuring the corpus showed it · narrowed to headings that state a fact ("Name: value"), 5 left.
- 2026-10-09 · reviewer on the job queue · found PDFium isn't thread-safe (two uploads at once
  could crash the API), that a document could get two jobs at once, and that raw exception text
  reached users · all fixed with tests.
- 2026-10-09 · reviewer on the parser · found wrong character ranges for paragraphs that
  continue across pages, hidden by a clamp that made its own test unable to fail · Claude
  confirmed the cause in Docling's source before fixing; 9 of 18 PDFs mix page sizes.

- 2026-10-09 · decided to let Claude write the core logic as well, and to use OpenAI instead
  of Anthropic for the LLM · Claude pointed out that the `LLMProvider` interface makes the
  switch one adapter class, and recommended keeping embeddings local (free, reproducible
  evals, still one API key) · kept that.

- 2026-10-07 · ran the reviewer subagent on the containers and CI · it said
  `astral-sh/setup-uv@v10` does not exist · half right: release v10.2.0 exists, but astral
  publishes no short `v10` tag, so CI would have failed · checked the tags myself and pinned the
  exact version. Lesson: the AI reviewer also needs checking.
- 2026-10-07 · re-ran the reviewer on fixes it had asked for · it found the worker crashed on a
  wrong database password instead of reporting it (asyncpg errors SQLAlchemy doesn't wrap) ·
  fixed in one shared place, with tests for wrong password and unknown database.
- 2026-10-07 · reviewer on the new web app · found that Next.js silently installs `sharp`, whose
  binary bundles LGPL code · removed it, since we never use Next's image optimisation.
- 2026-10-07 · looked at the first traces in Phoenix · they showed a 2 s database connect, caused
  by an earlier change that bound ports to 127.0.0.1 while the app still used `localhost` ·
  switched the defaults to 127.0.0.1 (readiness check now 8 ms).
- 2026-10-07 · first traces in Phoenix had request spans but no database query spans · Claude
  found the tracing library silently refuses SQLAlchemy 2.1 (only a log line) · pinned 2.0.x
  and added a test that fails if query spans disappear.
- 2026-10-07 · reviewer on the first API skeleton · found a crash that would only happen inside
  a container (a settings path that assumed the folder depth) and the database published on all
  network interfaces with a default password · both fixed before the first commit.
- 2026-10-07 · asked for a phase 2 plan · Claude flagged that psycopg, the usual Postgres
  driver, is LGPL and proposed asyncpg · kept asyncpg, because of my no-copyleft rule.
- 2026-10-07 · Claude's plan named pg_textsearch 1.5.0 and listed "must be preloaded" as a risk ·
  checking the actual release before writing the Dockerfile showed 1.5.1, and the preload really
  is required · the image sets it, and a test runs a real BM25 query to prove it.
- 2026-10-07 · Claude's lint hook reported every `assert` in the tests as an error · Claude
  traced it to a lowercase drive letter (`c:` vs `C:`) breaking ruff's per-folder rules · fixed
  the hook.
- 2026-10-07 · status updates were too dense to follow · asked for plainer explanations, then
  said technical terms are fine · saved as a standing preference: clear and short, proper terms
  allowed.
