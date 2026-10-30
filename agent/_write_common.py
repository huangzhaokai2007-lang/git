"""写路径的**共享底座**：核心契约类型 + 与意图无关的小工具。

**为什么单独一个模块**（F15/F20 拆卡）：`agent/write_intents.py` 曾到 **296 行**，距 `CLAUDE.md`
的「单文件 ≤300 行」红线只剩 4 行，装不下 F15（群收款）与 F20（送礼计划）两个新描述符。
按 card-04b / card-05b 按域拆文件的先例，把写描述符**按意图族**拆开：

| 模块 | 内容 |
|---|---|
| **`_write_common.py`（本模块）** | `ShapeResult` / `Prepared` / `WriteSpec` + `_code_of` / `yuan_to_cents` |
| `_write_transfer.py` | 转账族 T6/T7（`preview_token` 族） |
| `_write_subscription.py` | 订阅取消族 T11（`confirm_ref` 族） |
| `write_intents.py` | **只剩注册表 `_SPECS` + 对外 API**（`spec_of` / `WRITE_INTENTS` / `otp_ok`） |

**本模块是最底层**：不 import 任何写意图族模块，故依赖是单向的（无环）。

三个类型都是**编排层内部契约，非冻结工具契约**（规格 §2 冻结的是 18 个工具函数）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Mapping

from agent import confirm_card
from tools.schemas import ToolResult

#: 金额文本形状：最多两位小数的十进制（禁科学计数/负号/多余位数）
_YUAN = re.compile(r"\d+(?:\.\d{1,2})?")

#: 脱敏手机号（`139****1001`）；追问文案与卡面用的就是这个形态
_MASKED_PHONE = re.compile(r"\d{3}\*{4}\d{4}")


def normalize_query(raw: object) -> str:
    """把「模型回显的卡面/列表串」还原成可用于**子串匹配**的查询键。

    **实机复现**（用户报「回 1001 识别不出收款人」）：追问时界面列出的是
    `李四（139****1001）`，模型会把候选**原样回显**成这个串；而各 `_shape_*` 是拿它去
    `resolve_payee` / `in merchant` 做**子串匹配**的 —— 整串一条都匹配不上。

    这里优先抽出**脱敏手机号**（唯一且精确）；抽不到就原样返回。

    ⚠️ **刻意没做的事**：不剥离一般的 `（…）`。商户名、产品名里可能合法地含括号
    （如「腾讯视频（会员）」），剥了会误伤 —— 只在**证明过会出错**的场合（手机号）才做转换。
    其余场合靠 `resolve_*` 的子串匹配 + 追问兜底。
    """
    text = str(raw or "").strip()
    found = _MASKED_PHONE.search(text)
    return found.group(0) if found else text


def _code_of(result: ToolResult) -> str | None:
    """工具结果的错误码字符串（枚举与裸字符串都接受）。"""
    code = result.error_code
    return str(getattr(code, "value", code)) if code else None


@dataclass(frozen=True)
class ShapeResult:
    """SLOT_FILL 的结果：补齐的槽位 / 缺槽（追问）/ 直接报错（零命中）。"""

    filled: dict
    missing: list[str] = field(default_factory=list)
    error_code: str | None = None
    message: str = ""
    clarify: str = ""                 # 缺槽时的具体追问（F17：收款人候选）；空 = 用通用模板追问
    #: 收款人**同名多命中**时的候选（`[{id,name,phone}]`）—— 界面据此出「解析卡」让人挑
    #: （设计 §3 关卡 1：「命中多个 → 出解析卡」）。空 = 无需选择。
    candidates: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class Prepared:
    """PRECHECK 的输入：工具层事实包 + **工具层签发**的凭证（编排层不得自己造串）。"""

    ok: bool
    facts: Mapping = field(default_factory=dict)
    credential: str = ""
    error_code: str | None = None
    message: str = ""


@dataclass(frozen=True)
class WriteSpec:
    """一个写意图的完整形状（写路径四步里「因意图而异」的那部分）。"""

    intent: str
    intent_cn: str
    title: str                        # 确认卡标题（不含书名号）
    credential_kind: str              # "preview_token"（工具层预览签发）| "confirm_ref"（工具层私有约定签发）
    otp_in_tool: bool                 # True=工具层校验 OTP；False=编排层校验
    missing: tuple[str, ...]          # 缺槽时回给用户的槽位名（CLARIFY 措辞用）
    tools: tuple[str, ...]            # PRECHECK 阶段调用的工具（审计 tool_calls）
    execute_tool: str                 # EXECUTE 阶段的工具名
    target_key: str                   # 工具层主键在槽位里的键名（`payee_id` / `sub_id`）
    what: str                         # 中文宾语（「这笔{what}」）
    shape: Callable[[Mapping], ShapeResult]
    prepare: Callable[[dict], Prepared]
    card: Callable[[str, dict, Mapping, str, bool], confirm_card.CardInput]
    plain: Callable[[dict, Mapping, str], str]
    execute: Callable[["confirm_card.Confirmation", str | None], ToolResult]
    # ↑ 入参是**整张确认卡**（而非旧的 `target_id`）：单主键装不下 T9 的收款人列表与
    #   T16 的 `(contact, date, budget)`；槽位快照在 `Confirmation.filled` 里（F15/F20 的改造）。


def yuan_to_cents(value: object) -> int | None:
    """元 → 整数分：**字符串拆分整数运算，禁 float**（analyst 裁决；避免 0.1 类精度问题）。

    认不出或非正数 → None（由调用方判为缺槽并追问）。
    """
    text = str(value).strip().replace(",", "").replace("元", "")
    if not _YUAN.fullmatch(text):
        return None
    whole, _, frac = text.partition(".")
    cents = int(whole) * 100 + int((frac + "00")[:2])
    return cents if cents > 0 else None                              # 非正数 → 视为缺槽（不猜 0 元）


#: 循环说法 → 周期码（确定性映射，不猜：认不出就按一次性处理）
_REPEAT_WORDS = {"每月": "monthly", "每个月": "monthly", "月月": "monthly", "月度": "monthly",
                 "每月末": "monthly", "每个月月末": "monthly", "月末": "monthly",
                 "每周": "weekly", "每星期": "weekly", "每礼拜": "weekly", "每周一次": "weekly",
                 #: 2026-10-30（用户要求「每日/每天」）：每天类说法 → daily（+1 天续期）
                 "每天": "daily", "每日": "daily", "天天": "daily", "日均": "daily",
                 "每天一次": "daily"}


def repeat_cycle(raw: object) -> str | None:
    """把「每个月 / 每周 …」归一成周期码；认不出 → None（= 一次性，不打扰用户）。"""
    text = str(raw or "").strip()
    if not text:
        return None
    if text in ("daily", "weekly", "monthly"):       # 2026-10-30：daily 也直接认
        return text
    for word, cycle in _REPEAT_WORDS.items():
        if word in text:
            return cycle
    return None


#: 脱敏手机号（`139****1001`）；追问文案与卡面用的就是这个形态
