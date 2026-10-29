"""理财产品档案：**规范大类 + 细分类型** + 结构化条款（**工具层单一来源**）。

为什么单开一个文件：产品的"门面信息"（属于哪一类、挂钩什么、敲出敲入怎么定）既是展示文案
也是业务口径。DDL 的 `wealth_product` 只有六列（id / name / risk_level / term_days /
expected_yield / min_amount），规格冻结不扩表，于是档案落在工具层 —— 推荐（T14，
`_wealth_risk`）与全部产品目录（T27，`wealth_holdings`）共用同一份，界面只负责排版
（铁律 1：口径只有工具层一份，界面不做算术）。

**分类为什么是两级、键为什么照监管口径**（2026-10-29，用户要求"把银行的产品分类搞专业"）：
- **大类 `category`**：照《资管新规》按投资性质的四分类（固定收益类 / 权益类 / 商品及金融
  衍生品类 / 混合类）+ 行业货架惯例补齐 现金管理类、存款类、结构性存款、公募 REITs（另类）、
  跨境类（QDII）、券商收益凭证。推荐结果必须**按大类分组**展示，不许一锅烩。
- **细分 `kind`**：大类里面再分（货币基金、二级债、可转债基金、CTA……），就是用户看的
  "它到底是什么"。

**雪球与鲨鱼鳍到底是什么（查证后修正，2026-10-29）**：
- **鲨鱼鳍不是基金** —— 它是**结构性存款**的一种：存款保本 + 小部分利息买期权博浮动收益，
  收益曲线像鲨鱼鳍（区间内涨多少按参与率给多少、涨过障碍只给上限）。保本，R2。
- **雪球也不是基金** —— 全称「**雪球型自动敲入敲出式券商收益凭证**」，券商发行的场外衍生品，
  **非保本**：每月观察，涨到敲出价提前兑付票息；跌破敲入价且到期没涨回，按指数跌幅**承担亏损**。
  此"雪球"与雪球公司无关（中证协提示过名称涉嫌侵权）。
- 气囊式是雪球的变体：不设敲入，改用"气囊"（跌 30% 以内不亏，跌破后按参与率放大承担）。

**结构化产品的数字为什么是字符串**：敲出价 103%、敲入价 75%、票息 12.80%/年 这些是
**条款原文**（合同里就写成百分数），不是算出来的；以文本形态进 facts 就不会违反
「facts 里不出现浮点」，界面也就无从"算错"。

**必须标"模拟"**：演示数据集没有净值表，结构化产品的条款是照真实结构**仿写**的示意条款，
不代表任何一只在售产品的合同。界面在条款下方统一标注。
"""

from __future__ import annotations

from typing import Mapping

#: 产品**大类**（键 → 中文标签），顺序即展示顺序（稳健 → 进取）。
#: 口径：《资管新规》按投资性质分类 + 银行货架惯例（现金管理 / 存款 / 结构性存款 /
#: REITs / QDII / 券商收益凭证）。标签对外展示，键进 facts（界面翻标签，不自己映射）。
CATEGORY_ORDER: tuple[str, ...] = (
    "cash", "deposit", "structured_deposit", "fixed_income",
    "hybrid", "equity", "reits", "commodity", "overseas", "note",
)
CATEGORY_LABELS: dict[str, str] = {
    "cash": "现金管理类",
    "deposit": "存款类",
    "structured_deposit": "结构性存款",
    "fixed_income": "固定收益类",
    "hybrid": "混合类",
    "equity": "权益类",
    "reits": "另类投资（公募 REITs）",
    "commodity": "商品及金融衍生品类",
    "overseas": "跨境类（QDII）",
    "note": "券商收益凭证（雪球结构 · 非保本）",
}

