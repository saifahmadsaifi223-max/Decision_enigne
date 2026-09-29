# Dockerfile for Hugging Face Spaces deployment (Docker Space).
# HF Spaces' free CPU tier gives 16GB RAM, which comfortably fits the
# embedding + reranker models that OOM'd on Render's 512MB free tier.
#
# HF Spaces Docker convention: the app must listen on port 7860.

FROM python:3.10-slim

WORKDIR /app

# System deps some ML libraries need at build time
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bake the policy index in at build time rather than at container start --
# this avoids re-running ingestion on every cold start/restart, which is
# wasted work since the source PDF never changes between deploys.
RUN python -m app.ingestion data/USGIC-CSCIndividualHealthInsurance_2017-2018.pdf

EXPOSE 7860

CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "7860"]
