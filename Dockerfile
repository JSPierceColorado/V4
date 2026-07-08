FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway/source ZIPs have intermittently arrived with NUL bytes in Python files.
# Strip only NUL bytes, then compile every Python source file so boot failures are caught at build time.
RUN python - <<'PY'
from pathlib import Path
for path in Path('.').rglob('*.py'):
    data = path.read_bytes()
    if b'\x00' in data:
        path.write_bytes(data.replace(b'\x00', b''))
        print(f'Removed NUL bytes from {path}')
print('Python source NUL cleanup complete')
PY

RUN python - <<'PY'
from pathlib import Path
import py_compile
for path in Path('.').rglob('*.py'):
    py_compile.compile(str(path), doraise=True)
print('Python source compile check passed')
PY

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
