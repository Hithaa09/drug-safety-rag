# Person B commands. `make help` lists them. Targets are added as each module is built.

PY ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: help setup db db-stop test lint format check ingest-dailymed verify-dailymed chunk-labels verify-chunks fetch-openfda verify-openfda fetch-classes verify-classes check-updates eval-retrieval db-migrate store-labels verify-store verify-curation curation-review select-papers verify-papers-selection fetch-papers parse-papers verify-papers chunk-papers verify-paper-chunks store-papers eval-flags fetch-drug-names verify-drug-names llm-cases verify-llm llm-bundle llm-ping eval-llm stub-api ui verify-ui fetch-cpic chunk-cpic verify-cpic store-cpic eval-report fetch-dpwg chunk-dpwg verify-dpwg store-dpwg fetch-bdpm bdpm-sheet chunk-bdpm verify-bdpm store-bdpm

help: ## List commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

setup: ## Create .venv, install with dev tools, create data folders and .env
	test -d $(VENV) || $(PY) -m venv $(VENV)
	$(BIN)/pip install -q -U pip
	$(BIN)/pip install -q -e ".[dev,rag,llm,ui]"
	mkdir -p data/raw data/cache data/outputs
	@test -f .env || cp .env.example .env

db: ## Start local PostgreSQL + pgvector (dev only)
	docker compose -f docker-compose.dev.yml up -d

db-stop: ## Stop the local database
	docker compose -f docker-compose.dev.yml down

test: ## Run tests
	$(BIN)/pytest

lint: ## Check style
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: ## Fix style
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

check: lint test ## Lint and test

ingest-dailymed: ## Fetch and split the pinned DailyMed labels for all v1 drugs
	$(BIN)/python -m drug_safety.ingest.dailymed load

verify-dailymed: ## Offline checks that the DailyMed output is complete and correct
	$(BIN)/python -m drug_safety.ingest.dailymed verify

chunk-labels: ## Split the parsed labels into evidence chunks (needs ingest-dailymed first)
	$(BIN)/python -m drug_safety.rag.chunking run

verify-chunks: ## Offline checks that the label chunks are complete and correct
	$(BIN)/python -m drug_safety.rag.chunking verify

fetch-openfda: ## openFDA: application holder, labeler role, IDs; proposes better labels
	$(BIN)/python -m drug_safety.ingest.openfda fetch

verify-openfda: ## Offline checks on the openFDA output
	$(BIN)/python -m drug_safety.ingest.openfda verify

fetch-classes: ## Drug classes per v1 drug from NLM RxClass
	$(BIN)/python -m drug_safety.ingest.rxclass fetch

verify-classes: ## Offline checks that every drug is in the right classes
	$(BIN)/python -m drug_safety.ingest.rxclass verify

check-updates: ## Network: has DailyMed published a newer version of any pinned label?
	$(BIN)/python -m drug_safety.ingest.dailymed check-updates

eval-retrieval: ## Retrieval test on 26 gold label cases (downloads the embedding model once)
	$(BIN)/python -m drug_safety.rag.eval_retrieval

db-migrate: ## Create the document-store tables (safe to rerun; needs `make db`)
	$(BIN)/python -m drug_safety.db migrate

store-labels: ## Embed the label chunks and load them into PostgreSQL (one transaction)
	$(BIN)/python -m drug_safety.rag.store load

verify-store: ## Checks on the database, including the 26 gold cases through SQL search
	$(BIN)/python -m drug_safety.rag.store verify

verify-curation: ## Checks on the curated rules (quotes, numbers, severity, coverage)
	$(BIN)/python -m drug_safety.curation.rules verify

curation-review: ## Write docs/curation_review.md: every draft rule with its label quotes
	$(BIN)/python -m drug_safety.curation.rules review

select-papers: ## PubMed: find and select open-access papers (metadata only, ~2 min)
	$(BIN)/python -m drug_safety.ingest.pubmed_select run

verify-papers-selection: ## Offline checks on the paper selection
	$(BIN)/python -m drug_safety.ingest.pubmed_select verify

fetch-papers: ## PMC BioC full text for the selected papers (resumable; ~1 request/second)
	$(BIN)/python -m drug_safety.ingest.pmc fetch

parse-papers: ## Licence decision + kept sections for each fetched paper (offline)
	$(BIN)/python -m drug_safety.ingest.pmc parse

verify-papers: ## Offline checks on the parsed papers and licence decisions
	$(BIN)/python -m drug_safety.ingest.pmc verify

chunk-papers: ## Chunk the full-text papers
	$(BIN)/python -m drug_safety.rag.chunk_papers run

