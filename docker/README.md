# docker

**Owner:** Person B (the `ui` image). The project's `docker-compose.yml` belongs to Person A.

| File | What it is |
|---|---|
| [`ui.Dockerfile`](ui.Dockerfile) | The Streamlit screen (spec service `ui`: depends on `api`, talks only to it) |
| [`ui-requirements.txt`](ui-requirements.txt) | Its 6 packages, pinned to the versions the tests ran with |
| [`compose.ui.yml`](compose.ui.yml) | **Proposal** for Person A: the `ui` entry to paste into `docker-compose.yml` |
| [`../.dockerignore`](../.dockerignore) | Keeps `data/`, `.env`, `.venv` and tests out of the build |

## Build and run
```bash
docker build -f docker/ui.Dockerfile -t drug-safety-ui .
docker run -p 8501:8501 -e API_URL=http://api:8080 drug-safety-ui      # http://localhost:8501
```
To try it against the stand-in API on a Mac: `make stub-api`, then
`docker run -p 127.0.0.1:8501:8501 -e API_URL=http://host.docker.internal:8080 drug-safety-ui`.

## What is in the image
- Python 3.12 slim.
- Only the files the screen imports: `config.py`, `contracts/`, the `ui` files and
  `scope/lab_codes.yaml`.
- **Not included:** the database driver, the RAG and LLM code, the stand-in API, and `ui/verify.py`,
  all of which need modules the screen doesn't.
- Runs as an ordinary user (uid 10001), not root.
- A health check on Streamlit's `/_stcore/health`.
- The "Deploy" toolbar button is hidden.

Size: about 830 MB, mostly Streamlit's data libraries (pandas, pyarrow).

## Environment variables
`API_URL` (default in the image `http://api:8080`), `API_TIMEOUT_S` (420).

## How to know it works (checked 2026-11-11)
| Check | Result |
|---|---|
| `docker inspect` health status | `healthy` within 20 s |
| Process user | `uid=10001(app)` |
| Reaches the API from inside the container | `/health` answered |
| No unneeded code | `import psycopg` fails inside the image, as intended |
| The worked example in Chrome against the container | Banner fixed, flag table, evidence including the CPIC chunk with its ClinPGx link; the API address shown cleanly; no Deploy button |

Found while checking: the sidebar showed the API address as `http://host\.docker\.internal:8080`.
Escaping backslashes show literally inside a code span, and the Mac runs used `localhost`, which
has nothing to escape. Fixed, and a test covers it.
