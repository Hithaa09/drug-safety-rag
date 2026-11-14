# ui

**Owner:** Person B · **Status:** built and checked against a stand-in API (2026-11-11); joins
Person A's real API when it exists (no code change expected)

## What it does

The Streamlit screen from spec p17. It **talks only to the API** (`API_URL`).

- **Input:** a form for prescriptions, lab values (LFT, CBC, RFT), genotypes, smoking, alcohol,
  diet type and meals. You can also load one of 10 synthetic examples or upload a patient JSON.
- **Result, in spec order:**
  1. A **fixed banner**: "Decision support for a pharmacist or clinician, not medical advice".
     It stays on screen while you scroll.
  2. The **flag table**, most severe first: severity, drug, type, reason, what triggered it, and
     the rule source.
  3. **Data gaps**, as notes.
  4. The **summary** and the **suggestions for clinician review**.
  5. **Each flag's explanation**, with numbered references [1], [2]. Each number is an
     **expandable evidence passage** with a link to its source (DailyMed or PubMed Central) and
     its licence. Passages that were retrieved but not cited are shown too.
- **AI summary notices:** if the report came from the template (the model failed or was down),
  the API's "AI summary unavailable" notice is shown. With the AI summary turned off
  (`use_llm: false`), you get flags and evidence only.
- **Health:** the sidebar shows the API's `GET /health`.

| File | Job |
|---|---|
| [`app.py`](app.py) | The screen: only places widgets |
| [`view.py`](view.py) | What to show, worked out from an `AssessResponse`: table rows, evidence numbering, source links, markdown escaping |
| [`patient_input.py`](patient_input.py) | Form rows ↔ `Patient`, with one readable message per problem; JSON upload; examples |
| [`api_client.py`](api_client.py) | `POST /v1/assess` and `GET /health`; every answer is checked against the `AssessResponse` contract |
| [`examples.yaml`](examples.yaml) | The 10 synthetic patients (the same as `tests/gold/llm_cases.yaml`, checked by a test) |
| [`stub_api.py`](stub_api.py) | **Temporary** stand-in for Person A's API (see below) |
| [`verify.py`](verify.py) | `make verify-ui` |

## Run

```bash
make llm-cases     # once, needs PostgreSQL: freezes the evidence the stand-in serves
make stub-api      # terminal 1: stand-in API on :8080 (add --no-model to skip Qwen)
make ui            # terminal 2: http://localhost:8501
```
In the sidebar: **Load example**, then **Assess**. Without a model server you get the template
report and the "AI summary unavailable" notice. To see Qwen's wording, start the Colab notebook's
Part B tunnel and put its `MODEL_URL`, `MODEL_NAME` and `MODEL_API_KEY` in `.env` before
`make stub-api`.

With Person A's API: set `API_URL` to it and run `make ui`. Nothing else changes.

## The stand-in API (`stub_api.py`): what is real

| Part | Real? |
|---|---|
| Endpoints and shapes (`POST /v1/assess`, `GET /v1/flags/{id}`, `GET /health`; FastAPI's 422 error shape) | As spec p17 and the shared contract |
| Flags and evidence | **Saved**: the frozen retrieval for the 10 examples. There is no rule engine, so any other patient, or an example you edited, gets a 404 that says so. It doesn't guess |
| Report | **Real** LLM layer: Qwen, validator, retry, template fallback |
| Data gaps | **A rough guess** from what is missing; Person A's engine decides the real ones |

Delete it once Person A's API runs.

## How to know it worked (no code review needed)

`make verify-ui` must end in `RESULT: PASS` and `59 passed`.

The first part sends **all 10 examples** over HTTP through the stand-in, then through the real
screen, run headless:

| Check (spec p17) | |
|---|---|
| The screen renders every example without an error | |
| The fixed decision-support banner is shown first | |
| The flag table lists every flag, sorted by severity | |
| The summary and suggestions are shown | |
| Every retrieved passage has its own expandable section | |
| Every passage links to its source document | Both link types opened (HTTP 200) |
| The "AI summary unavailable" notice is shown with the template report | |
| Every data gap is shown as a note | |
| Each patient survives the JSON upload unchanged | |

Then the tests (`pytest tests/ui`, offline, with no database or model):

- **The screen:**
  - an AI-written report shows its explanation, citations and suggestions;
  - a template report shows the notice;
  - with the AI summary off, the flags and evidence are still shown;
  - form problems are listed;
  - an edited example is refused by the stand-in, not guessed;
  - with the API down, you get "Cannot reach the API".
- **The form:** all 10 examples survive form → patient unchanged; empty rows are dropped; every
  problem is reported at once.
- **The API client:**
  - FastAPI's 422 and 404 messages are shown readably;
  - an answer that breaks the contract is refused;
  - timeouts and a down API give clear messages.
- **Safety:** text from the API can't inject links, images or HTML into the page. Everything is
  escaped before display.
- **The tests themselves:** six deliberate breaks to the code were each caught by a test, and a
  seventh by `make verify-ui`:
  - escaping off;
  - the banner removed;
  - the stand-in guessing for unknown patients;
  - uncited evidence hidden;
  - the contract check off;
  - empty rows kept;
  - data-gap notes missing (caught by `make verify-ui`).

**Checked by eye in Chrome** (the headless tests can't see layout), using the worked example:
- **The banner:** it scrolled away with the page at first, because Streamlit 1.67 wraps each
  container in its own box. It now stays under the top bar.
- **The form** folds away after Assess, so the result is near the top.

## Docker
The image is in [`docker/`](../../../docker/README.md): `docker build -f docker/ui.Dockerfile -t
drug-safety-ui .`, then `docker run -p 8501:8501 -e API_URL=... drug-safety-ui`. It is checked
`healthy`, runs as an ordinary user, and holds only the screen's files.

## Health check
`make ui` serves `http://localhost:8501/_stcore/health` (Streamlit's own). The sidebar shows the API's
`/health`. It reads `status` and, if present, `checks: {name: {ok, detail}}`; any other shape is
shown as is.

## Environment variables
`API_URL` (default `http://localhost:8080`), `API_TIMEOUT_S` (420: two model calls of up to
180 s each, plus retrieval).

## For Person A
- The screen relies only on the `AssessResponse` contract, plus FastAPI's usual `{"detail": ...}`
  on errors.
- A `/health` body shaped `{"status": "ok", "checks": {"database": {"ok": true, "detail": "..."}, ...}}`
  shows best in the sidebar. This is a suggestion, not a requirement.

## Tests
`pytest tests/ui`
