"""Read-only M10 configuration preflight; never print profiles, paths or credentials."""

import argparse
import json
from pathlib import Path

from .config import InvalidInput
from .query_routing_config import load
from .query_routing_preview import preview
from .query_routing_probe import probe


def _backup_index(value: str) -> int:
    if len(value) != 1 or value not in "1234":
        raise argparse.ArgumentTypeError("backup index must be an integer from 1 to 4")
    return int(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-file", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--probe-local", action="store_true", help="显式检查回环模型目录，不做推理")
    mode.add_argument(
        "--probe-local-auth", action="store_true", help="显式读取本地凭证检查模型目录"
    )
    mode.add_argument("--preview-answer-file", type=Path, help="离线校验答案文件，仅输出脱敏摘要")
    parser.add_argument(
        "--preview-backup", type=_backup_index, help="离线检查已声明备用的序号（1–4）"
    )
    args = parser.parse_args()
    if args.preview_backup is not None and args.preview_answer_file is None:
        parser.error("--preview-backup requires --preview-answer-file")
    try:
        config = load(args.policy_file)
        if args.preview_answer_file is not None:
            result = preview(config, args.preview_answer_file, backup_index=args.preview_backup)
        else:
            result = (
                probe(config, read_credentials=args.probe_local_auth)
                if args.probe_local or args.probe_local_auth
                else config.inspect()
            )
    except (OSError, InvalidInput):
        print(json.dumps({"state": "rejected", "reason": "routing_preflight_failed"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return (
        1
        if (args.probe_local or args.probe_local_auth)
        and result["local_probe"]["state"] == "failed"
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
