import subprocess
import sys
import unittest


class CliCommandsTest(unittest.TestCase):
    def _run_module(self, module_name, *args):
        return subprocess.run(
            [sys.executable, '-m', module_name, *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_nts_feed_entrypoint_supports_help(self):
        result = self._run_module('nts_feed.app', '--help')
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('run the nts feed web application', result.stdout.lower())

    def test_build_database_cli_supports_help(self):
        result = self._run_module('nts_feed.cli.build_database', '--help')
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('build', result.stdout.lower())

    def test_backfill_tracklists_cli_supports_help(self):
        result = self._run_module('nts_feed.cli.backfill_tracklists', '--help')
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('tracklist', result.stdout.lower())

    def test_backfill_auto_downloads_cli_supports_help(self):
        result = self._run_module('nts_feed.cli.backfill_auto_downloads', '--help')
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('auto-download', result.stdout.lower())

    def test_recover_shows_cli_supports_help(self):
        result = self._run_module('nts_feed.cli.recover_shows', '--help')
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn('recover', result.stdout.lower())


if __name__ == '__main__':
    unittest.main()
