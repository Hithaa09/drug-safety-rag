# The Streamlit screen (spec service "ui": depends on api, talks only to it).
# Build from the repository root:
#   docker build -f docker/ui.Dockerfile -t drug-safety-ui .
#   docker run -p 8501:8501 -e API_URL=http://api:8080 drug-safety-ui
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src \
    API_URL=http://api:8080

WORKDIR /app
COPY docker/ui-requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Only what the screen imports. The stand-in API (ui/stub_api.py) and ui/verify.py are dev tools
# that need the llm module, so they are left out.
COPY src/drug_safety/__init__.py src/drug_safety/config.py src/drug_safety/
COPY src/drug_safety/contracts/ src/drug_safety/contracts/
COPY src/drug_safety/ui/__init__.py src/drug_safety/ui/app.py src/drug_safety/ui/api_client.py \
     src/drug_safety/ui/view.py src/drug_safety/ui/patient_input.py src/drug_safety/ui/examples.yaml \
     src/drug_safety/ui/
COPY scope/lab_codes.yaml scope/lab_codes.yaml

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8501
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4).status != 200)"

CMD ["streamlit", "run", "src/drug_safety/ui/app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", \
     "--browser.gatherUsageStats=false", "--client.toolbarMode=viewer"]
