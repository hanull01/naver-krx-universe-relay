"""Render the approved A v1.1 views from one published report contract."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.market_dashboard_renderer import (
    TEMPLATE_DIR,
    TemplateUnavailable,
    render_contract_views,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render Markdown and A v1.1 HTML from a published report contract."
    )
    parser.add_argument(
        "--input",
        default="data/monitoring/latest-report-contract.json",
        help="published report contract JSON",
    )
    parser.add_argument("--output-dir", default="data/reports")
    parser.add_argument("--template-dir", default=str(TEMPLATE_DIR))
    args = parser.parse_args(argv)

    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = render_contract_views(
            payload,
            Path(args.output_dir),
            template_dir=Path(args.template_dir),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TemplateUnavailable) as exc:
        print(json.dumps({"htmlStatus": "TEMPLATE_UNAVAILABLE", "error": str(exc)}, ensure_ascii=False))
        return 2

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
