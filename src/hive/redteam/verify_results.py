"""Verify a finalized HIVE red-team result directory offline."""

from __future__ import annotations

import argparse
import json

from hive.redteam.integrity import verify_result_directory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", help="Timestamped red-team result directory")
    args = parser.parse_args()
    result = verify_result_directory(args.directory)
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
