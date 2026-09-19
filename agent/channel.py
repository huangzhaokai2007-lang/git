"""通道入口（卡 17b）：把**不可信通道文本**包成数据块的唯一薄函数（铁律 7）。

为什么单独一层：`interfaces/` 只允许 import `agent/`（CLAUDE.md 分层铁律 + 卡 16c 的机器守卫），
而包裹实现归 `guard/injection.py`。卡 17 当时在接口层写 `orchestrator.injection.wrap_untrusted`
**借命名空间** —— 能过守卫，但脆弱（编排层哪天不再 import `injection`，那里就静默失效）且语义上
等于接口层直接掏护栏层。本模块把它变成显式的一层：

    interfaces/  →  agent.channel  →  guard.injection

单向、可断言（`tests/test_web_layering.py` 断言 IM 层调的是这个函数）、且**不复制实现**（纯转发）。

调用方：`interfaces/im/channel.py`（IM 正文进编排层前包裹，`source="im"`）。
新通道（网页端要标来源、将来的电话/邮件通道）也走这里，不要各自去掏 `guard/`。
"""

from __future__ import annotations

from guard.injection import wrap_untrusted as _wrap_untrusted

__all__ = ["wrap_untrusted"]


def wrap_untrusted(source: str, text: object) -> str:
    """把不可信通道文本包成 `<untrusted_data source="...">…</untrusted_data>`（铁律 7 的通道入口）。

    纯转发 `guard.injection.wrap_untrusted`：本层只固定"通道文本走这条路"这个契约 ——
    调用方（接口层）依赖一个稳定的名字，`guard/` 的实现与净化细节仍只有一份。
    """
    return _wrap_untrusted(source, text)
