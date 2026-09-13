# Phase 10 - FastAPI service. The dashboard on Vercel talks to this.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY requirements.txt requirements-api.txt ./
RUN pip install -r requirements.txt -r requirements-api.txt

COPY agent/ ./agent/
COPY api/ ./api/
COPY seed/ ./seed/
COPY sim/ ./sim/
COPY eval/ ./eval/
COPY main.py ./

# The judge (Antigravity CLI) is a local binary and is NOT available in a
# container. A hosted deployment therefore serves cached verdicts and reports
# a judge failure for anything uncached - which the UI surfaces honestly as an
# invalid run rather than inventing a number.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
