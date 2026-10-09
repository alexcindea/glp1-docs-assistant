# Decisions

One entry per non-obvious choice, newest first. Claude drafts each entry from the work
and the measurements; I make the decisions and own the text. These become the "Key
technical decisions" section of the README.

Template:

```
## YYYY-MM-DD — Title
**Context:** the problem, one or two sentences.
**Options:** A (trade-off) · B (trade-off) · C (trade-off)
**Decision:** what I chose.
**Why:** the deciding reasons, with numbers where I measured something.
**Consequences:** what it costs, what I'll have to handle later, what would make me revisit it.
```

---

## 2026-10-09 — Answer eval: scored on evidence; wording left to the judge

**Context:** Phase 5 needs a number for whole answers, before the LLM judge exists (phase 7).
**Options:** pass/fail on evidence (answered citing the gold passage, or refused when it should)
· an LLM judge now, uncalibrated · reading answers by hand each run
**Decision:** `evals/answers.py` runs every gold question through the answer pipeline and passes
it when it answers citing its gold passage (the same matching as retrieval recall), or refuses
when the documents don't answer it. Answerable failures are split by whether retrieval had
found the passage. Answer text and citations are saved with the results.
**Why:** It is cheap (~$0.22 a run), deterministic to score, and says where a failure happened.
First run 0.694; reading the failures showed three right answers citing the same fact stated
elsewhere in the same document, so those gold items now accept that passage too (`match: any`,
each new quote checked against the corpus). With that: **0.778** pass, answerable questions
answered citing gold 0.774 (exactly the share where retrieval found the passage; no question
failed once the model had its passage), unanswerable refused 0.8.
**Consequences:** It cannot tell a right answer from a wrong one that cites the right passage;
the judge will. q033 (SELECT trial results) is answered "not in the report; the trial was still
ongoing", with citations, rather than refused: honest, but a failure here until the judge scores
it. Since the model isn't deterministic, a question can flip between runs.

## 2026-10-09 — Rewrite only follow-up questions before searching

**Context:** "And for Mounjaro?" can't be searched alone. Rewriting every question would also
strip attack text ("ignore all previous instructions…") from the search.
**Options:** never rewrite · rewrite follow-ups only · rewrite every question
**Decision:** Follow-ups only, with the small model (gpt-6-luna), into a full natural question,
not keywords. `QUERY_REWRITE=always` stays available. A failed rewrite searches the question as
asked.
**Why:** Answer eval, 36 questions: follow-ups only passes 0.778 at 2.2 s median to first text;
every question 0.75 at 3.2 s (the rewrite call takes ~2 s, not the ~0.5 s I expected), and it
lost q001 to a worse search. Keyword rewrites hurt: "Ozempic maximum dose" lost the label's
"maximum recommended dosage" passage from the top 10, which the question as asked ranks 6th.
**Consequences:** A first question's attack text goes into the search query. q035 then misses
its passage, but the model ignores the attack (tested), and even the clean question "What's the
maximum dose of Ozempic?" misses it ("dose" against the label's "dosage"): a retrieval gap for
phase 7 (a reranker or query expansion), not an injection one.

## 2026-10-09 — Streaming answers: FastAPI's own SSE, every answer saved

**Context:** The chat UI (phase 6) builds on this contract, and API types must be generated,
never hand-written.
**Options:** our own event types over server-sent events · the Vercel AI SDK stream protocol ·
a plain JSON response after the whole answer
**Decision:** `POST /answers` streams typed events through FastAPI's `EventSourceResponse`:
`sources` (the passages, with spans), text `delta`s, then `done` (answer, citations, refusal,
tokens, cost, timings, answer id) or a typed `error`. Each answer is saved to an `answers`
table with its trace id.
**Why:** No new dependency; the event types are in the OpenAPI schema with everything else;
FastAPI keeps the request's database session open until the stream ends, sends keep-alive
pings, and cancels the work, model call included, when the client leaves. Saving answers gives
feedback (phase 6) something to point at and gives cost and latency over real use.
**Consequences:** openapi-typescript 7 doesn't read OpenAPI 3.2's `itemSchema`, so the stream
itself is typed `unknown`; the web app joins the four generated event types into one union.
Error responses are declared as JSON explicitly, or FastAPI documents them as event streams.

