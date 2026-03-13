#!/usr/bin/env python3
"""Convenience CLI wrapper for openclaw-model-ops skill.

Examples:
  python3 model_ops.py check --providers ccll,kegui
  python3 model_ops.py add --from-message "https://api.xx sk-xxx codex5.3"
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import List


HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_MATRIX = os.path.join(HERE, "model_matrix.py")
ADD_PROVIDER = os.path.join(HERE, "add_provider.py")


def detect_default_config() -> str:
    candidates = [
        os.environ.get("OPENCLAW_CONFIG", ""),
        os.path.expanduser("~/.openclaw/openclaw.json"),
        os.path.expanduser("~/.config/openclaw/openclaw.json"),
        "/etc/openclaw/openclaw.json",
        "/root/.openclaw/openclaw.json",
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    # best effort default for user installs
    return os.path.expanduser("~/.openclaw/openclaw.json")


DEFAULT_CONFIG = detect_default_config()


def run_cmd(args: List[str]) -> int:
    proc = subprocess.run(args)
    return int(proc.returncode)


def main() -> int:
    p = argparse.ArgumentParser(description="openclaw-model-ops CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="run model availability matrix")
    p_check.add_argument("--config", default=DEFAULT_CONFIG)
    p_check.add_argument("--providers", default="")
    p_check.add_argument("--timeout", type=float, default=12.0)
    p_check.add_argument("--output", default="")
    p_check.add_argument("--json", dest="json_output", default="")

    p_add = sub.add_parser("add", help="add/update provider")
    p_add.add_argument("--config", default=DEFAULT_CONFIG)
    p_add.add_argument("--provider", default="")
    p_add.add_argument("--base-url", default="")
    p_add.add_argument("--api-key", default="")
    p_add.add_argument("--models", default="")
    p_add.add_argument("--api-mode", default="openai-completions")
    p_add.add_argument("--user-agent", default="")
    p_add.add_argument("--set-primary", action="store_true")
    p_add.add_argument("--from-message", default="")
    p_add.add_argument("--no-fuzzy", action="store_true")
    p_add.add_argument("--timeout", type=float, default=15.0)
    p_add.add_argument("--models-dev-url", default="https://models.dev/api.json")
    p_add.add_argument("--no-models-dev", action="store_true")

    args = p.parse_args()

    if args.cmd == "check":
        cmd = [
            sys.executable,
            MODEL_MATRIX,
            "--config",
            args.config,
            "--timeout",
            str(args.timeout),
        ]
        if args.providers:
            cmd += ["--providers", args.providers]
        if args.output:
            cmd += ["--output", args.output]
        if args.json_output:
            cmd += ["--json", args.json_output]
        return run_cmd(cmd)

    if args.cmd == "add":
        cmd = [
            sys.executable,
            ADD_PROVIDER,
            "--config",
            args.config,
            "--api-mode",
            args.api_mode,
            "--timeout",
            str(args.timeout),
            "--models-dev-url",
            args.models_dev_url,
        ]
        if args.provider:
            cmd += ["--provider", args.provider]
        if args.base_url:
            cmd += ["--base-url", args.base_url]
        if args.api_key:
            cmd += ["--api-key", args.api_key]
        if args.models:
            cmd += ["--models", args.models]
        if args.user_agent:
            cmd += ["--user-agent", args.user_agent]
        if args.from_message:
            cmd += ["--from-message", args.from_message]
        if args.set_primary:
            cmd += ["--set-primary"]
        if args.no_fuzzy:
            cmd += ["--no-fuzzy"]
        if args.no_models_dev:
            cmd += ["--no-models-dev"]
        return run_cmd(cmd)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
