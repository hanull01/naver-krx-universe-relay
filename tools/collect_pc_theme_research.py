"""Stage validated NAVER PC theme rankings without enabling report use."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import theme_research as themes  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', default='data/research/theme-ranking')
    parser.add_argument('--max-pages', type=int, default=themes.PC_THEME_MAX_PAGES)
    args = parser.parse_args(argv)
    if not 1 <= args.max_pages <= themes.PC_THEME_MAX_PAGES:
        parser.error(f'--max-pages must be 1..{themes.PC_THEME_MAX_PAGES}')
    payload = themes.collect_pc_theme_periods(max_pages=args.max_pages)
    try:
        themes.write_pc_theme_snapshot(payload, args.output_dir)
    except themes.market.MarketApiError:
        print('PC_THEME_RESEARCH diagnostics=' + json.dumps(
            themes.pc_theme_period_diagnostics(payload.get('periods', {})),
            ensure_ascii=False, sort_keys=True))
        raise
    print('PC_THEME_RESEARCH status=OK output=' + str(Path(args.output_dir) / 'latest.json'))


if __name__ == '__main__':
    main()
