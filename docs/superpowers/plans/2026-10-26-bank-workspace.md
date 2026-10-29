# Bank workspace implementation plan

> Execute in this session with subagent-driven-development. The user approved the linked design on 2026-10-26.

**Goal:** One current business view, concise history, contextual bill exploration and consistent feedback for bills, cards and transfers.

**Architecture:** Keep existing Streamlit and agent/tool boundaries. Add presentation state in a workspace module; keep validated query context in agent modules. Reuse existing financial confirmation and OTP components.

**Tech stack:** Existing Python, Streamlit, vanilla JS and pytest. No new dependencies.

## Task 1: Workspace lifecycle and history (primary implementation)

Files: interfaces/web/app.py, components.py, shortcut_ui.py; new interfaces/web/workspace_ui.py; tests/test_web_single_chat.py and new tests/test_workspace_ui.py.

- [ ] Add failing AppTest regressions: card→transfer hides card detail/forms, same shortcut does not append messages or recreate confirmation, reset closes all bound flags, failed query preserves previous successful result.
- [ ] Keep messages intact as evidence/history; render user prompts and short assistant conclusions in transcript. Render interactive results only in the current workspace.
- [ ] Maintain a current scene identity, last successful turn and failed request. Same scene reuses the current view. Explicit scene switch calls existing shortcut cancellation once and clears old UI flags.
- [ ] Move current charts and insight actions out of historical messages; reuse spending pie and card controls. Keep history plain; full result accessible in current view as needed.
- [ ] Keep loading within existing workspace and add read-only retry. Never automatically retry writes.
- [ ] Validate: `.venv/Scripts/python.exe -m pytest tests/test_web_single_chat.py tests/test_workspace_ui.py tests/test_card_gallery_ui.py tests/test_sidebar_balance_ui.py -q`.

## Task 2: Validated bill selection and followups (agent implementation)

Files: agent/scene_flow.py, scene_language.py, scene_phrases.py, scene_context.py; tools/_scene_reads.py and related scene read helpers as required; tests/test_scene_integration.py and new focused tests.

- [ ] Add failing full chain test: August bill→current chart selects ecommerce→largest transaction→associated card→card details. Add month/card changes and no-result checks.
- [ ] Add `select_bill_category(session, source, category)` in agent bridge: validate owner/current context and source month/category; perform audited read, update context only on success.
- [ ] Add largest-transaction and transaction-associated-card understanding in deterministic planner, with computations/ownership verification in tool layer. Use existing `scene_read` request contract; do not change frozen public tools.
- [ ] Distinguish account-linked card from proven payment card; never infer exact payment instrument from account alone.
- [ ] Clear incompatible transaction selection on explicit scope changes; preserve owner/session isolation.
- [ ] Validate focused tests plus existing scene tests. No reads or writes to demo DB or .env.

## Task 3: Chart feedback and visual consistency

Files: interfaces/web/spending_pie.py/.js/.css, scene_ui.py, theme.css; existing JS runtime tests plus focused regressions.

- [ ] Current pie callback updates validated context; historical chart inspection stays read-only. Preserve chart widget identity on ordinary reruns.
- [ ] Failed category detail provides retry in place, not close-and-reselect instructions. No animation on ordinary rerender.
- [ ] Current context header shows month/category/card and allows changing month/category/card through agent.
- [ ] Keep one main chart, compact scrolling ledger and at most three related actions. Use common font, amount, spacing, colors and reduced-motion behavior.
- [ ] Validate JS runtimes, AppTest and browser at narrow/wide layouts.

## Task 4: Review and delivery

- [ ] Review spec compliance, then code quality with reviewer agents; resolve material issues.
- [ ] Run complete pytest and scripts/verify.sh with UTF-8 output, UV offline/no-sync, and an independent D:/CodexChecks test database. Preserve demo.db.
- [ ] Browser verify three paths with queries and previews only, then save screenshots.
- [ ] `git diff --check`, commit and push feature branch; create and attach a new PR because #64 was merged. Report exact evidence and any limitations.