verify-paper-chunks: ## Offline checks on the paper chunks
	$(BIN)/python -m drug_safety.rag.chunk_papers verify

store-papers: ## Embed the paper chunks and load them into PostgreSQL
	$(BIN)/python -m drug_safety.rag.store load --source pmc

fetch-cpic: ## Network: CPIC guideline recommendations for the v1 drugs (CC0)
	$(BIN)/python -m drug_safety.ingest.cpic fetch

chunk-cpic: ## One chunk per CPIC recommendation
	$(BIN)/python -m drug_safety.ingest.cpic parse
	$(BIN)/python -m drug_safety.rag.chunk_cpic run

verify-cpic: ## Offline checks on the CPIC chunks
	$(BIN)/python -m drug_safety.rag.chunk_cpic verify

store-cpic: ## Embed the CPIC chunks and load them into PostgreSQL
	$(BIN)/python -m drug_safety.rag.store load --source cpic

fetch-dpwg: ## Network: ClinPGx's DPWG annotations (CC BY-SA 4.0, non-commercial; Person A E5)
	$(BIN)/python -m drug_safety.ingest.dpwg fetch

chunk-dpwg: ## One chunk per DPWG recommendation, for pairs CPIC does not cover
	$(BIN)/python -m drug_safety.ingest.dpwg parse
	$(BIN)/python -m drug_safety.rag.chunk_dpwg run

verify-dpwg: ## Offline checks on the DPWG chunks
	$(BIN)/python -m drug_safety.rag.chunk_dpwg verify

store-dpwg: ## Embed the DPWG chunks and load them into PostgreSQL
	$(BIN)/python -m drug_safety.rag.store load --source dpwg

fetch-bdpm: ## Network: the French acenocoumarol label page (BDPM, Licence Ouverte 2.0)
	$(BIN)/python -m drug_safety.ingest.bdpm fetch

bdpm-sheet: ## Side-by-side French/English review sheet (docs/translation_review_sintrom.md)
	$(BIN)/python -m drug_safety.rag.chunk_bdpm sheet

chunk-bdpm: ## Chunks from the translation-REVIEWED passages only
	$(BIN)/python -m drug_safety.rag.chunk_bdpm run

verify-bdpm: ## Offline checks: French word for word on the page, sheet up to date, reviews
	$(BIN)/python -m drug_safety.rag.chunk_bdpm verify

store-bdpm: ## Embed the reviewed acenocoumarol passages and load them into PostgreSQL
	$(BIN)/python -m drug_safety.rag.store load --source bdpm

eval-flags: ## Gold retrieval through the real path: Flag -> retriever -> PostgreSQL (26 flags)
	$(BIN)/python -m drug_safety.rag.eval_flags

fetch-drug-names: ## Network: every prescribable drug name (RxNorm) for the LLM validator
	$(BIN)/python -m drug_safety.ingest.rxnorm_names fetch

verify-drug-names: ## Offline checks on the drug name list
	$(BIN)/python -m drug_safety.ingest.rxnorm_names verify

llm-cases: ## Freeze the evidence for the 10 LLM test patients (needs PostgreSQL)
	$(BIN)/python -m drug_safety.llm.eval_llm build

verify-llm: ## Offline LLM-layer checks: cases, prompt sizes, fallback passes the validator, tests
	$(BIN)/python -m drug_safety.llm.eval_llm verify
	$(BIN)/pytest tests/llm

llm-bundle: ## Zip for Colab (notebooks/colab_qwen_vllm.ipynb, Part A)
	$(BIN)/python -m drug_safety.llm.eval_llm bundle

llm-ping: ## Is the model server in MODEL_URL up and serving MODEL_NAME?
	$(BIN)/python -m drug_safety.llm.client ping

eval-llm: ## LLM evaluation on the 10 patients against MODEL_URL (plain, then schema-guided)
	$(BIN)/python -m drug_safety.llm.eval_llm run --label plain
	$(BIN)/python -m drug_safety.llm.eval_llm run --guided --label guided

stub-api: ## Stand-in for Person A's API on :8080 (the 10 example patients; real LLM layer)
	$(BIN)/python -m drug_safety.ui.stub_api

ui: ## Streamlit screen on :8501, talking to API_URL
	$(BIN)/streamlit run src/drug_safety/ui/app.py --browser.gatherUsageStats false

verify-ui: ## All 10 example patients through the stand-in API and the screen (headless), then tests
	$(BIN)/python -m drug_safety.ui.verify
	$(BIN)/pytest tests/ui

eval-report: ## Person B's evaluation report (retrieval, LLM runs, latency, definition of done)
	$(BIN)/python -m drug_safety.evaluation.report
