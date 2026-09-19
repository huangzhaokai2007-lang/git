"""卡 16b 变异自检：4 项修复各配一条变异，每条都必须**真变红**（红 = 有用例失败或 rc≠0）。

用法：`uv run python scripts/mutcheck_16b.py`
每个变异只做**一处文本替换**，跑完立即逐字节还原并复核哈希；判据同时看 FAILED 行数与 rc
（卡 07 的教训：collection error 也有 rc≠0，不能只看 FAILED 行数就当作"没检出"）。

7 个变异（对应 4 项修复）：
  #1 线程安全：连接别退化成"每次新建"；`close()` 别不关连接；事务别退回 deferred BEGIN（升级死锁）
  #2 槽位归一化：别跳过 `normalize_output`（中文 account_type 会直通工具层）
  #3 history 串味：别丢掉 assistant 边界标记（退回实测 0/6 的形状）
  #4 旧库漂移：别对全量 DDL 无条件执行；别短路结构漂移自检
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
DAO_CORE = REPO / "data/_dao_core.py"
DATA_DB = REPO / "data/db.py"
CLASSIFIER = REPO / "agent/classifier.py"
THREAD_TESTS = ["tests/test_dao_threads.py"]
DB_TESTS = ["tests/test_db.py", "tests/test_dao_threads.py"]
CLASSIFIER_TESTS = ["tests/test_classifier.py", "tests/test_classifier_history.py"]

#: (文件, 测试集, [(标签, 原文, 新文), ...]) —— 每次只替换一处
GROUPS: list[tuple[pathlib.Path, list[str], list[tuple[str, str, str]]]] = [
    (DAO_CORE, THREAD_TESTS, [
        ("连接不复用（同线程每次新建）",
         '    if conn is not None and getattr(_local, "path", None) == path:',
         '    if conn is not None and False:'),
        ("close() 不再真的关连接",
         '    conn = getattr(_local, "conn", None)\n    _local.conn, _local.path = None, None\n'
         '    if conn is not None:\n        conn.close()',
         '    conn = getattr(_local, "conn", None)\n    _local.conn, _local.path = None, None\n'
         '    if conn is not None:\n        pass'),
    ]),
    (DATA_DB, DB_TESTS, [
        ("init_db 对全量 DDL 无条件执行（旧库撞 table already exists）",
         'if (name := created_table(statement)) is None or name in missing:',
         'if True:'),
        ("结构漂移自检被短路（缺列不再报）",
         '        if problems := schema_drift(conn):',
         '        if problems := {}:'),
        ("事务退回 deferred BEGIN（先读后写升级死锁）",
         '    conn.execute("BEGIN IMMEDIATE")',
         '    conn.execute("BEGIN")'),
    ]),
    (CLASSIFIER, CLASSIFIER_TESTS, [
        ("history 丢掉 assistant 边界标记（回退成 0/6 的形状）",
         '        turns += [{"role": "user", "content": str(turn)},\n'
         '                  {"role": "assistant", "content": HISTORY_MARKER}]',
         '        turns += [{"role": "user", "content": str(turn)}]'),
        ("槽位归一化被跳过（中文 account_type 直通工具层）",
         '            return normalize_output(result)',
         '            return result'),
    ]),
]


def run_tests(tests: list[str]) -> tuple[int, int]:
    """返回 (FAILED 行数, 退出码)。"""
    proc = subprocess.run([sys.executable, "-m", "pytest", *tests, "--no-header", "-q", "-p", "no:cacheprovider"],
                          cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    stdout = proc.stdout or ""
    return len([line for line in stdout.splitlines() if line.startswith("FAILED ")]), proc.returncode


def _with_file_newlines(text: str, original: bytes) -> str:
    """把锚点里的 `\\n` 换成文件实际的换行（Windows 上是 CRLF，否则多行锚点一个都匹配不上）。"""
    newline = "\r\n" if b"\r\n" in original else "\n"
    return text.replace("\n", newline)


def main() -> int:
    total = sum(len(mutations) for _, _, mutations in GROUPS)
    caught = 0
    for path, tests, mutations in GROUPS:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        group_caught = 0
        failed, rc = run_tests(tests)
        print(f"[{path.name}] 基线：failed={failed} rc={rc}")
        if failed or rc:
            print("基线不是全绿 → 变异自检不成立，先修基线。")
            return 1
        for label, old, new in mutations:
            original = path.read_bytes()
            text = original.decode("utf-8")
            old, new = _with_file_newlines(old, original), _with_file_newlines(new, original)
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
            group_caught += red
            print(f"[{'变红 ✓' if red else '仍绿 ✗ 假绿'}] {label}: failed={failed} rc={rc}")
        print(f"{path.name}：{group_caught}/{len(mutations)} 个变异如期变红")
    print(f"\n{caught}/{total} 个变异如期变红（逐字节还原、哈希复核通过）")
    return 0 if caught == total else 1


if __name__ == "__main__":
    sys.exit(main())
