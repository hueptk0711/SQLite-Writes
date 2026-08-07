import argparse

from nldbwrite.common import load_config, load_json
from nldbwrite.retrieval.diagnostics import save_flat_yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--selection-json', required=True)
    parser.add_argument('--base-config', required=True)
    parser.add_argument('--locked-config', required=True)
    args = parser.parse_args()
    best = (load_json(args.selection_json).get('best') or {})
    required = ['schema_top_k', 'closure', 'value_threshold']
    if any(key not in best for key in required):
        raise SystemExit(f'Incomplete dev selection: {args.selection_json}')
    config = load_config(args.base_config)
    config.update({
        'run_name': str(config.get('run_name', 'm4')) + '_locked_dev',
        'schema_top_k': best['schema_top_k'],
        'schema_closure': best['closure'],
        'value_match_threshold': best['value_threshold'],
    })
    save_flat_yaml(config, args.locked_config)
    print(f'Wrote {args.locked_config}')


if __name__ == '__main__':
    main()
