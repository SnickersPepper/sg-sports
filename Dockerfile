# SG Sports backend. Build: docker compose build   (see docs/OPERATOR-GUIDE.md)
FROM python:3.12.7-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --user-group --no-create-home sgsports \
 && mkdir -p /data && chown 10001:10001 /data

WORKDIR /app
# Every runtime module and bundled data file. Tests, Android sources, evidence and private files are not copied (.dockerignore).
COPY --chown=root:root *.py providers.json fixtures.json lvbp_data.json lvbp_overrides.json soccer_teams.json mlb_teams.json ncaa_conferences.json ./
COPY --chown=root:root web ./web
COPY --chown=root:root scripts/ops_backup.py ./scripts/ops_backup.py

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SG_DATA=/data SG_SECRETS=/run/secrets
USER 10001:10001
VOLUME /data
EXPOSE 8099
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8099/health',timeout=2)"
CMD ["python", "app.py"]
