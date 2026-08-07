#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from recipe_engine.nc_dps_wave_a_pipeline import build


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packets", required=True, type=Path)
    parser.add_argument("--receipts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.packets, args.receipts, args.output), indent=2))


if __name__ == "__main__":
    main()
