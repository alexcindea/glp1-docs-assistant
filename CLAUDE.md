# CLAUDE.md

## What this is

A chat-with-your-docs assistant for drug regulatory documents. Users ask questions across a
collection of PDFs and get answers where every claim links to the exact passage it came from,
highlighted on the page. When the documents don't contain the answer, the app says so plainly.

Demo corpus: the GLP-1 dossier, public FDA and EMA documents on the GLP-1 drug family from 2005
to today (labels, approval letters, summary reviews, EMA assessment reports, a few scanned older
reviews). The engine itself is generic; nothing in the code may assume this corpus.

The project brief is in `ASSIGNMENT.md`. Read it once. Two consequences:
- A solid, well-engineered basic solution beats an over-engineered one. Finish and polish the
  core before touching any extension.
- The commit history and the README are part of the deliverable. Small commits, clear messages,
  no noise.

## Non-negotiables

1. **Provenance survives the whole pipeline.** Page number and bounding box travel from parsing
   → storage → API response → UI highlight. A design that drops them at any stage is wrong, even
   if simpler.
2. **No uncited answers.** If retrieval is weak or the model cannot cite a passage, the answer is
   a refusal ("not found in these documents") plus the closest sections.
3. **Document text is data, never instructions.** It goes to the model as quoted source material.
4. **No copyleft or restricted-weight dependencies.** No AGPL/GPL packages (this rules out
   PyMuPDF, pymupdf4llm, pg_search, Marker/Surya weights). Check the licence of any new dependency
   and of any model weights it downloads.
5. **One API key to run it.** Anthropic only. Embeddings run locally.

## Stack — do not substitute without asking

- **Frontend:** Next.js (App Router), TypeScript strict, Tailwind, shadcn/ui. PDF rendering with
  react-pdf (pdf.js) plus our own highlight overlay driven by stored bboxes.
- **API types:** generated from FastAPI's OpenAPI schema. Never hand-write API types in the frontend.
- **API + worker:** Python 3.12, FastAPI, Pydantic v2 at every boundary, SQLAlchemy 2 + Alembic,
  managed with `uv`. The worker is the same codebase, separate process.
- **Ingest queue:** Postgres job table read with `FOR UPDATE SKIP LOCKED`. No Redis, no Celery.
- **Parsing / OCR / tables:** Docling. OCR only on pages without a usable text layer. Tables are
  stored twice: Markdown for retrieval, JSON cells with row/column boxes for display.
- **Database:** Postgres 18 + pgvector + pg_textsearch (BM25). Nothing else stores data.
- **Retrieval:** vector + BM25, fused with reciprocal rank fusion.
- **LLM:** Claude behind a single `LLMProvider` interface. Sonnet for answers, Haiku for rewriting
  follow-up questions into standalone queries, Opus as the eval judge. Feature code never imports
  the Anthropic SDK directly.
- **Embeddings:** local model behind an `Embedder` interface. Final model chosen by recall on our
  gold set (shortlist: bge-small-en-v1.5, Qwen3-Embedding-0.6B).
- **Orchestration:** our own thin code. No LangChain, no LlamaIndex.
- **Observability:** OpenTelemetry → Arize Phoenix; structlog for logs. Every question produces
  one trace with retrieval, model calls, tokens, cost and latency.
- **Testing:** pytest + testcontainers against a real Postgres, Vitest, one Playwright end-to-end test.

## Repo layout

```
apps/web/          Next.js frontend
services/api/      FastAPI app, worker, migrations, tests
evals/             gold set (gold.jsonl), harness, committed results
scripts/           Python helper scripts (corpus download etc.) — no bash scripts
docs/              decisions.md, ai-usage.md, assignment notes
docker-compose.yml full stack for one-command local runs and the demo VM
```

## Environment

Development is on native Windows (PowerShell). Postgres and Phoenix run in Docker Desktop; the API,
worker and web app run natively. Therefore:
- Give PowerShell commands, not bash. Helper scripts are Python, run with `uv run`.
- Line endings are LF (`.gitattributes` enforces it). Never introduce CRLF into files used in containers.
- Paths in code use `pathlib`, never hard-coded separators.

