#!/usr/bin/env bash
set -e

# Bring the schema to the latest revision before serving any traffic.
echo "Applying database migrations..."
alembic upgrade head

# Train the ML models on first start if the artifacts (gitignored .joblib files)
# aren't present, e.g. on a host that builds without the Dockerfile's train step.
# Set ML_CRIME_INDEX_SOURCE=ncrb to ground the safety model's crime index in real NCRB data.
echo "Ensuring ML models exist..."
python -m app.ml.train_all --if-missing --crime-index-source "${ML_CRIME_INDEX_SOURCE:-ncrb}" --report-path ""

# Optionally seed demo data on first boot (set SEED_ON_START=true in the env).
if [ "${SEED_ON_START:-false}" = "true" ]; then
  echo "Seeding demo data..."
  python -m app.scripts.seed || echo "Seed skipped/failed (continuing)."
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${WEB_CONCURRENCY:-1}"
