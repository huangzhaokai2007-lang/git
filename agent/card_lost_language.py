"""挂失入口只分流，不从自然语言直接执行写操作。"""
import re

from guard import injection


def recognize(text: str) -> str | None:
    clean = re.sub(r'\s+', '', text)
    if injection.detect(clean).blocked or re.search(r'不要|不想|不用|无需|别|没丢|没有丢|没有不见|取消|撤销', clean):
        return None
    if re.search(r'挂失|补卡', clean) and re.search(r'记录|进度|申请状态', clean):
        return 'records'
    if re.search(r'查询|查看|有哪些|有没有|只看|几张', clean):
        return None
    if re.search(r'补[办一张]*卡|补一张|换一张', clean) and re.search(r'丢|失|偷|盗|挂失', clean):
        return 'lost'
    if '挂失' in clean:
        return 'choice'
    return None
