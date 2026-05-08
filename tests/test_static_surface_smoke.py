import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class StaticSurfaceSmokeTests(unittest.TestCase):
    def test_mini_app_dist_contains_expected_pwa_artifacts(self):
        mini_app_dist = REPO_ROOT / 'mini_app_dist'
        expected_files = (
            'index.html',
            'manifest.webmanifest',
            'share-target-sw.js',
            'sw.js',
            'workbox-8c29f6e4.js',
        )

        for relative_path in expected_files:
            with self.subTest(relative_path=relative_path):
                self.assertTrue((mini_app_dist / relative_path).exists())

        manifest = json.loads((mini_app_dist / 'manifest.webmanifest').read_text(encoding='utf-8'))
        self.assertEqual(manifest['name'], 'Gold')
        self.assertEqual(manifest['share_target']['action'], '/share-receive')
        self.assertGreaterEqual(len(manifest['icons']), 2)

    def test_template_and_font_assets_exist_and_are_non_empty(self):
        template_path = REPO_ROOT / 'templates' / '404.html'
        font_path = REPO_ROOT / 'fonts' / 'Vazir.ttf'

        self.assertTrue(template_path.exists())
        self.assertIn('<!DOCTYPE html>', template_path.read_text(encoding='utf-8'))
        self.assertTrue(font_path.exists())
        self.assertGreater(font_path.stat().st_size, 0)

    def test_map_data_and_pip_package_caches_have_representative_artifacts(self):
        map_data_dir = REPO_ROOT / 'map_data'
        pip_packages_dir = REPO_ROOT / 'pip_packages'

        mbtiles_files = sorted(map_data_dir.glob('*.mbtiles'))
        wheel_files = sorted(pip_packages_dir.glob('*.whl'))

        self.assertGreaterEqual(len(mbtiles_files), 2)
        self.assertTrue((map_data_dir / 'sources').is_dir())
        self.assertTrue((pip_packages_dir / '.requirements_hash').exists())
        self.assertGreaterEqual(len(wheel_files), 20)

    def test_root_src_and_optional_generated_artifact_dirs_are_well_formed(self):
        src_dir = REPO_ROOT / 'src'
        expected_entries = {'README.md', '__init__.py', 'core', 'infrastructure', 'interfaces', 'shared'}
        self.assertTrue(src_dir.is_dir())
        self.assertTrue(expected_entries.issubset({path.name for path in src_dir.iterdir()}))

        for optional_dir in (
            REPO_ROOT / 'frontend' / 'playwright-report',
            REPO_ROOT / 'frontend' / 'test-results',
            REPO_ROOT / 'tmp',
        ):
            if optional_dir.exists():
                self.assertTrue(optional_dir.is_dir(), msg=f'{optional_dir} must remain a directory when present')


if __name__ == '__main__':
    unittest.main()