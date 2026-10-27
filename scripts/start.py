"""Run copied source with Python 3.10+; frontend files need no build step."""
import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Start Eval Studio in mock-only mode (Python runtime only).')
    parser.add_argument('--host', default='127.0.0.1', help='Bind address; use 0.0.0.0 for server access.')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--data-dir', type=Path, help='Writable runtime directory outside the application. Overrides EVAL_DATA_DIR.')
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error('Python 3.10 or newer is required. The environment name is unrestricted.')
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    repo_root = Path(__file__).resolve().parents[1]
    if not all((repo_root / 'frontend' / name).is_file() for name in ('index.html', 'app.js', 'style.css')):
        parser.error('Missing frontend files. Copy the entire project, including frontend/index.html, app.js and style.css.')
    if args.data_dir is not None:
        os.environ['EVAL_DATA_DIR'] = str(args.data_dir.expanduser().resolve())
    try:
        import uvicorn
    except ModuleNotFoundError:
        parser.error('Python dependencies are missing. Run: python -m pip install -r requirements.txt')
    sys.path.insert(0, str(repo_root))
    print(f'Eval Studio (mock only): http://{args.host}:{args.port}', flush=True)
    uvicorn.run('backend.app:app', host=args.host, port=args.port, workers=1)


if __name__ == '__main__':
    main()
