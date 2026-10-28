"""界面快捷场景（按钮文案 → 送进编排层的原话）。

**为什么单独一个文件**：`interfaces/web/components.py` 在 F15/F16/F20 之后已到 303 行
（CLAUDE.md 红线 300）；再加场景条目只会更超。场景是**纯文案配置**，与渲染无关，拆出来最干净。

口径（卡 16 第 1 条）：界面只负责「把这句话交给 agent/」，**不读库、不解析、不拼回执**。
"""

from __future__ import annotations

SCENES: tuple[tuple[str, str], ...] = (
    ("看账单", "出一份上个月的账单报告"),
    ("转账", "给王五转 100 元"),
    ("收款人", "我的收款人有哪些"),
    ("管订阅", "我有哪些订阅"),
    ("管卡", "卡片查询"),
    ("调整额度", "调整额度"),
    ("我的理财", "我买了哪些理财"),
    ("推荐理财", "推荐理财产品"),
    ("锁卡", "锁卡"),
    ("挂失补卡", "帮我挂失"),
    ("操作记录", "查看操作记录"),
)

SCENE_ICONS = tuple(f":material/{name}:" for name in (
    "receipt_long", "swap_horiz", "group", "autorenew", "credit_card",
    "tune", "monitoring", "grid_view", "lock", "credit_card_off", "history",
))
