"""Offline lexical schema retrieval with FK expansion and bounded JSON context."""
from collections import Counter, deque
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata

import yaml

ROOT = Path(__file__).resolve().parents[1]
STOP_WORDS = set('a al con de del el en es la las los para por que un una y quiero cuales cual mostrar dame how what the of by and for to'.split())


def tokens(text: str) -> set[str]:
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c)).lower()
    words = re.findall(r'[a-z0-9]+', text)
    return {w for w in words if len(w) > 1 and w not in STOP_WORDS}


def object_key(schema: str, table: str) -> str:
    return '[' + schema.replace(']', ']]') + '].[' + table.replace(']', ']]') + ']'


def searchable(value) -> str:
    return json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value


@dataclass(frozen=True)
class RetrievalResult:
    context: str
    selected_objects: list[str]
    scores: dict[str, float]
    warnings: list[str]


class SchemaRetriever:
    def __init__(self, schema_dir: str | Path | None = None, config_path: str | Path | None = None):
        self.schema_dir = Path(schema_dir) if schema_dir else ROOT / 'schema'
        self.config_path = Path(config_path) if config_path else ROOT / 'config' / 'retrieval.yml'

    def _load(self):
        folder = self.schema_dir
        if (folder / '.refresh.lock').exists():
            raise ValueError('El catálogo se está actualizando. Reintenta al terminar refresh_schema.py.')
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        raw = (folder / 'catalog.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest.get('catalog_sha256'):
            raise ValueError('Catálogo y manifiesto no coinciden. Ejecuta refresh_schema.py.')
        catalog = json.loads(raw)
        business = yaml.safe_load((folder / 'business.yml').read_text(encoding='utf-8'))
        if not isinstance(business, dict) or not isinstance(business.get('objects'), dict):
            raise ValueError('business.yml debe contener un mapa objects.')
        if business.get('source') != catalog.get('source') or manifest.get('source') != catalog.get('source'):
            raise ValueError('Los archivos de schema pertenecen a orígenes distintos.')
        if not isinstance(catalog.get('objects'), dict):
            raise ValueError('catalog.json no contiene un mapa objects.')
        config = yaml.safe_load(self.config_path.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('retrieval.yml debe contener un mapa.')
        for name, default, minimum, maximum in (
            ('top_k', 4, 1, 20), ('max_objects', 8, 1, 30),
            ('max_join_hops', 3, 0, 6), ('max_context_chars', 16000, 500, 100000),
        ):
            value = config.setdefault(name, default)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f'Valor inválido para {name}')
        groups = config.setdefault('synonyms', [])
        if not isinstance(groups, list) or any(not isinstance(g, list) or any(not isinstance(w, str) for w in g) for g in groups):
            raise ValueError('synonyms debe ser una lista de listas de palabras.')
        if any(not isinstance(v, dict) for v in business['objects'].values()):
            raise ValueError('Cada entrada de business.objects debe ser un mapa.')
        return catalog, business, manifest, config

    @staticmethod
    def _path(graph, selected: list[str], target: str, hops: int):
        queue = deque((node, [node]) for node in selected)
        seen = set(selected)
        while queue:
            node, path = queue.popleft()
            if node == target:
                return path
            if len(path) - 1 >= hops:
                continue
            for neighbor in sorted(graph.get(node, ())):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))
        return None

    def retrieve(self, question: str) -> RetrievalResult:
        if not isinstance(question, str) or not question.strip():
            raise ValueError('Escribe una pregunta no vacía.')
        # Reload every time so manual business.yml edits are reflected immediately.
        catalog, business, manifest, config = self._load()
        objects = catalog['objects']
        query_terms = tokens(question)
        expanded = set(query_terms)
        for group in config['synonyms']:
            terms = set().union(*(tokens(word) for word in group))
            if terms & query_terms:
                expanded.update(terms)
        fields = {}
        for key, obj in objects.items():
            note = business['objects'].get(key, {})
            fields[key] = [
                (tokens(obj['table_name']), 5),
                (tokens(searchable({k: note.get(k, '') for k in ('description', 'keywords', 'grain', 'role')})), 4),
                (tokens(' '.join(c['column_name'] for c in obj['columns'])), 1),
            ]
        frequency = Counter(t for values in fields.values() for t in set().union(*(terms for terms, _ in values)))
        def score(values):
            return sum(weight * sum(math.log(1 + len(objects) / (1 + frequency[t])) for t in terms & expanded)
                       for terms, weight in values)
        boost = config.get('base_table_boost', 2.0)
        if type(boost) not in (int, float) or not 1 <= boost <= 5:
            raise ValueError('base_table_boost debe estar entre 1 y 5.')
        scores = {key: score(values) * (boost if objects[key]['table_type'] == 'BASE TABLE' else 1)
                  for key, values in fields.items()}
        metrics = business.get('metrics', {}) or {}
        if not isinstance(metrics, dict):
            raise ValueError('business.metrics debe ser un mapa.')
        matched_metrics = {}
        warnings = []
        for name, metric in metrics.items():
            if not isinstance(metric, dict):
                continue
            text = searchable({k: metric.get(k, '') for k in ('description', 'keywords')})
            if tokens(str(name) + ' ' + text) & expanded:
                references = metric.get('objects', [])
                if not isinstance(references, list) or any(not isinstance(k, str) for k in references):
                    warnings.append(f'Métrica {name}: objects inválido; omitida.')
                    continue
                if not references or any(k not in objects for k in references):
                    warnings.append(f'Métrica {name}: faltan referencias válidas en objects; omitida.')
                    continue
                matched_metrics[name] = metric
                for key in references:
                    scores[key] += 10
        ranked = sorted((k for k in objects if scores[k] > 0), key=lambda k: (-scores[k], k))[:config['top_k']]
        graph = {k: set() for k in objects}
        joins = {}
        for key, obj in objects.items():
            for fk in obj.get('foreign_keys', []):
                target = object_key(fk['target_schema'], fk['target_table'])
                if target not in objects:
                    continue
                graph[key].add(target)
                graph[target].add(key)
                group = (key, fk['constraint_name'], target)
                joins.setdefault(group, []).append(fk)

        def payload(selected):
            result = {'catalog_version': manifest['catalog_version'],
                      'schema_refreshed_at_utc': manifest['refreshed_at_utc'],
                      'objects': {}, 'joins': [], 'metrics': {},
                      'scope': 'Selected metadata only; no business rows. Missing objects/joins may exist outside this selection.'}
            for key in selected:
                obj = objects[key]
                result['objects'][key] = {
                    'type': obj['table_type'],
                    'columns': [{k: c[k] for k in ('column_name', 'data_type', 'is_nullable', 'max_length', 'numeric_precision', 'numeric_scale') if k in c} for c in obj['columns']],
                    'primary_key': obj.get('primary_key', []),
                    'business': business['objects'].get(key, {}),
                }
            for (source, name, target), pairs in sorted(joins.items()):
                if source in selected and target in selected:
                    result['joins'].append({'name': name, 'source': source, 'target': target,
                        'column_pairs': [[p['source_column'], p['target_column']] for p in sorted(pairs, key=lambda p: p['column_position'])]})
            for name, metric in matched_metrics.items():
                if all(k in selected for k in metric['objects']):
                    result['metrics'][name] = metric
            return json.dumps(result, ensure_ascii=False, separators=(',', ':'))

        selected = []
        for key in ranked:
            if key in selected:
                continue
            path = self._path(graph, selected, key, config['max_join_hops']) if selected else None
            additions = [k for k in (path or [key]) if k not in selected]
            proposed = selected + additions
            if len(proposed) > config['max_objects'] or len(payload(proposed)) > config['max_context_chars']:
                warnings.append(f'{key}: omitido por límite de contexto/objetos (incluye tablas puente).')
                continue
            if selected and path is None:
                warnings.append(f'{key}: sin ruta FK encontrada dentro del límite; no inventar un join.')
            selected = proposed
        context = payload(selected)
        if len(context) > config['max_context_chars']:
            raise ValueError('max_context_chars es demasiado pequeño para el encabezado de contexto.')
        if not selected:
            warnings.append('Sin coincidencias utilizables. Aclara la pregunta o añade descripciones/sinónimos.')
        return RetrievalResult(context, selected, {k: round(scores[k], 3) for k in selected}, warnings)