#: 产品**细分类型**（键 → 中文标签）。大类回答"按监管算哪类"，细分回答"它到底是什么"。
KIND_LABELS = {
    "money": "货币基金",
    "cash": "现金管理",
    "deposit": "存款",
    "bond": "债券基金",
    "bond_plus": "固收+（二级债基）",
    "hybrid": "混合型基金",
    "equity": "股票型",
    "quant": "量化选股",
    "convertible": "可转债基金",
    "commodity": "商品型",
    "reits": "公募 REITs",
    "overseas": "QDII",
    "private": "私募资管",
    "cta": "CTA 管理期货",
    "sharkfin": "鲨鱼鳍结构性存款",
    "snowball": "雪球收益凭证",
}

#: 每个产品的大类与细分（**按 product_id 对齐 `data/seed.PRODUCT_ROWS`**）。
#: 新产品进 seed 时这里必须同步登记，否则 `attachment()` 返回空标签（测试会抓）。
_SHELF: dict[str, tuple[str, str]] = {
    # --- 现金管理类 ---
    "prod_r1_mmf": ("cash", "money"),
    "prod_r1_cash": ("cash", "cash"),
    # --- 存款类 ---
    "prod_r1_cd": ("deposit", "deposit"),
    # --- 固定收益类（≥80% 债权资产） ---
    "prod_r2_bond30": ("fixed_income", "bond"),
    "prod_r2_bond90": ("fixed_income", "bond"),
    "prod_r2_bond180": ("fixed_income", "bond"),
    "prod_r2_cd_index": ("fixed_income", "bond"),
    "prod_r3_secondary": ("fixed_income", "bond_plus"),
    # --- 结构性存款（保本） ---
    "prod_r2_sharkfin": ("structured_deposit", "sharkfin"),
    # --- 混合类 ---
    "prod_r3_balance": ("hybrid", "hybrid"),
    "prod_r3_mixed365": ("hybrid", "hybrid"),
    "prod_r4_convertible": ("hybrid", "convertible"),
    # --- 权益类（≥80% 权益资产） ---
    "prod_r3_div": ("equity", "equity"),
    "prod_r4_quant720": ("equity", "quant"),
    "prod_r4_growth540": ("equity", "equity"),
    "prod_r5_theme": ("equity", "equity"),
    "prod_r5_growth": ("equity", "equity"),
    "prod_r5_pe": ("equity", "private"),
    # --- 另类（公募 REITs） ---
    "prod_r3_reits": ("reits", "reits"),
    # --- 商品及金融衍生品类 ---
    "prod_r3_gold": ("commodity", "commodity"),
    "prod_r5_cta": ("commodity", "cta"),
    # --- 跨境类（QDII） ---
    "prod_r4_asia": ("overseas", "overseas"),
    "prod_r4_qdii": ("overseas", "overseas"),
    # --- 券商收益凭证（雪球结构，非保本） ---
    "prod_r4_snowball": ("note", "snowball"),
    "prod_r5_airbag": ("note", "snowball"),
}

