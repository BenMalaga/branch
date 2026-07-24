# branch: the open-source AI city-planning GIS platform, containerized.
# Build:  docker build -t branch .
# Run:    docker run --rm -p 8080:8080 branch
# Deploy: any container host (Fly.io, Render, Railway). The Python geospatial
# backend does NOT fit Vercel/serverless; use a container host.
FROM python:3.12-slim

WORKDIR /app

# Copy metadata first so dependency install is cached across code changes.
COPY pyproject.toml README.md ./
COPY branch ./branch
RUN pip install --no-cache-dir -e ".[full]"

COPY . .

EXPOSE 8080
CMD ["sh", "-c", "branch serve --host 0.0.0.0 --port ${PORT:-8080}"]