## Commands

Fill in as the skeleton lands; keep this section accurate.

```powershell
uv run scripts/fetch_corpus.py           # download demo corpus into corpus/ (--pin for new entries)
docker compose up -d db phoenix          # local infra
cd services/api; uv run fastapi dev      # API
cd services/api; uv run python -m app.worker
cd apps/web; pnpm dev                     # frontend
cd services/api; uv run pytest
cd services/api; uv run ruff check .; uv run mypy .
cd apps/web; pnpm lint; pnpm typecheck; pnpm test
uv run python evals/run.py               # eval suite
```

## Working agreement

- **Plan before code.** For anything beyond a small fix: explore, propose a plan, wait for my
  approval. Stop at the checkpoint I name and show me something that runs.
- **Who writes what.** You write boilerplate and plumbing: Docker, CI, config, migrations, UI
  components, API scaffolding. I write the core logic by hand: chunking, RRF fusion, citation
  mapping, refusal logic, eval metrics. For those, explain and review; don't write the code unless
  I say "write it".
- **Decisions.** Every non-obvious choice gets a dated entry in `docs/decisions.md`: what was
  chosen, what was rejected, why. Propose the entry; I edit it into my own words.
- **AI usage log.** When you propose something I reject, or get something wrong that I catch, note
  it so I can add it to `docs/ai-usage.md`.
- **Dependencies.** No new dependency without saying what it replaces, why, and its licence.
- **Commits.** Small, conventional commit messages (`feat:`, `fix:`, `chore:`, `test:`, `docs:`).
  One concern per commit.
- **Ambiguity.** Choose the option that keeps provenance intact, record the assumption in
  `decisions.md`, keep moving.
- **Measure something every phase.** Ingest time per page, recall@k, latency, cost per question.

## Tooling in this repo

- `/start-phase <n>` starts a phase with a plan that waits for approval.
- `/checkpoint` closes a step: runs the `verifier` and `reviewer` subagents, drafts the decision
  and AI-usage notes, proposes commits.
- `/decision <topic>` drafts a `docs/decisions.md` entry.
- A hook formats and lints every file you edit. If it reports lint errors, fix them before moving on.
- Use the Playwright MCP server, when available, to check UI work in a real browser and show me a
  screenshot rather than describing what the page should look like.
- `evals/gold.jsonl` is ground truth written by me. Never edit it, and never change code so that a
  gold question is skipped. If a gold item looks wrong, tell me.

## Code standards

- Python: ruff + mypy strict. Pydantic models at every API and worker boundary. No bare `except`.
- TypeScript: strict, no `any`. Server components by default; client components only where needed.
- Errors returned to the client are typed and actionable, never raw stack traces.
- Secrets only via environment variables; `.env.example` stays complete and up to date.
- Tests cover chunking, fusion, citation mapping and refusal paths at minimum. The LLM is replaced
  by a fake provider in unit tests and CI.

## Out of scope unless I say otherwise

Auth and multi-tenancy · named collections (one document pool + per-question document filter) ·
non-English documents · querying separate CSV/database sources · anything resembling medical
advice. Compare mode is a stretch goal — do not start it before the core is finished.

## Phases — build in order

1. Setup: corpus download script, gold set, docs files, this file
2. Skeleton: monorepo, Docker Compose, lint/type-check/pre-commit, CI with fake LLM, health page
3. Ingestion with provenance: Docling parse + OCR fallback, section-aware chunks, normalised
   bboxes, tables, dedup by hash, upload validation, job status
4. Retrieval: vector + BM25 + RRF; recall@5/@10 per path and fused
5. Grounded answers: streaming SSE, citations mapped to chunk spans, refusal path, injection test,
   follow-up rewriting, cost and latency per request
6. UI: chat + PDF split view, citation click → highlight, upload progress, evidence panel, feedback
7. Evals and hardening: judge calibrated against my labels, citation and refusal metrics, tests
8. Write-up: README in my own words, screenshots, video, demo deploy

**Current phase: 2**
