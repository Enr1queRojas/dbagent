import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import yaml
from core.schema_retriever import SchemaRetriever


class RetrieverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.source = {'database_name': 'test'}
        self.objects = {}
        for name in ('Customers', 'Orders', 'Products'):
            self.objects[f'[dbo].[{name}]'] = {'table_name': name, 'table_type': 'BASE TABLE',
                'columns': [{'column_name': 'ID', 'data_type': 'int'}], 'primary_key': [], 'foreign_keys': []}
        for target in ('Customers', 'Products'):
            self.objects['[dbo].[Orders]']['foreign_keys'].append({'constraint_name': 'FK_' + target,
                'column_position': 1, 'source_column': target + 'ID', 'target_schema': 'dbo',
                'target_table': target, 'target_column': 'ID'})
        raw = json.dumps({'source': self.source, 'objects': self.objects}).encode()
        (self.path/'catalog.json').write_bytes(raw)
        (self.path/'manifest.json').write_text(json.dumps({'source': self.source, 'catalog_version': 1,
            'refreshed_at_utc': '2026-01-01', 'catalog_sha256': hashlib.sha256(raw).hexdigest()}))
        self.business = {'source': self.source, 'objects': {}, 'metrics': {}}
        self.write_business()
        self.config = self.path/'retrieval.yml'
        self.config.write_text('top_k: 2\nmax_context_chars: 4000\nsynonyms: [[cliente, customers], [producto, products]]\n')
        self.retriever = SchemaRetriever(self.path, self.config)

    def write_business(self):
        (self.path/'business.yml').write_text(yaml.safe_dump(self.business))

    def test_bridge_and_fk(self):
        result = self.retriever.retrieve('cliente producto')
        self.assertEqual(set(result.selected_objects), set(self.objects))
        self.assertEqual(len(json.loads(result.context)['joins']), 2)

    def test_business_reload_without_writes(self):
        self.assertFalse(self.retriever.retrieve('unicornio').selected_objects)
        self.business['objects']['[dbo].[Products]'] = {'keywords': ['unicornio']}
        self.write_business()
        original = (self.path/'business.yml').read_bytes()
        self.assertEqual(self.retriever.retrieve('unicornio').selected_objects, ['[dbo].[Products]'])
        self.assertEqual(original, (self.path/'business.yml').read_bytes())

    def test_hash_failure(self):
        with (self.path/'catalog.json').open('ab') as handle:
            handle.write(b' ')
        with self.assertRaisesRegex(ValueError, 'no coinciden'):
            self.retriever.retrieve('cliente')

    def test_budget(self):
        self.config.write_text('max_context_chars: 500\n')
        result = self.retriever.retrieve('Products Customers')
        self.assertLessEqual(len(result.context), 500)
        self.assertTrue(result.warnings)
        json.loads(result.context)

    def test_metric_references(self):
        self.business['metrics']['revenue'] = {'description': 'facturacion', 'objects': ['[dbo].[Orders]'], 'formula': 'example'}
        self.write_business()
        result = self.retriever.retrieve('facturacion')
        self.assertIn('revenue', json.loads(result.context)['metrics'])

    def test_lock(self):
        (self.path/'.refresh.lock').write_text('123')
        with self.assertRaisesRegex(ValueError, 'actualizando'):
            self.retriever.retrieve('cliente')


if __name__ == '__main__':
    unittest.main()
