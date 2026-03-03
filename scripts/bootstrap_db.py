from __future__ import annotations

import argparse
from pathlib import Path

from oam.persistence.db import SQLiteMetadataStore


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    args = ap.parse_args()

    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)

    store = SQLiteMetadataStore(db)
    store.init_schema()
    print(f"OK: initialized {db}")


if __name__ == "__main__":
    main()
