"""卡 14b 变异自检：guard 4 条 + transfer 4 条（14b-6），每条变异必须真变红（红 = 有用例失败或 rc≠0）。

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
TRANSFER = REPO / "tools/transfer.py"
TRANSFER_TOKEN = REPO / "tools/_transfer_token.py"
GUARD_TESTS = ["tests/test_tool_guard.py"]
TRANSFER_TESTS = ["tests/test_tools_transfer.py", "tests/test_tools_transfer_execute.py",
                  "tests/test_tools_transfer_aa.py"]

#: (文件, 测试集, [(标签, 原文, 新文), ...]) —— 每次只替换一处
GROUPS: list[tuple[pathlib.Path, list[str], list[tuple[str, str, str]]]] = [
    (GUARD, GUARD_TESTS, [
        ("幂等改内存（读写都走内存，不进库）",
         'dao.get_idempotent(token)',
         '_MEMO.get(token)'),
        ("限流阈值放宽（5 → 50）",
         'RATE_MAX_WRITES = 5',
         'RATE_MAX_WRITES = 50'),
        ("只读也计数（评分函数顺手记账）",
         '    if cents > AMOUNT_HARD_MAX_CENTS:',
         '    note_write(tool or "read-path", tool=tool)\n    if cents > AMOUNT_HARD_MAX_CENTS:'),
        ("重放也计数（重放分支先计数）",
         '        return json.loads(existing["result_json"]), True',
         '        check_write_rate(user_id, tool=tool)\n'
         '        return json.loads(existing["result_json"]), True'),
    ]),
    (TRANSFER_TOKEN, TRANSFER_TESTS, [
        ("快照读改回内存（重启重放失效）",
         'row = dao.get_idempotent(token)',
         'row = None'),
        ("去 DB 守卫（翻转只写内存不落库）",
         'flipped = dao.update_idempotent(token, new_json, existing["result_json"]) > 0',
         'flipped = True'),
    ]),
    (TRANSFER, TRANSFER_TESTS, [
        ("回滚改先写快照后事务（提交前写快照）",
         '            dao.insert_audit(token["trace_id"], token["session_id"], actor="agent",',
         '            _store_token(preview_token, {**token, "state": "executed", "data": data.model_dump(),\n'
         '                                         "facts": facts, "message": message})\n'
         '            dao.insert_audit(token["trace_id"], token["session_id"], actor="agent",'),
        ("重放走限流（重放分支先计数）",
         '        return _ok(token["data"], token["facts"], token["message"])',
         '        tool_guard.check_write_rate(current_user_id(), tool="execute_transfer")\n'
         '        return _ok(token["data"], token["facts"], token["message"])'),
    ]),
]


def run_tests(tests: list[str]) -> tuple[int, int]:
    """返回 (FAILED 行数, 退出码)。"""
    proc = subprocess.run([sys.executable, "-m", "pytest", *tests, "--no-header", "-q",
                           "-p", "no:cacheprovider"], cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    stdout = proc.stdout or ""
    return len([line for line in stdout.splitlines() if line.startswith("FAILED ")]), proc.returncode


def main() -> int:
    total_mutations = sum(len(mutations) for _, _, mutations in GROUPS)
    caught_total = 0
    for path, tests, mutations in GROUPS:
        base = path.read_bytes()
        digest = hashlib.sha256(base).hexdigest()
        failed, rc = run_tests(tests)
        print(f"[{path.name}] 基线：failed={failed} rc={rc}")
        if failed or rc:
            print("基线不是全绿 → 变异自检不成立，先修基线。")
            return 1
        caught = 0
        for label, old, new in mutations:
            original = path.read_bytes()
            text = original.decode("utf-8")
            if text.count(old) != 1:
                print(f"[锚点缺失 ✗] {label}（命中 {text.count(old)} 次）")
                return 1
            try:
                path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
                failed, rc = run_tests(tests)
            finally:
                path.write_bytes(original)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                print(f"[还原失败 ✗] {label}")
                return 1
            red = failed > 0 or rc != 0
            caught += red
            print(f"[{'变红 ✓' if red else '仍绿 ✗ 假绿'}] {label}: failed={failed} rc={rc}")
        caught_total += caught
        print(f"{path.name}：{caught}/{len(mutations)} 个变异如期变红")
    print(f"\n{caught_total}/{total_mutations} 个变异如期变红（逐字节还原、哈希复核通过）")
    return 0 if caught_total == total_mutations else 1


if __name__ == "__main__":
    sys.exit(main())
