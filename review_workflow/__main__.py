from __future__ import annotations

import argparse
from pathlib import Path

from .model_io import load_gateway
from .workflow import build_prompt, run_review


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch review workflow V1")
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build-prompt")
    build.add_argument("config", type=Path)
    build.add_argument("--gateway", default="mock")
    build.add_argument("--force", action="store_true")

    run = commands.add_parser("run")
    run.add_argument("config", type=Path)
    run.add_argument("--gateway", default="mock")

    args = parser.parse_args()
    gateway = load_gateway(args.gateway)
    if args.command == "build-prompt":
        run_dir = build_prompt(args.config, gateway, args.force)
    else:
        run_dir = run_review(args.config, gateway)
    print(run_dir)


if __name__ == "__main__":
    main()
