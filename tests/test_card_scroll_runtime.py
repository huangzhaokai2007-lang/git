"""Exercise gallery scheduling with a small DOM double, without browser dependencies."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_card_animation_does_not_compete_with_manual_scrolling():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node runtime unavailable')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root / 'tests/js/card_scroll_runtime.cjs')],
                            cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
