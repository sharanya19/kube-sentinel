# Kube-Sentinel: agent + MCP server + approval UI in one image.
#   minikube image build -t kube-sentinel:0.1.0 .      (build straight into minikube)
#   docker build -t ghcr.io/<you>/kube-sentinel:0.1.0 .
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RUNBOOKS_MODEL_CACHE=/opt/models \
    KUBE_SENTINEL_UI_HOST=0.0.0.0

WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake the embedding model into the image: pods start fast and need no internet for it.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/models')"

COPY agent/ agent/
COPY mcp_server/ mcp_server/
COPY rag/ rag/
COPY ui/ ui/
COPY runbooks/ runbooks/

# Run as an unprivileged user.
RUN useradd --uid 10001 --no-create-home sentinel && chown -R 10001 /opt/models
USER 10001

EXPOSE 8000
CMD ["python", "-m", "ui"]
