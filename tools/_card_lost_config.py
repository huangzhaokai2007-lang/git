"""Synthetic branch pickup configuration, never external bank promises."""
LOSS_FEE_YUAN = '0.00'
REPLACEMENT_FEE_YUAN = '0.00'
TOTAL_FEE_YUAN = '0.00'
ESTIMATED_DAYS = 15
BRANCHES = (
    {'id': 'demo_central', 'name': '模拟银行中心支行（演示网点）',
     'address': '合成城市演示路 A 区服务大厅', 'phone': '000-****-0001', 'hours': '演示配置：工作日 09:00—17:00'},
    {'id': 'demo_east', 'name': '模拟银行东区支行（演示网点）',
     'address': '合成城市演示路 B 区服务大厅', 'phone': '000-****-0002', 'hours': '演示配置：工作日 09:00—17:00'},
)
IMPACT = ('挂失生效后，原卡将停止使用，无法自助恢复。本次操作仅限制该卡，不冻结关联账户资金，'
          '不影响同账户其他正常卡及独立账户转账。余额与交易记录保留，已有订阅不自动取消。'
          '倒计时内原卡保持原状态，可撤销；生效后即使找回原卡也不能自助解锁。'
          '补卡仅受理模拟申请，待人工复核标记不代表已审核、制卡或寄送；补卡失败不恢复原卡。')
