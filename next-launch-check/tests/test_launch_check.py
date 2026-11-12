"""Runs the skill's Node test suite from the catalog's unittest-based CI.

The launch check is a Node script; its tests use `node --test`. CI only
discovers Python unittest modules, so this wrapper runs the Node suite and
fails when it fails. Everything is offline: the tests start a local server
on 127.0.0.1 and answer any other request locally.
"""

import os
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class NodeSuite(unittest.TestCase):
    def test_node_suite(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        files = sorted(str(p) for p in (HERE / "js").glob("*.test.js"))
        self.assertTrue(files, "no Node test files found")
        result = subprocess.run(
            [node, "--test", *files],
            cwd=HERE,
            capture_output=True,
            text=True,
            timeout=900,
            env={**os.environ, "NEXT_LAUNCH_CHECK_OFFLINE": "1"},
        )
        if result.returncode != 0:
            self.fail(result.stdout[-6000:] + result.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
