from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def clean_python_sources() -> None:
    for cache_dir in Path('.').rglob('__pycache__'):
        for item in cache_dir.glob('*'):
            try:
                item.unlink()
            except OSError:
                pass
        try:
            cache_dir.rmdir()
        except OSError:
            pass
    for path in Path('.').rglob('*.py'):
        data = path.read_bytes()
        if b'\x00' in data:
            path.write_bytes(data.replace(b'\x00', b''))
            print(f'Removed NUL bytes from {path}', flush=True)


clean_python_sources()
port = os.getenv('PORT', '8000')
sys.argv = ['uvicorn', 'main:app', '--host', '0.0.0.0', '--port', port]
runpy.run_module('uvicorn', run_name='__main__')
