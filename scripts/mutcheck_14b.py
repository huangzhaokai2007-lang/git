"""卡 14b 变异自检：4 条变异必须全部真变红（红 = 有用例失败，或整轮 rc≠0）。

用法：`uv run python scripts/mutcheck_14b.py`
每个变异只做**一处文本替换**，跑完立即逐字节还原并复核哈希；判据同时看 FAILED 行数与 rc
（卡 07 的教训：collection error 也有 rc≠0，不能只看 FAILED 行数就当作"没检出"）。
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
GUARD = REPO / "guard/tool_guard.py"
TESTS = ["tests/test_tool_guard.py"]

#: (标签, 文件, 原文, 新文) —— 每次只替换一处
MUTATIONS: list[tuple[str, pathlib.Path, str, str]] = [
    ("幂等改内存（读写都走内存，不进库）", GUARD,
     'dao.get_idempotent(token)',
     '_MEMO.get(token)'),
    ("限流阈值放宽（5 → 50）", GUARD,
     'RATE_MAX_WRITES = 5',
     'RATE_MAX_WRITES = 50'),
    ("只读也计数（评分函数顺手记账）", GUARD,
     '    if cents > AMOUNT_HARD_MAX_CENTS:',
     '    note_write(tool or "read-path", tool=tool)\n    if cents > AMOUNT_HARD_MAX_CENTS:'),
    ("重放也计数（重放分支先计数）", GUARD,
     '        return json.loads(existing["result_json"]), True',
     '        check_write_rate(user_id, tool=tool)\n'
     '        return json.loads(existing["result_json"]), True'),
]


def run_tests() -> tuple[int, int]:
    """返回 (FAILED 行数, 退出码)。"""
    proc = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "--no-header", "-q",
                           "-p", "no:cacheprovider"], cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    stdout = proc.stdout or ""
    return len([line for line in stdout.splitlines() if line.startswith("FAILED ")]), proc.returncode


def main() -> int:
    base = GUARD.read_bytes()
    digest = hashlib.sha256(base).hexdigest()
    failed, rc = run_tests()
    print(f"基线：failed={failed} rc={rc}")
    if failed or rc:
        print("基线不是全绿 → 变异自检不成立，先修基线。")
        return 1
    caught = 0
    try:
        for label, path, old, new in MUTATIONS:
            original = path.read_bytes()
            text = original.decode("utf-8")
            if text.count(old) != 1:
                print(f"[锚点缺失 ✗] {label}（命中 {text.count(old)} 次）")
                continue
            try:
                path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
                failed, rc = run_tests()
            finally:
                path.write_bytes(original)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                print(f"[还原失败 ✗] {label}")
                return 1
            red = failed > 0 or rc != 0
            caught += red
            print(f"[{'变红 ✓' if red else '仍绿 ✗ 假绿'}] {label}: failed={failed} rc={rc}")
    finally:
        if hashlib.sha256(GUARD.read_bytes()).hexdigest() != digest:
            print("收尾哈希复核失败：文件未回到基线！")
            return 1
    print(f"\n{caught}/{len(MUTATIONS)} 个变异如期变红（逐字节还原、哈希复核通过）")
    return 0 if caught == len(MUTATIONS) else 1


if __name__ == "__main__":
    sys.exit(main())