#: 结构化产品的条款（**只有雪球/鲨鱼鳍这类带条款的才登记**）。
#: 字段是卡片上分条展示用的，缺项一律填空串（界面据此不画那一行）。
_STRUCTURED: dict[str, dict[str, str]] = {
    "prod_r2_sharkfin": {
        "structure": "结构性存款（保本型）· 看涨鲨鱼鳍 · 挂钩沪深 300 指数 · 365 天 · "
                     "行权价 100% · 障碍价 106% · 参与率 25% · 收益上限 6.00%/年",
        "underlying": "沪深 300 指数",
        "observe": "到期一次性观察（仅到期日结算）",
        "knock_out": "障碍价 106%：期末涨幅越过上限，只按收益上限 6.00%/年 计息",
        "knock_in": "无敲入（保本型鲨鱼鳍不设下跌触发）",
        "coupon": "期末涨幅在 0～6% 区间内，按参与率 25% 分取指数涨幅，最高 6.00%/年",
        "protect": "存款部分保本：到期指数下跌，本金 100% 兑付（可能只有最低利息）",
        "structure_note": ("鲨鱼鳍的本质：大部分资金存存款保本，小部分利息买看涨期权博浮动收益，"
                           "收益曲线像鲨鱼鳍 —— 区间内涨得越多给得越多，涨过障碍价只给上限。"
                           "**它是结构性存款，不是基金**。"),
    },
    "prod_r4_snowball": {
        "structure": "雪球型自动敲入敲出式券商收益凭证（**非保本**）· 挂钩中证 500 指数 · "
                     "730 天 · 月度观察 · 敲出价 103%（每月递减 0.5%）· 敲入价 75% · 票息 12.80%/年",
        "underlying": "中证 500 指数",
        "observe": "月度观察（每月同一天看一次）",
        "knock_out": "敲出价 103%，每月递减 0.5%：观察日涨过敲出价，产品提前终止、兑付票息",
        "knock_in": "敲入价 75%（每日观察，触及即生效）",
        "coupon": "票息 12.80%/年，按实际持有月份折算",
        "protect": "**非保本**：敲入后到期未涨回敲入价，按指数跌幅承担亏损（可能远超票息）",
        "structure_note": ("雪球全称「雪球型自动敲入敲出式券商收益凭证」，是券商发行的场外衍生品，"
                           "**不是基金、不保本**：没跌破敲入价才能稳拿票息；跌破 75% 且到期没涨回，"
                           "指数跌多少亏多少。此『雪球』与雪球公司无关（中证协已提示名称侵权风险）。"),
    },
    "prod_r5_airbag": {
        "structure": "气囊式雪球收益凭证（**非保本**）· 挂钩中证 1000 指数 · 550 天 · "
                     "气囊保护 30% · 敲出价 105% · 参与率 120% · 票息 16.50%/年",
        "underlying": "中证 1000 指数",
        "observe": "月度观察（每月同一天看一次）",
        "knock_out": "敲出价 105%（不递减）：观察日涨过敲出价，提前终止、兑付票息",
        "knock_in": "无敲入，改以气囊保护 30%",
        "coupon": "票息 16.50%/年，按实际持有月份折算",
        "protect": "**非保本**：跌幅在 30% 气囊内不亏，跌破后超出部分按参与率 1.2 倍放大承担",
        "structure_note": ("气囊雪球是雪球的变体：不设敲入价，改用『气囊』挡下跌 —— 跌得不多"
                           "（30% 以内）气囊顶着不亏；跌破气囊后，超出部分按 1.2 倍放大承担。"
                           "它同样**不是基金、不保本**。"),
    },
}

#: `attachment()` 永远返回这些键 —— 界面不必判断键在不在（缺项就是空串）。
_FIELDS = ("kind", "kind_label", "category", "category_label", "structure",
           "structure_note", "underlying", "observe", "knock_out", "knock_in",
           "coupon", "protect")


def attachment(product_id: str) -> dict[str, str]:
    """取一只产品的门面信息（**永远返回全部键**，没登记的项为空串）。

    没登记的产品返回空标签 —— 宁可少显示，也不让界面自己造一个类型出来
    （老 facts / 测试数据里可能没有这只产品）。`category_label` 按 `CATEGORY_LABELS`
    翻译大类键；没登记的产品大类为空串。
    """
    category, kind = _SHELF.get(product_id, ("", ""))
    info = {field: "" for field in _FIELDS}
    info["kind"] = kind
    info["kind_label"] = KIND_LABELS.get(kind, "")
    info["category"] = category
    info["category_label"] = CATEGORY_LABELS.get(category, "")
    info.update(_STRUCTURED.get(product_id, {}))
    return info


def is_structured(product_id: str) -> bool:
    """这只产品带结构条款吗（卡片据此展开条款区）。"""
    return product_id in _STRUCTURED


def structured_count() -> int:
    """结构化产品数量（供 facts 展示"共 N 只结构化产品"）。"""
    return len(_STRUCTURED)


def merge(item: Mapping[str, object]) -> dict[str, object]:
    """把档案并进一只产品的展示字典（返回**新** dict，不改调用方的）。"""
    return {**item, **attachment(str(item.get("product_id") or item.get("id") or ""))}
