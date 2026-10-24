# Graphite UI Implementation Plan

> **For agentic workers:** Use subagent-driven-development or executing-plans. The user approved the reference-derived dark design and one-column sidebar.

**Goal:** 将已批准的深色参考风格应用到现有银行聊天与业务界面。

**Architecture:** 集中 theme.css + theme.py，Streamlit theme 原生配置覆盖图表及弹层；既有展示组件引用视觉变量，不修改 agent/tools/data 业务实现。

**Tech Stack:** 现有 Streamlit/CSS、Material 内置图标和 pytest。无下载或新依赖。

1. 迁移快捷场景的呈现标签测试，验证无 emoji 的单列入口仍调用相同指令、图标存在、表单与敏感信息保护不变；先观察预期失败。
2. 创建 interfaces/web/theme.css、theme.py 和 .streamlit/config.toml；用本机字体及深色 token 覆盖按钮、表单、提示、聊天、表格与导航。
3. components.py 引用主题；快捷入口使用 Material 图标和显式激活标记，助手/用户头像统一线性图标；app.py 加入紧凑品牌与会话标题。
4. card_gallery_ui.py、payee_card_ui.py 改为主题变量与低饱和表面；保留卡号、芯片、真实状态标记及动效代码。
5. 运行相关 UI 与分层回归，检查不改变业务数字或验证码/PIN 门槛；独立审查。
6. 本地浏览器检查首页、卡片、挂失说明、表单和窄屏；全量验收通过后在当前分支提交，保留输出截图和日志，不自动推送。

## 验收记录

- 相关 UI 回归：68 条通过；新回归先观察预期失败，再通过。
- scripts/verify.sh：1518 条测试、53/53 评测、100 条红线检查、30 条红队、IM 7/7、API 4/4 全部通过。
- git diff --check 通过；独立代码审查无阻塞问题。
- 桌面浏览器已检查首页、卡面动画、挂失双选项、须知和补卡资料；业务面板实测背景 rgb(21,22,24)、圆角16px，无横向溢出。
- 正文实测15px、本机 Segoe UI / Microsoft YaHei 字体。主按钮白字对比度4.83:1，正文12.21:1，次要文字7.34:1。
- 已提供760px以下的间距适配；本次未进行真实移动设备验收。
- 本地分支保存，不自动推送。
