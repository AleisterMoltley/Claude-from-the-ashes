FROM python:3.12-slim

WORKDIR /app

# System deps for MCP stdio transport and general tooling
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl jq ripgrep tree nodejs npm \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
