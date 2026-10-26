"""话术排雷进 CI：`scripts/audit_phrases.py` 离线模式必须全绿。

**为什么要这条**：本项目最近的 5 个真 bug（空回执、渲染器崩溃、口径分裂、同名误报…）
都是「换一种说法就坏」，而单元测全绿。这个脚本沿整条链路跑 29 种说法，
这条测试负责让它**每次 `pytest` 都跑一遍**，防止哪天又静默退化。

用**子进程**跑：脚本会自己造临时库并改 `DB_PATH`，隔离掉才不影响同进程里的其他用例。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "audit_phrases.py"


def test_audit_phrases_offline_is_green() -> None:
    assert SCRIPT.exists(), "排雷脚本不见了"
    done = subprocess.run([sys.executable, str(SCRIPT)], cwd=REPO,
                          capture_output=True, text=True, timeout=600)
    output = (done.stdout or "") + (done.stderr or "")
    assert done.returncode == 0, f"话术排雷发现问题：\n{output[-3000:]}"
    assert "全部通过" in output, output[-2000:]
