import argparse
from pathlib import Path


def read_ids(path: str | Path) -> list[str]:
    return [line.strip() for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def write_subset(source_ids: str | Path, out: str | Path, n: int = 300) -> list[str]:
    ids = read_ids(source_ids)
    if len(ids) < n:
        raise ValueError(f'Requested {n} ids, but only found {len(ids)} in {source_ids}')
    selected = ids[:n]
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text('\n'.join(selected) + '\n', encoding='utf-8')
    return selected


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--source-ids', default='data/splits/augmented900_v1/test_ids.txt')
    ap.add_argument('--out', default='data/splits/augmented900_v1/model_family_subset300_ids.txt')
    ap.add_argument('--n', type=int, default=300)
    args = ap.parse_args()
    selected = write_subset(args.source_ids, args.out, args.n)
    print(f'Wrote {len(selected)} ids to {args.out}')


if __name__ == '__main__':
    main()