## 2026-10-09 — Citations by passage number; three ways to refuse

**Context:** No uncited answers, and every claim must lead to a page and a box.
**Options:** `[n]` markers in streamed text, mapped to chunks by us · strict JSON with a quote per
claim (validates well, streams badly) · refusing below a retrieval score threshold
**Decision:** Passages go to the model as numbered, quoted blocks; it cites `[n]`; we map `n` to
the chunk and its spans, and drop numbers that name no passage. Refuse (1) when retrieval finds
nothing, without calling the model, (2) when the model replies `NOT_FOUND` or `OUT_OF_SCOPE`,
which is held back so it never streams as text, (3) when a finished reply cites nothing. Every
refusal carries the three closest passages. A reply cut off by the token cap is an error, not a
"not found".
**Why:** Markers stream naturally. Fused (RRF) scores are ranks, not relevance, so there is no
honest threshold, and a threshold can't catch a near miss like the SELECT trial question, where
the retrieved text is on topic but holds no answer.
**Consequences:** A highlight covers the whole cited chunk (median 104 tokens); citations carry
`char_start`/`char_end` so narrowing to a sentence won't change the API. A reply that cites
nothing has already streamed when it is refused, and clients replace it.

## 2026-10-09 — OpenAI models per role, prompt caching off

**Context:** CLAUDE.md left the exact models to phase 5; cost per question is a phase metric.
**Options:** one strong model for every role · a strong model for answers and a small one for
rewriting · OpenAI's automatic prompt caching on (the default) or off
**Decision:** gpt-6.1-sol at low reasoning effort answers, gpt-6-luna without reasoning rewrites
follow-ups, gpt-6-astra will judge (phase 7). Prompt caching is off. Models and effort are
settings; prices live in `app/pricing.py`, copied from OpenAI's pricing page.
**Why:** Sol costs a fifth of Astra and answered the checkpoint questions correctly at about
$0.006 each. With caching on, each ~3k-token prompt was written to the cache at 25% over the
input price and never reused, because every question gets different passages.
**Consequences:** Prices must be updated by hand when OpenAI changes them; an unknown model is
costed as unknown, not zero. The answer model is not deterministic (no temperature on reasoning
models), so a gold question can flip between runs.

## 2026-10-09 — Embedding model: Qwen3-Embedding-0.6B, chosen by fused recall

