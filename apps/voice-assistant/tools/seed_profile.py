from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from memory.service import apply_profile_file, get_identity_prompt


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed assistant identity into local memory.")
    parser.add_argument(
        "--profile",
        default=str(PROJECT_ROOT / "memory" / "default_profile.json"),
        help="Path to a JSON profile file.",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Optional SQLite database path. Defaults to data/assistant.db.",
    )
    args = parser.parse_args()

    apply_profile_file(args.profile, db_path=args.db)
    print("assistant profile seeded")
    print(get_identity_prompt(db_path=args.db))


if __name__ == "__main__":
    main()
