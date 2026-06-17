import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from rbac_rag.env_check import check_env_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Databricks .env values without printing secrets.")
    parser.add_argument(
        "--file",
        default=".env",
        help="Path to .env file. Defaults to .env in the current directory.",
    )
    args = parser.parse_args()

    env_path = Path(args.file)
    rows, ok = check_env_file(env_path)

    print(f"env_file={env_path.resolve()}")
    for row in rows:
        status = "OK" if row["valid"] else "MISSING_OR_PLACEHOLDER"
        print(f"{row['key']}={row['value']} [{status}]")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
