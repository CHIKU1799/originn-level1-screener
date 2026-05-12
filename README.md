# Originn Level 1 Screener

A KG-backed first-screen filter for the **Originn startup portal**. Takes a
seven-field Step 1 form **and an uploaded pitch deck**, builds a typed
knowledge graph of the submission, and returns a triage label — **pass /
review / reject** — plus a 0–100 composite score across five dimensions.

The pitch deck is **required** — the Originn portal does not accept a
form-only Level 1 submission.

Designed for the top of the funnel: even ideation-stage ventures with no
product, no revenue, and no traction can submit. The scoring rubric does
not penalise missing TAM/SAM, missing financials, or missing traction. It
penalises confusion, missing founders, missing problem grounding, and
buzzword-salad tech.

## What it scores (5 dimensions, 20 points each)

| Dimension | What the KG checks |
|---|---|
| **Clarity & Coherence** | Coverage of expected node types (Problem, Customer, Solution, Why-Now, Founder, Credential…) and form/deck consistency. |
| **Problem & Market Plausibility** | Problem and customer nodes present + grounded by evidence nodes. |
| **Founder–Problem Fit** | Density of `qualified_by` edges from Founder → Credential nodes. |
| **Differentiation & Why-Now** | Presence of Competitor, Why-Now, and Differentiator nodes. |
| **Technical Feasibility** | Solution mechanism + technical-kind credentials + vertical fit. |

Composite → **PASS** at ≥70 · **REVIEW** at 40–69 · **REJECT** at <40.
Thresholds are env-configurable.

## How it works

```
form + deck text (both required)
   └─► [LLM extract — gpt-4o-mini] → typed ExtractedFacts (JSON)
          └─► SubmissionKG (networkx)
                 └─► deterministic scoring (no LLM)
                        └─► [LLM narrate — gpt-4o-mini] → strengths,
                            concerns, technical remark, next steps
```

Two LLM calls per submission (~$0.001 with gpt-4o-mini). The score is pure
graph math, so the same submission produces the same score every run —
narration drift doesn't move the number.

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/originn-level1-screener.git
cd originn-level1-screener

cp .env.example .env
# add your OpenAI key to .env

bash run.sh
```

Open <http://localhost:8000/docs> for the Swagger UI.

## Submit a screening

```bash
curl -X POST http://localhost:8000/screen \
  -F "startup_name=VoxArena" \
  -F "industry_vertical=DeepTech & AI" \
  -F "institute_or_incubator=IIT Bombay" \
  -F "what_are_you_building=Voice-AI adaptive negotiation training for enterprise sales." \
  -F "stage=Idea" \
  -F "why_solve_this=Voice LLMs are finally good enough to argue back in real time." \
  -F 'founders_json=[{"name":"Aarav Mehta","role":"CEO","credential_one_line":"IIT Bombay Speech Lab alum"}]' \
  -F "file=@deck.pptx"     # required
```

Returns `{"job_id": "...", "status": "queued", ...}`. Poll
`GET /jobs/{job_id}` for the result.

Supported deck formats: `.pptx`, `.pdf`, `.md`, `.txt`. Submissions
without a deck return `422 Unprocessable Entity`.

## API

| Endpoint | Purpose |
|---|---|
| `POST /screen` | Submit form + deck (both required) — returns `job_id` (202) |
| `GET /jobs/{id}` | Poll status; includes full result when `status=done` |
| `GET /jobs` | List recent jobs |
| `GET /screenings` | List screening results (filter by name / triage / score) |
| `GET /screenings/{id}` | Full result by ID |
| `GET /health` | Liveness + config snapshot |
| `GET /docs` | Swagger UI |

## Python API

For scripts and notebooks:

```python
import asyncio
from originn_level1 import (
    screen_sync, Level1Form, FounderInput, FormStage, load_deck,
)

form = Level1Form(
    startup_name="VoxArena",
    industry_vertical="DeepTech & AI",
    institute_or_incubator="IIT Bombay",
    what_are_you_building="Voice-AI adaptive negotiation training.",
    stage=FormStage.IDEA,
    why_solve_this="Voice LLMs are finally good enough to argue back.",
    founders=[
        FounderInput(name="Aarav Mehta", role="CEO",
                     credential_one_line="IIT Bombay Speech Lab alum"),
    ],
)

# Deck is required — load it via deck_loader
with open("deck.pptx", "rb") as f:
    deck_text = load_deck("deck.pptx", f.read())

result = asyncio.run(screen_sync(form, deck_text=deck_text))
print(f"{result.composite_score}/100 → {result.triage.value}")
print(result.narrative.technical_remark)
```

## Tests

```bash
# Unit tests (no LLM, fast)
pytest

# End-to-end smoke (hits OpenAI, opt-in)
LEVEL1_RUN_E2E=1 pytest tests/test_screener.py::test_screen_sync_e2e
```

## Configuration

All settings can be overridden via env vars (see `.env.example`):

| Variable | Default | What it does |
|---|---|---|
| `OPENAI_API_KEY` | (required) | OpenAI auth |
| `LEVEL1_MODEL` | `gpt-4o-mini` | Model for both extract & narrate calls |
| `MAX_CONCURRENT_API_CALLS` | `8` | Global OpenAI request semaphore |
| `MAX_CONCURRENT_EVALUATIONS` | `4` | Background worker count |
| `MAX_QUEUE_DEPTH` | `100` | Submissions queued before 503 |
| `TRIAGE_PASS_THRESHOLD` | `70` | Composite ≥ this → `pass` |
| `TRIAGE_REVIEW_THRESHOLD` | `40` | Composite ≥ this → `review`, else `reject` |
| `DB_PATH` | (package dir) | SQLite file path |

## Project layout

```
originn-level1-screener/
├── originn_level1/
│   ├── __init__.py            # public re-exports
│   ├── _openai_client.py      # async client + rate limiter
│   ├── agent.py               # 2-LLM-call agent (extract + narrate)
│   ├── api.py                 # FastAPI server
│   ├── config.py              # env-driven settings
│   ├── deck_loader.py         # pptx / pdf / md / txt → text
│   ├── evaluator.py           # orchestrator + worker queue
│   ├── kg.py                  # SubmissionKG + deterministic scoring
│   ├── models.py              # Pydantic schemas
│   ├── storage.py             # SQLite persistence
│   └── prompts/
│       ├── _registry.py       # versioned prompt loader
│       ├── level1_extract.v1.md
│       └── level1_narrate.v1.md
├── tests/
│   ├── conftest.py
│   └── test_screener.py       # 8 unit tests + 1 opt-in e2e
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
├── requirements.txt
└── run.sh
```

## License

MIT — see [LICENSE](LICENSE).
