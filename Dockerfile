FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Fail the build if any Python source file is corrupted with NUL bytes.
RUN python - <<'PY'
from pathlib import Path
for path in Path('.').rglob('*.py'):
    data = path.read_bytes()
    if b'\x00' in data:
        raise SystemExit(f'NUL bytes found in {path}')
print('Python source sanity check passed')
PY

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
