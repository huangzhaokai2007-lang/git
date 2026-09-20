"""卡 20-C 守卫：**卷只挂数据目录，代码目录绝不被挂卷盖住**（真机事故的机器防线）。

事故：`- bankdata:/app/data` 把**代码目录**整个盖住 → 容器里跑的是第一次建卷时的旧 `dao.py`，
网页端「加个收款人」一提交就 500 `AttributeError: module 'data.dao' has no attribute 'insert_payee'`；
`--build` 重建镜像也救不了（**卷盖在镜像之上**）。本文件一旦有人再挂错目录立刻变红。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]

#: 代码目录（相对仓库根）：任何卷都不许挂到这些目录下
CODE_DIRS = ("agent", "app", "data", "guard", "interfaces", "scripts", "tools")
#: 数据目录：库文件只许住这里
DATA_DIR = "var"


def _services() -> dict:
    text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text)["services"]


def _mount_targets(service: dict) -> list[str]:
    """服务里每个卷的**容器内挂载点**（命名卷 `bankdata:/app/var` / 绑定挂载 `./x:/y`）。"""
    targets: list[str] = []
    for mount in service.get("volumes") or []:
        parts = str(mount).split(":")
        if len(parts) >= 2 and parts[1].startswith("/"):
            targets.append(parts[1])
    return targets


def test_no_volume_shadows_a_code_directory() -> None:
    for name, service in _services().items():
        for target in _mount_targets(service):
            top = target.removeprefix("/app/").strip("/").split("/")[0]
            assert top not in CODE_DIRS, f"服务 {name} 把卷挂到了代码目录 {target}（卡 20-C 事故：构造器会盖住代码）"


def test_volume_mounts_the_data_directory() -> None:
    for name, service in _services().items():
        targets = [t for t in _mount_targets(service) if t.startswith("/app/")]
        assert targets == [f"/app/{DATA_DIR}"], f"服务 {name} 的卷应只挂 /app/{DATA_DIR}，实际 {targets}"


def test_db_path_points_into_the_data_directory() -> None:
    for name, service in _services().items():
        db_path = (service.get("environment") or {})["DB_PATH"]
        assert db_path.startswith(f"{DATA_DIR}/"), f"服务 {name} 的 DB_PATH 必须在 {DATA_DIR}/ 里，实际 {db_path!r}"
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(rf"^\s*DB_PATH={DATA_DIR}/", dockerfile, re.M), "Dockerfile 默认 DB_PATH 也要落在数据目录"


def test_code_directory_holds_no_database_file() -> None:
    """库文件不许混进代码目录（否则挂卷没得选、只能连代码一起盖住）。"""
    assert [p.name for p in (REPO / "data").glob("*.db*")] == []
