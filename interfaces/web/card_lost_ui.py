"""F09 内联办理，所有业务数字和执行结果均取自 agent。"""
from concurrent.futures import ThreadPoolExecutor

import streamlit as st
from agent import card_binding_flow, card_lost_flow as flow

NOTICE = '''**挂失补卡办理须知**

1. 本功能受理本人已绑定借记卡的正式挂失及补卡申请。冻结卡、信用卡暂不支持线上办理。
2. **挂失生效后，原卡停止使用，找到原卡也不能通过临时解锁恢复。** 原卡不能再办理调额；以该卡为付款渠道的新交易应被拒绝。
3. **仅限制该卡，不冻结关联账户资金。** 账户余额及历史流水保留，其他正常卡和独立账户转账继续按原规则办理；已有订阅不会自动取消。
4. 提交并验证成功后进入可撤销窗口；生效前可在申请记录中撤销，生效后不可恢复原卡。返回、下一步及关闭未提交表单均不执行挂失。
5. 本次受理补卡申请，待模拟审核，不代表已制卡、已寄送或已完成补卡。补卡后续处理失败不恢复已挂失原卡。
6. 领卡人须为申请人本人；按选定模拟网点提示核验身份。本系统全部使用合成资料，不收集真实证件、卡号或手机号。'''


def reset() -> None:
    for key in list(st.session_state):
        if str(key).startswith('lost-'):
            del st.session_state[key]


def _state(executor: ThreadPoolExecutor, session: str) -> dict:
    scope = (executor.submit(card_binding_flow.owner_key).result(), session)
    if st.session_state.get('lost-scope') != scope:
        reset()
        st.session_state['lost-scope'] = scope
    return st.session_state.setdefault('lost-view', {'open': False, 'step': 'select', 'preview': None,
                                                    'branch': None, 'error': None})


def accept_turn(executor: ThreadPoolExecutor, session: str, ask: str | None) -> None:
    if ask not in ('card_lost', 'card_lost_choice', 'card_lost_records'):
        return
    reset()
    state = _state(executor, session)
    state.update(open=True, step={'card_lost': 'select', 'card_lost_choice': 'choice',
                                 'card_lost_records': 'records'}[ask])


def _choice(executor: ThreadPoolExecutor, session: str, state: dict) -> None:
    st.write('想先临时锁卡继续找，还是确认挂失并补卡？')
    if st.button('临时锁卡，继续寻找', type='primary', key='lost-choice-lock'):
        import card_lock_ui
        reset()
        card_lock_ui.accept_turn(executor, session, 'card_lock')
        st.rerun()
    if st.button('确认挂失并补卡', key='lost-choice-permanent'):
        state['step'] = 'select'
        st.rerun()


def _select(executor: ThreadPoolExecutor, session: str, state: dict) -> None:
    result = executor.submit(flow.cards).result()
    if not result['ok']:
        st.error(result['message'])
        return
    st.write('选择需要挂失并补卡的银行卡')
    if not result['data']['items']:
        st.info('暂无已绑定卡片，请先绑定预设模拟借记卡。')
    for item in result['data']['items']:
        allowed = item['type'] == 'savings' and item['status'] in ('normal', 'locked')
        st.caption(f"{item['card_no_mask']} · {card_binding_flow.TYPE_LABELS.get(item['type'], item['type'])} · {card_binding_flow.STATUS_LABELS[item['status']]}")
        if not allowed:
            st.caption('已挂失卡不能重复办理；信用卡及冻结卡暂不支持此线上流程。')
        if st.button(f"办理 {item['card_no_mask']}", disabled=not allowed, key=f"lost-card-{item['card_id']}", width='stretch'):
            preview = executor.submit(flow.preview, item['card_id'], session).result()
            if preview['ok']:
                state.update(preview=preview['data'], step='notice', error=None)
            else:
                state['error'] = preview['message']
            st.rerun()


def _notice(state: dict) -> None:
    preview = state['preview']
    st.write(f"卡号：{preview['card_no_mask']}")
    st.warning('原卡挂失生效后不可自助恢复；本次不冻结关联账户资金。')
    st.markdown(NOTICE)
    st.caption(f"验证后可撤销窗口：{preview['delay_seconds']} 秒；窗口内卡片保持原状态，如需先保护请使用临时锁卡。")
    understood = st.checkbox('我已阅读并理解办理须知', key='lost-notice-read')
    if st.button('下一步：补卡信息', disabled=not understood, type='primary'):
        state['step'] = 'details'
        st.rerun()


def _summary(preview: dict) -> None:
    for label, key in (('卡号', 'card_no_mask'), ('客户名称', 'customer_name_mask'),
                       ('原卡产品名称', 'old_product'), ('新卡产品名称', 'new_product')):
        st.text(f'{label}：{preview[key]}')
    a, b, c = st.columns(3)
    a.metric('挂失费用（元）', preview['loss_fee_yuan'])
    b.metric('补卡工本费（元）', preview['replacement_fee_yuan'])
    c.metric('合计（元）', preview['total_fee_yuan'])
    st.caption(f"预计办理时间：{preview['estimated_days']} 天（模拟配置估计，不承诺实际制卡或到货时间）；费用为演示配置，无真实扣费。")


