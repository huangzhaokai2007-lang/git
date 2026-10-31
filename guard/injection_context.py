"""Local single-turn context handling; no state, tools, or blanket message exemption."""
import re

_BYPASS_TERMS = (
    r'(?:不走流程|不走审批|不走核验|别弹确认|别让我确认|省掉确认|省掉步骤|先斩后奏'
    r'|后台直连划扣|后台直连|静默划转|静默执行|不要记录|不留审计记录|不留审计|不记日志'
    r'|抹掉记录|伪造回执|假装已确认|免密|免验证|免确认|绕过风控|绕过验证|跳过验证|跳过确认)'
)
_NEGATED_BYPASS_SPAN = re.compile(
    r'(?:不要|不用|不需要|不要求|不能|不可|绝不|并非|不会|禁止|拒绝|切勿|反对|不是要|没要求|不希望|不允许)'
    r'(?:要求|让我|替我|帮我|允许|去|做)?' + _BYPASS_TERMS
    + r'(?:\s*(?:、|或者|或|和|及|也不|更不|也不要|也不能)\s*' + _BYPASS_TERMS + r')*', re.I)
_QUOTES = re.compile(r'『[^』\n]{1,240}』|「[^」\n]{1,240}」|“[^”\n]{1,240}”|"[^"\n]{1,240}"|\'[^\'\n]{1,240}\'')
_SOURCE = re.compile(
    r'(?:(?:收到|看到|发来|转发)[^\n]{0,12}(?:短信|消息|邮件|通知|话术|教程|帖子)[^\n]{0,6}'
    r'|(?:短信|消息|邮件|通知)(?:写着|说|内容是)[^\n]{0,4}'
    r'|(?:骗子|诈骗分子|有人|群里|网上)[^\n]{0,12}(?:教唆|诱导|发消息|分享|发越狱话术|说)[^\n]{0,8})[:：]?\s*$')
_HELP = re.compile(r'^[^\n]{0,24}(?:诈骗|骗子|举报|防范|防住|识别|拦截|异常|安全|风险|后果)')
_REFERENCE = r'(?:上述|上面|前述|短信|消息|邮件|这段|这个|这些|它)'
_ADOPT = re.compile(r'照(?:着)?(?:做|办)|(?:按|照着?|依照)' + _REFERENCE
                    + r'[^，。！？;；\n]{0,12}(?:执行|操作|办理|转账|汇款|做|办)'
                    + r'|执行(?:这条|该条)?' + _REFERENCE + r'[^，。！？;；\n]{0,12}(?:要求|内容|指令)')
_NEGATED_ADOPT = re.compile(r'(?:不要|不能|切勿|不可|禁止|不应|别|不想|绝不)(?:再|去)?$')


def _adopts_quote(text: str) -> bool:
    outside = _QUOTES.sub(' ', text)
    for match in _ADOPT.finditer(outside):
        before = outside[max(0, match.start() - 12):match.start()]
        after = outside[match.end():match.end() + 24]
        if _NEGATED_ADOPT.search(before):
            continue
        if re.search(r'(?:如果|假如|假设)(?:我|我们|直接)?$', before) and re.match(r'会.*(?:风险|后果|怎样|怎么样)', after):
            continue
        return True
    return False


def strip_benign_contexts(text: str) -> str:
    """Remove only attributed help quotes and directly negated bypass phrases.

    Adopting a quote as an instruction keeps it eligible for every existing rule.
    Unquoted trailing instructions always remain in the checked text.
    """
    if not _adopts_quote(text):
        original = text
        def replace(match: re.Match) -> str:
            before = original[max(0, match.start() - 64):match.start()]
            after = original[match.end():match.end() + 64]
            return ' ' if _SOURCE.search(before) and _HELP.search(after) else match[0]
        text = _QUOTES.sub(replace, text)
    return _NEGATED_BYPASS_SPAN.sub(' ', text)
