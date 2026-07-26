from __future__ import annotations

import argparse
import json

from power_reading import available_assets, read_asset


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch one live asset power reading in MW.")
    parser.add_argument("asset", choices=available_assets())
    args = parser.parse_args()
    print(json.dumps(read_asset(args.asset).to_dict(), ensure_ascii=True))


if __name__ == "__main__":
    main()
