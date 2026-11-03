"""Explicit offline upgrade; never starts FastAPI or calls a model."""
import argparse
import os
import sys
from pathlib import Path


def main():
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    parser = argparse.ArgumentParser(description='停服后检查或转换 v1.1 存储。建议先 --check；--apply 自动完整备份。')
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('EVAL_DATA_DIR', repo.parent / 'review_workflow_data')))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='只读检查数据库结构与完整性，不转换、不调用模型。')
    mode.add_argument('--apply', action='store_true', help='确认已停止旧版服务后，备份并显式转换。')
    args = parser.parse_args()
    from backend.migration import migrate
    try:
        migrate(args.data_dir, apply=args.apply)
    except Exception as exc:
        # Only this offline tool handles database errors; no secrets are read here.
        parser.exit(1, f'升级未完成：{exc}\n')


if __name__ == '__main__':
    main()