def _details(state: dict) -> None:
    preview = state['preview']
    _summary(preview)
    st.divider()
    st.text('领卡方式：模拟网点领取')
    branches = {item['id']: item for item in preview['branches']}
    default = state['branch'] or preview['default_branch_id']
    selected = st.selectbox('领卡网点', list(branches), index=list(branches).index(default),
                            format_func=lambda value: branches[value]['name'], key='lost-branch-select')
    state['branch'] = selected
    branch = branches[selected]
    for label, key in (('网点地址', 'address'), ('网点电话', 'phone'), ('营业时间', 'hours')):
        st.text(f'{label}：{branch[key]}')
    st.caption('默认选择模拟开户网点；可重新选择其他模拟网点，所有网点资料仅供演示。')
    st.text(f"领卡人预留手机号：{preview['phone_mask']}")
    st.info('本人携带有效身份证明领取；本演示不采集证件或真实联系方式，不发送真实短信。新卡尚未生成，卡号及激活结果以后续处理为准。')
    if st.button('下一步：验证确认', type='primary'):
        state['step'] = 'verify'
        st.rerun()


def _verify(executor: ThreadPoolExecutor, session: str, state: dict) -> None:
    preview = state['preview']
    _summary(preview)
    branch = next(item for item in preview['branches'] if item['id'] == state['branch'])
    st.write(f"领取网点：{branch['name']}")
    st.warning('请核对原卡及领取信息。生效后原卡永久停止使用，不可通过临时解锁恢复；账户资金不冻结。')
    with st.form('lost-verify-form', clear_on_submit=True):
        pin = st.text_input('模拟银行卡密码', type='password', max_chars=6)
        otp = st.text_input('模拟短信验证码', type='password', max_chars=6)
        consent = st.checkbox('确认原卡失效及补卡信息，提交本次申请')
        submitted = st.form_submit_button('确认挂失并提交补卡申请', type='primary')
    if submitted:
        if not consent:
            state['error'] = '请先确认原卡失效及补卡信息。'
        else:
            result = executor.submit(flow.submit, preview['token'], pin, otp, state['branch'], session).result()
            if result['ok']:
                state.update(step='records', error=None)
            else:
                state['error'] = result['message']
        st.rerun()


@st.fragment(run_every='2s')
def records(executor: ThreadPoolExecutor, session: str, *, prefix: str = 'panel') -> None:
    result = executor.submit(flow.records, session).result()
    if not result['ok']:
        st.error(result['message'])
        return
    st.caption('申请持久保存，重置聊天不会删除。服务运行时检查到期申请；停止运行期间不承诺准时生效。')
    if not result['data']['items']:
        st.info('暂无挂失补卡申请。')
    for item in result['data']['items']:
        with st.container(border=True):
            st.write(f"申请编号：{item['application_id']}")
            st.write(f"原卡：{item['card_no_mask']}")
            status = item['status']
            if status == 'pending':
                st.info(f"挂失待生效 · 截止时间：{item.get('execute_at_text', item['execute_at'])}")
                st.caption('窗口内原卡尚未挂失，撤销只取消本次申请，不解除原先的锁卡状态。')
                if st.button('撤销本次申请', key=f"lost-{prefix}-revoke-{item['application_id']}"):
                    cancelled = executor.submit(flow.cancel, item['application_id'], session).result()
                    if not cancelled['ok']:
                        st.error(cancelled['message'])
                    else:
                        st.rerun()
            elif status == 'success':
                st.success('原卡已挂失；补卡申请已受理，待模拟审核。')
                st.caption('复核标记已登记，不代表人工审核通过；尚未制卡、寄送或生成新卡。')
            elif status == 'cancelled':
                st.info('本次申请已撤销，未执行挂失。')
            else:
                st.error('本次申请未能完成，请核对当前卡片状态后重新办理。')
            st.write(f"补卡进度：{'待模拟审核' if status == 'success' else '待挂失生效' if status == 'pending' else '未受理'}")
            if item.get('branch'):
                st.write(f"领取网点：{item['branch']['name']}")
                st.caption(f"网点地址：{item['branch']['address']} · 电话：{item['branch']['phone']}")
            if item.get('submitted_at_text'):
                st.caption(f"提交时间：{item['submitted_at_text']} · 预留手机号：{item['phone_mask']}")
            if item.get('total_fee_yuan'):
                st.caption(f"费用合计：{item['total_fee_yuan']} 元（模拟配置，无真实扣费）")
    if st.button('刷新办理进度', key=f'lost-{prefix}-refresh'):
        st.rerun()


def render(executor: ThreadPoolExecutor, session: str, *, show_button: bool = True) -> None:
    state = _state(executor, session)
    if show_button and st.button('挂失并补卡', key='lost-open', width='stretch'):
        # 2026-10-29（Comment 12/13）：与「调整额度 / 临时锁卡」同一宽度规格。
        import card_lock_ui
        card_lock_ui.reset()
        accept_turn(executor, session, 'card_lost')
        st.rerun()
    if not state['open']:
        return
    with st.container(border=False, width=720, key='lost-panel'):
        st.markdown('### 挂失补卡')
        steps = {'select': '选择银行卡', 'choice': '选择办理方式', 'notice': '办理须知',
                 'details': '补卡信息', 'verify': '验证确认', 'records': '办理结果与申请记录'}
        st.write(f"**{steps[state['step']]}**")
        if state['error']:
            st.error(state['error'])
        step = state['step']
        if step == 'choice':
            _choice(executor, session, state)
        elif step == 'select':
            _select(executor, session, state)
        elif step == 'notice':
            _notice(state)
        elif step == 'details':
            _details(state)
        elif step == 'verify':
            _verify(executor, session, state)
        else:
            records(executor, session)
        if step in ('notice', 'details', 'verify') and st.button('上一步', key='lost-back'):
            state.update(step={'notice': 'select', 'details': 'notice', 'verify': 'details'}[step], error=None)
            st.rerun()
        if st.button('关闭办理面板', key='lost-close'):
            reset()
            st.rerun()
