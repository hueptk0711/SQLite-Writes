import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_json(path: str | Path) -> Any:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(obj: Any, path: str | Path, indent: int = 2) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)


def _yaml_scalar(value: str) -> Any:
    """Parse the small scalar subset used by this bundle's flat YAML files."""
    value = value.strip()
    if not value:
        return ''
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass
    lowered = value.lower()
    if lowered in {'true', 'yes', 'on'}:
        return True
    if lowered in {'false', 'no', 'off'}:
        return False
    if lowered in {'null', 'none', '~'}:
        return None
    try:
        return float(value) if any(ch in value for ch in '.eE') else int(value)
    except ValueError:
        return value.strip('"\'')


def load_config(path: str | Path) -> Dict[str, Any]:
    """Load YAML config, with a dependency-free fallback for flat key/value files."""
    text = Path(path).read_text(encoding='utf-8')
    if text.lstrip().startswith('{'):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f'Config must be an object: {path}')
        return data
    try:
        import yaml

        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError(f'Config must be an object: {path}')
        return data
    except ModuleNotFoundError:
        data: Dict[str, Any] = {}
        for lineno, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith('#'):
                continue
            if ':' not in line:
                raise ValueError(f'Unsupported YAML at {path}:{lineno}: {raw_line}')
            key, value = line.split(':', 1)
            data[key.strip()] = _yaml_scalar(value)
        return data


def iter_jsonl(path: str | Path) -> Iterable[Dict[str, Any]]:
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def write_jsonl(rows: Iterable[Dict[str, Any]], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        for obj in rows:
            f.write(json.dumps(obj, ensure_ascii=False) + '\n')


def append_jsonl(obj: Dict[str, Any], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(obj, ensure_ascii=False) + '\n')


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def read_id_file(path: str | Path) -> set[str]:
    return {x.strip() for x in Path(path).read_text(encoding='utf-8').splitlines() if x.strip()}


def find_db_path(db_root: str | Path, db_id: str) -> Path:
    db_root = Path(db_root)
    candidates: list[Path] = []
    for ext in ['sqlite', 'db', 'sqlite3']:
        candidates.extend(db_root.glob(f'**/{db_id}.{ext}'))
    if not candidates:
        raise FileNotFoundError(f'Cannot find SQLite DB for db_id={db_id} under {db_root}')
    return candidates[0]


def quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def workspace_tmp_dir(root: str | Path | None = None) -> Path:
    """Return a writable temp directory under the current workspace by default."""
    env = os.environ.get('NLDBWRITE_TMP_DIR')
    base = Path(root or env or '.tmp/nldbwrite')
    base.mkdir(parents=True, exist_ok=True)
    return base


def copy_sqlite_db(db_path: str | Path, tmp_root: str | Path | None = None) -> Path:
    """Copy a SQLite DB to a writable workspace temp path and return the copy path."""
    db_path = Path(db_path)
    tmpdir = workspace_tmp_dir(tmp_root)
    out = tmpdir / f'{db_path.stem}_{uuid.uuid4().hex}{db_path.suffix}'
    shutil.copyfile(db_path, out)
    out.chmod(0o666)
    return out


def git_commit(cwd: str | Path = '.') -> str | None:
    try:
        res = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=False,
        )
        if res.returncode != 0:
            return None
        return res.stdout.strip() or None
    except Exception:
        return None


def code_version(cwd: str | Path = '.') -> tuple[str, str]:
    """Return a non-empty reproducibility identifier and its source."""
    commit = git_commit(cwd)
    if commit:
        return commit, 'git'
    env_version = os.environ.get('NLDBWRITE_CODE_VERSION')
    if env_version:
        return env_version, 'environment'
    version_file = Path(cwd) / 'BUNDLE_VERSION'
    if version_file.exists():
        value = version_file.read_text(encoding='utf-8').strip()
        if value:
            return f'bundle:{value}', 'bundle'
    return 'bundle:unknown', 'fallback'
