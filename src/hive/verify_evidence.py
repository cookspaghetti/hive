"""Verify a portable HIVE evidence package."""

from __future__ import annotations

import argparse
import json

from hive.vault.package import verify_evidence_package


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", help="Path to a .evidence.zip package")
    args = parser.parse_args()
    result = verify_evidence_package(args.package)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
