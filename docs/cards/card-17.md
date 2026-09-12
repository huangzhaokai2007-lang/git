<!-- 自动从 docs/02-AI指令剧本.md 拆分生成；不要直接改这里，改源文件后重新拆分 -->

> 用法：`bash scripts/run-card.sh 17`（无人值守）或在 Hermes 里直接说「做卡 17」。
> 开工前 Agent 必须先读 `CLAUDE.md` 和 `docs/01-接口规格.md`。

【任务卡 #17】接入一条 IM 通道
范围：interfaces/im/、.env.example
要求：
1. 实现 IM webhook（优先飞书自定义机器人出消息 + 事件订阅入消息；不行就用 HTTP 轮询 demo 通道）
2. IM 消息进入编排层前必须经 wrap_untrusted 包裹，并标记 source=im
3. 同一编排层复用（不得为 IM 复制一份逻辑）
4. 无网络时降级为本地回环 self-test，保证 verify.sh 不依赖外网
交付：按模板 + 说明如何配置。
