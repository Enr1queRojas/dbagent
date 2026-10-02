"""Refresh the SQL Server metadata cache without calling the LLM.

Run: python refresh_schema.py
Only metadata is read. Existing business.yml is never overwritten.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent


def encode_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def object_key(schema: str, table: str) -> str:
    return '[' + schema.replace(']', ']]') + '].[' + table.replace(']', ']]') + ']'


def complete(result, label: str) -> list[dict]:
    if result.truncated:
        raise ValueError(f"Metadatos incompletos en {label}. Aumenta max_rows en database.yml y reintenta.")
    return result.to_records()


def collect_catalog(db) -> dict:
    identity = complete(db.test_connection(), 'identity')
    if len(identity) != 1:
        raise ValueError('No se pudo identificar la base de datos')
    source = identity[0]
    tables = complete(db.list_tables(), 'tables')
    columns = complete(db.query('''
        SELECT TABLE_SCHEMA AS schema_name, TABLE_NAME AS table_name,
               ORDINAL_POSITION AS position, COLUMN_NAME AS column_name,
               DATA_TYPE AS data_type, CHARACTER_MAXIMUM_LENGTH AS max_length,
               NUMERIC_PRECISION AS numeric_precision, NUMERIC_SCALE AS numeric_scale,
               IS_NULLABLE AS is_nullable, COLUMN_DEFAULT AS default_value
        FROM INFORMATION_SCHEMA.COLUMNS
        ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
    '''), 'columns')
    primary_keys = complete(db.get_primary_keys(), 'primary_keys')
    relationships = complete(db.get_relationships(), 'relationships')
    objects = {}
    for item in tables:
        key = object_key(item['schema_name'], item['table_name'])
        objects[key] = {**item, 'columns': [], 'primary_key': [], 'foreign_keys': []}
    for column in columns:
        key = object_key(column['schema_name'], column['table_name'])
        if key not in objects:
            raise ValueError('El esquema cambió durante la extracción; vuelve a ejecutar')
        objects[key]['columns'].append({k: v for k, v in column.items() if k not in ('schema_name', 'table_name')})
    for item in primary_keys:
        key = object_key(item['schema_name'], item['table_name'])
        if key in objects:
            objects[key]['primary_key'].append({'column_name': item['column_name'], 'position': item['key_position']})
    for item in relationships:
        key = object_key(item['source_schema'], item['source_table'])
        if key in objects:
            objects[key]['foreign_keys'].append(item)
    for obj in objects.values():
        if not obj['columns']:
            raise ValueError(f"Objeto sin columnas visibles: {obj['table_name']}. Revisa permisos o cambios concurrentes.")
        obj['columns'].sort(key=lambda c: c['position'])
        obj['primary_key'].sort(key=lambda c: c['position'])
        obj['foreign_keys'].sort(key=lambda c: (c['constraint_name'], c['column_position']))
    return {'format_version': 1, 'source': source, 'objects': objects}


def business_template(catalog: dict) -> dict:
    return {
        'format_version': 1,
        'source': catalog['source'],
        'metrics': {},
        'objects': {
            key: {'description': '', 'role': 'unclassified', 'grain': '', 'keywords': [], 'notes': ''}
            for key in sorted(catalog['objects'])
        },
    }


def atomic_write(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def refresh_lock(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / '.refresh.lock'
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError('Hay otra actualización o un lock residual. Revisa schema/.refresh.lock.') from exc
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


def publish(catalog: dict, folder: Path) -> dict:
    """Caller owns the lock. Manifest is written last as the commit marker."""
    catalog_path = folder / 'catalog.json'
    business_path = folder / 'business.yml'
    manifest_path = folder / 'manifest.json'
    old_catalog = json.loads(catalog_path.read_text(encoding='utf-8')) if catalog_path.exists() else None
    old_manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    for existing in (old_catalog, old_manifest):
        if existing and existing.get('source') != catalog['source']:
            raise ValueError('Origen o usuario distinto. Usa otra carpeta con --output para evitar mezclar catálogos.')
    template = business_template(catalog)
    if business_path.exists():
        business = yaml.safe_load(business_path.read_text(encoding='utf-8'))
        if not isinstance(business, dict) or not isinstance(business.get('objects'), dict):
            raise ValueError('business.yml debe contener un mapa objects; no se modificó ningún archivo.')
        if business.get('source') != catalog['source']:
            raise ValueError('business.yml pertenece a otro origen/usuario; usa otra carpeta.')
    else:
        business = template
    old_objects = (old_catalog or {}).get('objects', {})
    current = catalog['objects']
    changes = {
        'added': sorted(current.keys() - old_objects.keys()),
        'removed': sorted(old_objects.keys() - current.keys()),
        'modified': sorted(k for k in current.keys() & old_objects.keys() if current[k] != old_objects[k]),
    }
    encoded = encode_json(catalog)
    hash_value = digest(encoded)
    old_version = old_manifest.get('catalog_version', 0)
    if type(old_version) is not int or old_version < 0:
        raise ValueError('catalog_version inválida en manifest.json')
    changed = old_manifest.get('catalog_sha256') != hash_value
    manifest = {
        'format_version': 1,
        'source': catalog['source'],
        'refreshed_at_utc': datetime.now(timezone.utc).isoformat(),
        'catalog_version': old_version + int(changed),
        'catalog_sha256': hash_value,
        'object_count': len(current),
        'column_count': sum(len(o['columns']) for o in current.values()),
        'changes': changes,
        'business_missing_objects': sorted(current.keys() - business['objects'].keys()),
        'business_orphan_objects': sorted(business['objects'].keys() - current.keys()),
        'scope': 'Metadata visible to source.login_name; not an authorization policy.',
    }
    # Everything is fetched/validated before the first write; business stays byte-identical.
    if not business_path.exists():
        atomic_write(business_path, yaml.safe_dump(template, allow_unicode=True, sort_keys=False).encode('utf-8'))
    if not catalog_path.exists() or catalog_path.read_bytes() != encoded:
        atomic_write(catalog_path, encoded)
    atomic_write(manifest_path, encode_json(manifest))
    return manifest


def refresh(db, folder: Path) -> dict:
    with refresh_lock(folder):
        return publish(collect_catalog(db), folder)


def main() -> int:
    parser = argparse.ArgumentParser(description='Crear o actualizar catálogo local de SQL Server')
    parser.add_argument('--config', type=Path, default=ROOT / 'config' / 'database.yml')
    parser.add_argument('--output', type=Path, default=ROOT / 'schema')
    args = parser.parse_args()
    # Lazy import allows offline cache tests without a native ODBC installation.
    try:
        from core.database_client import DatabaseClient, DatabaseError
    except ImportError as exc:
        print(f'Faltan dependencias o runtime ODBC: {exc}')
        return 1
    try:
        result = refresh(DatabaseClient.from_yaml(args.config), args.output.resolve())
    except (DatabaseError, ValueError, OSError, yaml.YAMLError) as exc:
        print(f'No se pudo actualizar el catálogo: {exc}')
        return 1
    print(f"Catálogo actualizado: {result['object_count']} objetos, {result['column_count']} columnas; versión {result['catalog_version']}.")
    print(f"Carpeta: {args.output.resolve()}")
    for label, values in result['changes'].items():
        print(f'{label}: {len(values)}')
    if result['business_missing_objects']:
        print('Añade estas entradas a business.yml cuando definas su significado:')
        print('\n'.join(result['business_missing_objects']))
    print('business.yml conservado. No se consultaron filas de negocio ni se llamó al LLM.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
