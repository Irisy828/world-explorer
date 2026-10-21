import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
import collector as app
from demo_data import fetch

class CollectorTests(unittest.TestCase):
    def setUp(self):
        Path('work').mkdir(exist_ok=True)
        self.c = app.config('config.json')

    def test_dedup_regions_and_missing_statistics(self):
        rows = app.collect(self.c, '', fetch)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['regions'], self.c['regions'])
        self.assertIsNone(rows[1]['view_count'])

    def test_filter_and_private(self):
        self.c['include_keywords'] = ['city WALK']
        self.assertEqual(len(app.collect(self.c, '', fetch)), 1)
        def private(p, k):
            page = fetch(p, k)
            for item in page['items']:
                item['status']['privacyStatus'] = 'private'
            return page
        self.assertEqual(app.collect(self.c, '', private), [])

    def test_pagination_and_failure(self):
        self.c['regions'] = ['US']
        self.c['pages_per_region'] = 2
        calls = []
        def paged(p, k):
            calls.append(p)
            page = fetch(p, k)
            if len(calls) == 1:
                page['nextPageToken'] = 'next'
            return page
        app.collect(self.c, '', paged)
        self.assertEqual(calls[1]['pageToken'], 'next')
        def fail(p, k):
            raise RuntimeError('failure')
        with self.assertRaises(RuntimeError):
            app.collect(self.c, '', fail)

    def test_exports_and_retention(self):
        with tempfile.TemporaryDirectory(dir='work') as d:
            root = Path(d)
            now = datetime(2026, 10, 21, tzinfo=app.CST)
            app.write(root, app.collect(self.c, '', fetch), now, self.c['regions'], True)
            old = root / 'history/2026-09-01.json'
            old.write_text('{}')
            app.cleanup(root, now, 25)
            self.assertFalse(old.exists())
            self.assertTrue((root / 'reports/2026-10-21.csv').read_text(encoding='utf-8-sig').find("'=海岸") >= 0)
            md = (root / 'reports/2026-10-21.md').read_text(encoding='utf-8')
            self.assertIn('&#124;', md)
            self.assertEqual(len(json.loads((root / 'history/2026-10-21.json').read_text(encoding='utf-8'))['videos']), 2)

    def test_retry_transient_and_stop(self):
        from urllib.error import HTTPError
        error = HTTPError('https://example.invalid', 503, 'unavailable', {}, None)
        with patch('collector.urlopen', side_effect=error) as opened, patch('collector.time.sleep'):
            with self.assertRaises(RuntimeError):
                app.request({}, 'SECRET')
            self.assertEqual(opened.call_count, 3)

    def test_http_error_redaction(self):
        from urllib.error import HTTPError
        error = HTTPError('https://example.invalid/SECRET', 403, 'SECRET', {}, None)
        with patch('collector.urlopen', side_effect=error):
            with self.assertRaises(RuntimeError) as cm:
                app.request({}, 'SECRET')
        self.assertNotIn('SECRET', str(cm.exception))

class EdgeCaseTests(unittest.TestCase):
    def test_invalid_configuration(self):
        Path('work').mkdir(exist_ok=True)
        for field, value in [('regions', []), ('regions', ['us']), ('top_n', True), ('history_days', 30), ('include_keywords', [''])]:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory(dir='work') as d:
                c = app.config('config.json')
                c[field] = value
                path = Path(d) / 'config.json'
                path.write_text(json.dumps(c), encoding='utf-8')
                with self.assertRaises(ValueError):
                    app.config(path)

    def test_empty_export_and_same_day_replacement(self):
        Path('work').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir='work') as d:
            root = Path(d)
            now = datetime(2026, 10, 21, tzinfo=app.CST)
            c = app.config('config.json')
            app.write(root, app.collect(c, '', fetch), now, c['regions'], True)
            app.write(root, [], now, c['regions'])
            self.assertEqual(len(list((root / 'history').glob('*.json'))), 1)
            self.assertEqual(json.loads((root / 'history/2026-10-21.json').read_text(encoding='utf-8'))['videos'], [])
            self.assertIn('本次无匹配候选', (root / 'reports/2026-10-21.md').read_text(encoding='utf-8'))

    def test_later_region_failure_aborts_result(self):
        c = app.config('config.json')
        def partial(params, key):
            if params['regionCode'] == c['regions'][1]:
                raise RuntimeError('region failed')
            return fetch(params, key)
        with self.assertRaises(RuntimeError):
            app.collect(c, '', partial)

    def test_retention_boundary_keeps_25_days(self):
        Path('work').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir='work') as d:
            root = Path(d)
            (root / 'history').mkdir()
            for day in ['2026-09-26', '2026-09-27', '2026-10-21']:
                (root / 'history' / (day + '.json')).write_text('{}')
            app.cleanup(root, datetime(2026, 10, 21, tzinfo=app.CST), 25)
            self.assertFalse((root / 'history/2026-09-26.json').exists())
            self.assertTrue((root / 'history/2026-09-27.json').exists())

if __name__ == '__main__':
    Path('work').mkdir(exist_ok=True)
    unittest.main()

