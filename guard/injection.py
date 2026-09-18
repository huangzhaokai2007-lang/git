"""护栏层：提示注入检测（铁律 7 的第一道防线）。

两层，各自独立、可单测：

1. **规则层**（`detect`）：关键词/正则表，**确定性代码**，不依赖 LLM 判断是不是注入。命中 → `unsafe_request`。
   为了不靠"字符串一模一样"匹配，匹配前先做**归一化**：解 unicode/十六进制转义、解百分号编码、NFKC
   统一全角、去掉零宽字符与软连字符；再对「归一化串」和「紧凑串」（去掉一切空白与标点）各匹配一遍 ——
   于是「忽 略 之 前 的 指 令」「忽略|之前|的|指令」「忽\u200b略」这类分段/混淆都能命中。
2. **数据层**（`wrap_untrusted`）：把不可信文本按 `<untrusted_data source="...">…</untrusted_data>` 包裹 ——
   memo / IM 正文 / 收款人备注等自由文本进模型上下文**必须**过这一层（铁律 7）。包裹时把正文里的
   `<` `>` 全角化，攻击者无法用 `</untrusted_data>` 提前闭合标签、把自己伪装成指令。

⚠ 本模块只**判定**与**包裹**，不决定"要不要拒答"：调用方（编排层）拿到 `Verdict.blocked` 后走 REFUSE。
   把规则层接进 `classifier`/`orchestrator` 属于范围外的文件，见交付说明「待拍板」。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, NamedTuple

from pydantic import BaseModel, ConfigDict

#: 命中规则时的意图名（与规格 §3 的意图清单一致）
UNSAFE_INTENT = "unsafe_request"

#: 自由文本字段名（memo / IM 正文 / 收款人备注…）：进模型上下文前必须 `wrap_untrusted`。
#: `tests/test_injection.py` 的 lint 用例按这份清单扫描 `agent/` 的 LLM 调用点。
FREE_TEXT_FIELDS = ("memo", "note", "remark", "counterparty", "message", "body", "content", "text")

#: 规则表：id → (中文说明, 正则)。正则写成"词元相邻"的形式（归一化与紧凑串都能命中）。
RULES: dict[str, tuple[str, str]] = {
    "inj-ignore-prev": (
        "要求忽略既有指令",
        r"(忽略|无视|忘记|不要理会)[^\n]{0,6}(之前|先前|上面|以上|上述|前面)[^\n]{0,6}"
        r"(的)?[^\n]{0,4}(指令|命令|要求|规则|提示)"
        r"|(ignore|disregard|forget)[^\n]{0,20}(previous|prior|above|earlier|all)[^\n]{0,20}"
        r"(instruction|prompt|rule|command)s?"),
    "inj-role-change": (
        "要求改变角色/身份",
        r"(你现在是|从现在起你是|你不再是|假装你是|扮演)[^\n]{0,10}(管理员|管理员权限|客服|银行|系统|root|admin)"
        r"|you are now[^\n]{0,20}(admin|administrator|root|developer|bank|system)"
        r"|act as[^\n]{0,20}(admin|administrator|root)"),
    "inj-dev-mode": (
        "要求进入开发者/无限制模式",
        r"(开发者模式|调试模式|无限制模式|越狱模式|解除(所有)?限制)"
        r"|(developer|debug|unrestricted|god|dan)\s*mode|jailbreak"),
    "inj-export-data": (
        "要求导出他人/全部数据",
        r"(导出|导出全部|列出|下载|打印)[^\n]{0,8}(全部|所有|其他|他人|所有用户)[^\n]{0,8}(用户|客户|账号|账户|数据|信息)"
        r"|(export|dump|list|print)[^\n]{0,20}(all|every)[^\n]{0,10}(user|customer|account|data)"),
    "inj-prompt-leak": (
        "索要系统提示词/内部规则",
        r"(告诉|给我|输出|显示|重复|泄露)[^\n]{0,8}(你的)?[^\n]{0,4}(系统)?(提示词|提示语|指令|prompt|规则表)"
        r"|(reveal|show|print|repeat|leak)[^\n]{0,20}(your|the)[^\n]{0,10}(system\s*)?(prompt|instructions|rules)"),
    "inj-transfer-all": (
        "要求把余额/资金整体转给本人或他人账户",
        r"(把|将)[^\n]{0,16}(余额|存款|资金|钱)[^\n]{0,12}(转|汇|划)[^\n]{0,6}(给|到|入)[^\n]{0,6}"
        r"(我|自己|他人|别人|对方|其他账户)"
        r"|(把|将)[^\n]{0,10}(他人的?|别人的?|对方的)[^\n]{0,6}(余额|存款|资金)[^\n]{0,12}(转|汇|划)"
        r"|transfer[^\n]{0,20}(entire|whole|all)[^\n]{0,20}balance"),
    "inj-bypass-verify": (
        "要求绕过验证/风控",
        r"(绕过|跳过|免去|无需|不用)[^\n]{0,8}(验证|认证|确认|校验|风控|密码|验证码|otp|人脸)"
        r"|(bypass|skip|without|no need for)[^\n]{0,20}(verification|auth|confirmation|security|otp|password|2fa)"),
    "inj-no-auth": (
        "宣称免密/无需确认",
        r"(免密|免验证|免确认|免 otp)[^\n]{0,6}(支付|转账|操作|执行)?"
        r"|(no|without)[^\n]{0,12}(password|otp|auth)[^\n]{0,12}(needed|required)"),
    "inj-new-rules": (
        "试图写入新规则/解除安全限制",
        r"(新的|以下是新的?)[^\n]{0,4}(指令|规则|设定|要求)[^\n]{0,4}(如下|：|:)"
        r"|(关闭|解除|禁用|ignore)[^\n]{0,8}(安全|防护|限制|safety|guardrail)"
        r"|disable[^\n]{0,12}(safety|guardrail|filter)"),
    "inj-impersonate": (
        "伪装成系统/上级/官方",
        r"(系统提示|系统消息|管理员说|银行通知|官方要求|开发者说明)[^\n]{0,4}(：|:)[^\n]{0,10}"
        r"(请|你)?[^\n]{0,6}(转账|付款|提供|告知|执行)"
        r"|(system message|admin says|official notice)[^\n]{0,4}:[^\n]{0,20}(transfer|send|give|execute)"),
}

#: 归一化：unicode 转义 / 十六进制转义
_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})")

#: 归一化：百分号编码（**必须按 UTF-8 字节解码**，逐字节 chr() 会得到 latin-1 乱码）
_PERCENT = re.compile(r"(?:%[0-9a-fA-F]{2})+")


def _decode_percent(match: re.Match) -> str:
    raw = bytes(int(part, 16) for part in re.findall(r"%([0-9a-fA-F]{2})", match.group(0)))
    return raw.decode("utf-8", "replace")

#: 零宽字符与软连字符（用于把"忽\u200b略"还原成"忽略"）
_INVISIBLE = re.compile(r"[\u200b-\u200f\u2060\ufeff\u00ad]")


class Verdict(BaseModel):
    """一次注入检测的结论（护栏层契约）。"""

    model_config = ConfigDict(extra="forbid")

    blocked: bool
    rule_ids: list[str] = []
    reason: str = ""
    intent: str = ""                      # 命中时为 unsafe_request；未命中为空串（交给 LLM 分类器）


def normalize(text: str) -> str:
    """归一化不可信文本：解转义 → 解百分号 → NFKC（全角/兼容字符）→ 去零宽。"""
    decoded = _PERCENT.sub(_decode_percent, _ESCAPE.sub(
        lambda m: chr(int(m.group(1) or m.group(2), 16)), text))
    return _INVISIBLE.sub("", unicodedata.normalize("NFKC", decoded))


def compact(text: str) -> str:
    """紧凑串：去掉一切空白与标点（分段绕过「忽 略 之 前」在这层现形）。"""
    return re.sub(r"[\s\W_]+", "", text)


def _match(text: str) -> list[str]:
    """在归一化串与紧凑串上各跑一遍规则表，返回命中的规则 id（保持规则表顺序、去重）。"""
    forms = (normalize(text), compact(normalize(text)))
    hits = [rule_id for rule_id, (_note, pattern) in RULES.items()
            if any(re.search(pattern, form, re.IGNORECASE) for form in forms)]
    return hits


def detect(text: object, *, extra_texts: Iterable[object] = ()) -> Verdict:
    """检测是否命中注入规则。命中 → `blocked=True` + `intent=unsafe_request` + 原因说明。

    `extra_texts` 用于一并检查同一请求里的其他自由文本（如 IM 正文、备注）——
    任一处命中即整体拦下（攻击者可能把指令藏在"备注"里）。
    """
    pieces = [str(text)] + [str(item) for item in extra_texts]
    hits: list[str] = []
    for piece in pieces:
        for rule_id in _match(piece):
            if rule_id not in hits:
                hits.append(rule_id)
    if not hits:
        return Verdict(blocked=False, reason="未命中注入规则")
    notes = "；".join(RULES[rule_id][0] for rule_id in hits)
    return Verdict(blocked=True, rule_ids=hits, intent=UNSAFE_INTENT,
                   reason=f"检测到疑似注入/越权指令（{notes}）")


# ---------------- 数据层：不可信文本包裹（铁律 7） ----------------

UNTRUSTED_TAG = "untrusted_data"

#: 包裹标签的 source 只允许字母数字点划线与下划线（防伪造属性/标签）
_SOURCE_SAFE = re.compile(r"[^0-9A-Za-z._\-]")


def wrap_untrusted(source: str, text: object) -> str:
    """把不可信文本包成数据块：`<untrusted_data source="...">…</untrusted_data>`。

    - `source` 只保留字母数字点划线下划线（其余替换为 `_`，最长 40 字符），防止伪造标签属性；
    - 正文里的 `<` `>` 一律全角化：正文无法提前闭合标签、也无法伪造新标签；
    - `None` → 空块（不写 "None"，避免把 Python 字面量喂给模型）。
    """
    label = _SOURCE_SAFE.sub("_", str(source))[:40] or "unknown"
    payload = "" if text is None else str(text)
    payload = payload.replace("<", "＜").replace(">", "＞")
    return f'<{UNTRUSTED_TAG} source="{label}">{payload}</{UNTRUSTED_TAG}>'