**Context:** The shortlist was bge-small-en-v1.5 and Qwen3-Embedding-0.6B. What matters is
recall after fusion with BM25, not the model alone: a model that only re-finds what BM25 finds
adds nothing, and can push BM25's correct hits down.
**Options:** bge-small (33M parameters, 384 dims, MIT) · Qwen3-Embedding-0.6B (0.6B, 1024 dims,
Apache-2.0) · bge-small with BM25 weighted higher in fusion
**Decision:** Qwen3-Embedding-0.6B, pinned to a revision, run on CPU with transformers.
**Why:** Measured on the 31 answerable gold questions (`evals/results/embedder-bakeoff.md`):
BM25 alone recall@10 0.61; bge fused with BM25 0.52 at best (worse than BM25 alone); Qwen3
fused 0.71 (chunk text) or 0.74 (section path + text). With bge, chunks both paths rank in
the middle (the same section in another GLP-1 drug's label) pushed out the right chunk:
"maximum dose of Ozempic" fell from BM25 rank 6 to 21. Weighting BM25 higher could at best
match BM25 alone, and tuning weights on 31 questions would fit the test, not the problem.
**Consequences:** About 0.5 s per chunk on this CPU (~1.8 s per page, against ~1.4 s to parse
it): ingest time roughly doubles, ~45 min for the demo corpus once. ~2.4 GB of RAM in the API
(it embeds questions) and in the worker; the images carry 1.2 GB of weights. A GPU or an ONNX
export would cut both; the README's production notes say so. Changing the model needs a
migration (vector size), which `app.providers` enforces at startup.

## 2026-10-09 — Hybrid retrieval: BM25 + vectors, RRF k=60, 50 candidates per path

**Context:** Retrieval must find passages that differ from their neighbours by one drug name,
and passages phrased unlike the question.
**Options:** vectors only · BM25 only · both, fused by reciprocal rank fusion (RRF) · both,
fused by normalised scores
**Decision:** Both. Each path returns 50 candidates; RRF merges them by rank (k = 60, the
original paper's value, not tuned). What is indexed is the chunk's section path plus its text;
the citeable text is stored unchanged. Searching the whole corpus uses an HNSW index with
`ef_search` raised to the candidate count; searching chosen documents is exact (their chunks are
read and every distance computed), because the approximate index can miss a chunk entirely.
**Why:** The paths fail differently: BM25 finds scanned pages and exact names (scan 0.75,
"printed page differs" 1.0, vectors 0.25 and 0.0 with bge) and vectors find paraphrases
(follow-ups 1.0 vs 0.67). Ranks avoid comparing BM25 scores with cosine similarities. End to
end (`evals/results/retrieval.md`): vector 0.63, BM25 0.68, fused 0.79 recall@10; fused
recall@5 0.68 (0.76 and 0.65 before q004 accepted the label's other statements of the same
fact; see the AI usage log). Indexing the section path helped bge's vector recall@5 from 0.32 to 0.42; with
Qwen3 it gained a question at @10 (0.71 to 0.74) and lost one and a half at @5 (0.65 to 0.60),
within noise on 31 questions, so I kept it for the larger top-10 gain and so both paths see the
same text. Without the HNSW settings a test returned 41 of 60 rows. A filtered search through
the index once returned nothing in CI (a vector the graph couldn't reach), so filtered searches
skip the index; the largest document has ~1,200 chunks, a few milliseconds to scan. Two later fixes came from reading the misses: word-for-word repeats within a document
are dropped after fusion (9 wasted top-10 places across the gold questions), and a table a page
break cut off its header gets the first part's caption and header (see the chunking entry).
Vector search only compares vectors from the running model (name and revision), so a corpus
embedded by another model can't be ranked by noise; readiness warns about such chunks.
**Consequences:** A search takes ~160 ms on the dev CPU: 130 ms to embed the question, 18 ms
vector, 7 ms BM25. Recall@5 moves by about ±0.03 between runs, because BM25 orders tied scores
by physical row order, which changes when rows are rewritten. Still missed: passages only one
path finds at a middling rank (an RRF trade-off; a reranker would address it), long chunks from
scanned pages, and the injection question, whose query is mostly attack text (phase 5 rewrites
queries first). k and the candidate count stay at defaults unless a larger gold set justifies
tuning.

## 2026-10-09 — Chunking: sections as boundaries, ~300 tokens, tables by rows; parse once

**Context:** Chunks are what retrieval finds and what answers cite, so each must be one coherent
unit and every character must map back to a page and box.
**Options:** fixed-size windows with overlap (simple, mixes sections, fuzzy citations) · Docling's
HybridChunker (tied to one tokenizer, its own provenance format) · our own section-aware chunker
**Decision:** Our own. Sections are hard boundaries; ~300 tokens, never over 450; no overlap.
Tables are their own chunks, rebuilt from cells, split by rows with the header repeated, each
part boxed by its own rows. The parse is stored, so re-chunking skips OCR.
**Why:** Measured on the corpus: 4,952 chunks, median 104 tokens, none over 450. Re-chunking all
18 documents takes 2.2 s against 25 minutes to parse, so chunking can be tuned without parsing
again.
**Consequences:** Many short chunks (labels have tiny sections). Sizes are words × 1.3, which
undercounts real tokenizers by about 35% here (median 141 real tokens); harmless for Qwen3,
which reads 1,024 tokens (14 of 4,955 chunks are longer), so the 450 cap now only keeps chunks
small enough to cite precisely. Measuring recall in phase 4 led to two structural fixes: a
heading that reads like a table caption becomes that table's caption, and a table a page break
cut off its header gets the first part's caption and header, boxed on the page where they sit
(99 of 584 tables start a page right after a same-width table; `sections-v5`).

## 2026-10-09 — The ingest queue is a Postgres table; PDFs are stored in Postgres

**Context:** Parsing takes seconds per page, so it runs in workers; nothing but Postgres may
store data.
**Options:** a Postgres job table claimed with FOR UPDATE SKIP LOCKED · Redis/Celery (ruled out
by CLAUDE.md) · PDFs on a disk volume or object storage
**Decision:** Job table with heartbeats; a job whose worker died is reclaimed when its heartbeat
goes stale; one active job per document; unreadable PDFs fail at once, other failures retry with
backoff. PDF bytes live in their own table.
**Why:** One system to run and back up. Verified live: a worker killed mid-document was taken
over by another and the document finished.
**Consequences:** For production, PDFs belong in object storage (S3/GCS) and the queue scales
with more workers; both are README points.

## 2026-10-09 — Docling with RapidOCR, OCR only on scanned pages; OpenCV's LGPL FFmpeg accepted

**Context:** Parsing must keep page and box for every block, read the 274 scanned pages, and stay
within the no-copyleft rule.
**Options:** Docling + RapidOCR (MIT / Apache-2.0, pip-only, CPU) · Docling + Tesseract (system
install, awkward on Windows) · Docling + EasyOCR (slow on CPU)
**Decision:** Docling + RapidOCR in the worker only, CPU PyTorch. OCR runs only on pages without a
text layer. RapidOCR needs OpenCV, whose wheels bundle FFmpeg (LGPL) as a separate shared library.
**Why:** Measured 1.3–2.7 s per text page and 4.3 s per scanned page. Docling's default install
includes RapidOCR, so OpenCV comes with it either way; FFmpeg is unmodified and never called
(video I/O). Unlike sharp, there is no free way around it.
**Consequences:** Text inside images on pages that do have a text layer isn't read (packaging
artwork on two Byetta label pages). Docling marks every heading level 1, so section depth comes
from heading numbers.

## 2026-10-09 — OpenAI for the LLM; embeddings stay local

**Context:** I already have OpenAI credit. The plan said Claude (Anthropic) for answers, rewriting
and the eval judge.
**Options:** keep Anthropic · OpenAI for the LLM only · OpenAI for the LLM and embeddings
**Decision:** OpenAI for the LLM only; models picked in phase 5. Embeddings stay local.
**Why:** Every model call already goes through `LLMProvider` by role, so the switch is one adapter
class and no feature code changes. Local embeddings keep retrieval free and eval scores
reproducible, and the app still needs only one API key.
**Consequences:** Citations come from our own mapping (the model returns passage ids in a strict
output schema), not a provider's citation feature.

## 2026-10-07 — Logs and traces: structlog plus OpenTelemetry, viewed in Phoenix

**Context:** Every question must produce one trace (retrieval, model calls, tokens, cost).
**Options:** Phoenix's own Python package (Elastic License) · plain OpenTelemetry SDK (Apache-2.0)
**Decision:** structlog for logs, each line carrying its trace id; the plain OpenTelemetry SDK for
traces, with FastAPI and SQLAlchemy instrumented. Phoenix runs only as a container. Tracing is off
unless `OTEL_TRACES_ENDPOINT` is set, so tests and CI never need it.
**Why:** No Elastic-licensed code in our dependencies, and any OTLP backend can replace Phoenix.
**Consequences:** Model-call spans with tokens and cost come in phase 5.

## 2026-10-07 — API types generated from OpenAPI and committed

**Context:** The frontend must never hand-write API types.
**Options:** generate at build time from a running API · export the schema without a server and
commit the generated files · a heavier generator (orval, hey-api)
**Decision:** Export the schema with a script, generate types with openapi-typescript, call the
API through openapi-fetch (both MIT). Both files are committed; CI fails if they are stale.
**Why:** Type changes show up in code review, and the web build never needs a running API.

## 2026-10-07 — Model access behind interfaces, with fakes until phases 4 and 5

**Context:** Tests and CI must run without an API key or a model download.
**Options:** call the Anthropic SDK directly and mock it · one `LLMProvider` and one `Embedder`
interface, with fakes now and real implementations later
**Decision:** Interfaces. Feature code asks for a role (answer, rewrite, judge), never a model
name, and every response carries token usage for cost tracking. The embedder embeds documents
and queries separately (both candidate models prefix queries) and reports its vector size.
**Consequences:** Real embedder in phase 4, Anthropic adapter in phase 5. Open question: a typed
field for source passages, so document text can never be mixed into instructions.

## 2026-10-07 — Database driver: asyncpg, not psycopg

**Context:** psycopg, the usual Postgres driver, is LGPL-3.0, which breaks the no-copyleft rule.
**Options:** asyncpg (Apache-2.0, async only) · psycopg 3 (LGPL) · pg8000 (BSD, slow)
**Decision:** SQLAlchemy 2 async with asyncpg everywhere.
**Why:** No licence question, and streaming answers are naturally async.
**Consequences:** asyncpg raises some connection errors (wrong password, unknown database) that
SQLAlchemy doesn't wrap; `app.db.DATABASE_ERRORS` handles them in one place.

## 2026-10-07 — Database image: pgvector plus prebuilt pg_textsearch

**Context:** We need Postgres 18 with vector search (pgvector) and BM25 (pg_textsearch); no
official image has both.
**Options:** small Dockerfile adding pg_textsearch's release package · compile from source
(slow) · a vendor image like timescaledb-ha (several GB, adds a source-available licence)
**Decision:** `services/db/Dockerfile`: pgvector 0.8.7 on Postgres 18.6 plus pg_textsearch 1.5.1,
checksum-verified and preloaded through Postgres's default config. Used by compose, tests and CI.
**Why:** Both use the PostgreSQL licence, and tests run against exactly what we ship. A test runs
a real BM25 query, because a missing preload only fails at query time.
**Consequences:** A managed Postgres (RDS, Cloud SQL) would need pg_textsearch; this matters for
the productionising section of the README.


## 2026-10-07 — Demo documents: 18 FDA and EMA PDFs, listed in a pinned manifest

**Context:** The app needs a realistic set of documents that makes retrieval and citations
genuinely hard, without making setup slow for someone cloning the repo.
**Options:** ~27 documents (more coverage, about twice the ingest time and test-writing effort) ·
18-document core (every GLP-1 molecule and era, FDA and EMA views of the same drugs) ·
a handful of documents (fast, but too little overlap to stress retrieval)
**Decision:** The 18-document core: 12 FDA (labels, approval letter, summary and medical reviews)
and 6 EMA (assessment reports, one product information), 2005–2026. They are listed in
`scripts/corpus.json` with sha256 pins and downloaded by `scripts/fetch_corpus.py`
(standard library only). The PDFs themselves are never committed.
**Why:** Difficulty comes from near-duplicate content (the same warnings and trial language
across semaglutide and tirzepatide documents), not from volume. Measured: 1,460 pages, 66 MB,
~20 s to download; 282 pages (19%) have no text layer, including two fully scanned reviews
(Byetta 2005, Victoza 2010), so the OCR path is exercised per page, not per document.
FDA documents are public domain; EMA documents may be reused with the source acknowledged.
**Consequences:** The EMA product-information URL always serves the latest version, so its pin
will break when EMA updates it; the script fails loudly rather than silently using a different
file. If ingest turns out slow, add a small quickstart subset for reviewers. The README must
credit EMA as a source.
