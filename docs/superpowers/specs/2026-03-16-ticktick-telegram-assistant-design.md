# TickTick Telegram Assistant Design

**Date:** 2026-03-16

**Owner:** Codex + user

**Goal:** 构建一个常驻云端的一对一 Telegram 助理，默认使用中文、以 Telegram 作为主提醒渠道，基于 `gpt-5-mini` 在充分理解上下文后查询、创建、修改和完成 TickTick 任务，并提供晨报、晚报、提前提醒、临时 snooze、长期偏好记忆与歧义确认。

## 1. Scope

### In Scope

- 一对一 Telegram Bot 作为唯一用户入口
- TickTick 任务的查询、创建、修改、完成、移动 list、补充描述、调整时间、设置重复
- 晨间简报：每天 08:00 发送
- 晚间回顾：每天 23:30 发送，并支持直接回消息批量勾完成或改时间
- 所有有明确时间的任务在开始前 5 分钟 Telegram 提醒
- 时间窗口任务与明确时间任务分开建模和提醒
- Telegram 侧 snooze，只改提醒节奏，不改任务本身时间
- 重复检测、冲突检测、歧义确认
- 长期偏好记忆、短期对话摘要、指代消解记忆
- 时区动态切换：支持自然语言切换和 Telegram 位置更新
- 默认中文输出，保留必要英文专有名词

### Out of Scope (Phase 1)

- TickTick Habit 模块
- Telegram 群聊
- Web/Mini App UI
- 直接替代 TickTick 的全部高级原生能力
- 非必要的复杂工作流编排平台

## 2. Product Principles

- 先理解，再执行；不把用户原话直接硬翻译成 API 参数
- Telegram 是主提醒渠道，尽量避免与 TickTick 原生提醒重复
- 默认中文、语气活泼亲密，但不废话；忙碌场景优先抓重点
- 有歧义时先确认，宁可多问一句，不误写 TickTick
- 优先使用 TickTick 官方能力，遇到缺口时再通过兼容层补齐
- 记忆可修正、可降权、可删除，不允许系统固化错误偏好

## 3. External Capability Assumptions

### TickTick

- TickTick 官方开发者入口可用
- TickTick 已发布官方 MCP 接入说明，且官方明确说明当前以基础任务/项目操作为主，其他高级功能尚未完全覆盖
- 因此系统必须保留兼容层与安全降级路径，不能假设所有高级语义都能直接映射

### Telegram

- Telegram Bot API 可稳定支持 webhook、inline keyboard、reply keyboard、location message、callback query
- Telegram 不会主动提供用户当前时区，时区更新必须来自自然语言或位置消息

## 4. High-Level Architecture

系统拆分为 6 个边界清晰的模块：

### 4.1 Telegram Gateway

职责：

- 接收文本消息、按钮点击、位置消息
- 发送晨报、晚报、提前提醒、冲突确认、重复确认、失败通知
- 维护 Telegram 侧 message/callback 上下文

接口：

- `POST /webhook/telegram`
- `sendMessage`
- `editMessageReplyMarkup`
- `sendLocationRequestKeyboard`

### 4.2 Conversation Brain

职责：

- 组装每轮对话所需的最小相关上下文
- 调用 `gpt-5-mini`
- 先产出结构化理解结果，再决定是否执行

输入上下文：

- 当前用户消息
- 最近几轮摘要
- 当前活跃任务上下文
- 候选 TickTick 任务
- 长期偏好记忆
- 当前时区和当前时间
- 最近确认结果

输出结构：

- `intent_type`: query/create/update/complete/reminder_control/brief_reply/clarification
- `target_candidates`
- `confidence`
- `requires_confirmation`
- `structured_actions`
- `memory_updates`

### 4.3 TickTick Adapter

职责：

- 负责与 TickTick 官方能力交互
- 把内部语义对象映射到 TickTick 任务字段
- 在官方能力不足时触发兼容层与安全降级

核心字段覆盖要求：

- title
- description / notes
- list / folder
- tags
- priority
- due date / due time
- start / end / duration
- repeat rule
- completion status
- subtasks

能力策略：

- Level 1: 官方能力
- Level 2: 兼容层映射
- Level 3: 不稳定时只做确认或降级为备忘/待确认项

重复规则要求：

- 必须支持常见自然语言重复表达，例如每天、每周、每月、工作日、每周几
- 若用户说“每周自动循环”“每周五提醒我交周报”，系统默认将其解析为 TickTick repeat，而不是普通一次性任务
- 当重复规则存在多种合理解释时，必须先确认，不允许自动猜测

### 4.4 Reminder & Briefing Engine

职责：

- 生成晨报、晚报
- 调度 5 分钟前提醒
- 调度时间窗口任务的轻提醒
- 调度 snooze 事件
- 避免重复发送同一事件

### 4.5 Memory & Preference Store

职责：

- 保存长期偏好、别名映射、时间表达习惯、指代解析结果、对话摘要
- 为每轮对话检索最相关记忆

### 4.6 Safety & Decision Layer

职责：

- 歧义确认
- 重复检测
- 冲突检测
- 失败重试与状态回滚
- 高风险操作的幂等保护

## 5. Semantic Model

系统内部将用户事项分为 5 类，不把所有输入都当作同一种 TickTick 任务处理。

### 5.1 Explicit-Time Task

示例：

- 明天 3 点开会
- 周五 18:30 跟朋友吃饭

字段：

- title
- description
- list/folder/tags
- due_at
- start_at/end_at 或 duration
- repeat_rule
- priority
- subtasks
- timezone_mode: floating/fixed

行为：

- 进入晨报“今天安排”
- 开始前 5 分钟提醒
- 进入晚间回顾候选
- 参与冲突检测

### 5.2 Windowed Task

示例：

- 下周做完
- 这两周处理掉
- 月底前整理完

字段：

- title
- description
- window_start
- window_end
- hard_deadline: true/false
- prompt_style
- related_ticktick_task_id

TickTick 落地：

- 创建为普通任务
- 放入专门 list
- 添加 `windowed` 类标签
- 描述中保留原始自然语言时间表达

行为：

- 晨报单独一栏展示
- 按窗口开始、过半、截止前一天进行轻提醒

### 5.3 Memo Task

示例：

- 先记着
- 以后可以研究一下

行为：

- 不进入当天硬安排
- 不触发 5 分钟提醒
- 进入备忘池
- 参与周期性清理

### 5.4 Modification Instruction

示例：

- 改到明天下午
- 再补一句说明
- 放到 fun 那个 list

行为：

- 先做目标任务召回
- 置信度不足时先确认
- 不默认新建任务

### 5.5 Reminder Control Instruction

示例：

- 1 小时后再提醒我
- 今晚 8 点再提醒
- 先别催我

行为：

- 仅调整 Telegram 侧 reminder event
- 不改 TickTick 任务时间

## 6. Persistence Model

建议使用 Postgres 作为主库。第一版至少包含以下实体。

### 6.1 `users`

- telegram_user_id
- display_name
- default_language
- tone_style
- primary_timezone
- current_timezone
- timezone_source
- created_at
- updated_at

### 6.2 `memory_facts`

- user_id
- memory_type
- key
- value_json
- confidence
- source_type
- last_confirmed_at
- expires_at

类型包括：

- preference
- alias_mapping
- time_expression
- disambiguation_pattern
- style_preference

### 6.3 `conversation_summaries`

- user_id
- summary_text
- relevance_window_start
- relevance_window_end
- created_at

### 6.4 `active_contexts`

- user_id
- context_type
- payload_json
- expires_at

用于存储当前“那个任务”“前两个任务”等短期指代上下文。

### 6.5 `task_shadows`

- user_id
- ticktick_task_id
- semantic_type
- normalized_title
- list_name
- tags_json
- due_at
- start_at
- end_at
- window_start
- window_end
- raw_nl_time
- timezone_mode
- last_synced_at

说明：该表不是替代 TickTick，而是保存系统自己的语义视图和索引，服务于理解、冲突检测、重复检测、提醒调度。

### 6.6 `reminder_events`

- user_id
- ticktick_task_id
- event_type
- scheduled_at
- status
- payload_json
- dedupe_key
- snoozed_from_event_id
- sent_at

事件类型包括：

- morning_brief
- prestart_reminder
- evening_review
- window_start_ping
- window_mid_ping
- window_deadline_ping
- memo_cleanup
- snoozed_reminder

### 6.7 `action_logs`

- user_id
- source_message_id
- original_text
- parsed_plan_json
- execution_result_json
- status
- created_at

用于幂等、排障和审计。

## 7. Conversation Pipeline

### 7.1 Incoming Message Flow

1. Telegram Gateway 接收入站消息
2. 标准化消息内容与 metadata
3. 检索相关长期记忆、短期上下文、候选 TickTick 任务
4. Conversation Brain 调用 `gpt-5-mini` 输出结构化理解结果
5. Safety Layer 判断是否需要确认
6. 若无需确认，则执行 TickTick/提醒操作
7. 写入 action log、记忆更新、task shadow 更新
8. 生成简洁中文回复并返回 Telegram

### 7.2 Batch Input Handling

单条消息可以包含多行、多类动作：

- 新建
- 完成
- 改时间
- 安排时间
- 备忘

系统先拆分 action，再逐条解析、排序执行。只有不稳定的条目会单独确认，不阻塞整批其他稳定条目。

## 8. Memory Strategy

### 8.1 Long-Term Memory

长期保存：

- 默认中文、英文专有名词保留
- 活泼亲密但不啰嗦
- 忙时先抓重点
- Telegram 为主提醒渠道
- 常用 list/tag/folder 别名
- 常见时间表达习惯
- 已确认的歧义偏好

### 8.2 Short-Term Memory

短期保存：

- 当前正在讨论的任务
- 最近一次列表候选
- 晚间回顾中列出的任务集合
- 最近一轮确认问题的候选项

### 8.3 Memory Write Policy

只有以下情况写入高置信度长期记忆：

- 用户明确表达偏好
- 用户确认过歧义选项
- 某映射反复稳定出现
- 某规则已经成功执行且未被用户纠正

推断性记忆必须带低/中置信度并允许后续覆盖。

## 9. Briefing and Reminder Rules

### 9.1 Morning Brief at 08:00

固定结构：

1. 顶部先给出绝对日期锚点（例如 `2026-03-29 周日`）
2. 今天有明确时间的任务
3. 今天要留意的日期任务
4. 这段时间可以找空推进的事
5. 未完成的事情提醒（需要跟进的截止项）
6. 接下来 7 天的明确安排和截止提醒，日期带周几
7. 未来 7 天里适合找空完成的事
8. 顺手记着的小备忘（暂未安排具体时间）

压缩策略：

- 当天任务很多时，各栏最多展开 3-5 件
- 其余任务改用短列表概览
- 任务描述存在时，默认保留摘要，避免只报标题
- 不要把未来事项塞进“今天”分栏
- 同一任务不要在多个晨报分栏中重复展开

### 9.2 Prestart Reminder at T-5 Minutes

适用对象：

- 所有有明确开始时间的任务

默认内容：

- 还有 5 分钟
- 任务标题
- 必要描述
- 快捷按钮：完成了 / 稍后提醒 / 改时间

### 9.3 Evening Review at 23:30

固定动作：

- 汇总今天的明确时间任务和关键待办
- 询问哪些已完成
- 允许自然语言或按钮回复
- 自动勾完成
- 未完成任务可继续追问是否改时间

### 9.4 Windowed Task Reminder Rhythm

默认节奏：

- 窗口开始日提醒一次
- 窗口过半提醒一次
- 窗口结束前一天提醒一次

短窗口自动降频，避免骚扰。

### 9.5 Memo Cleanup

默认每周进行一次备忘清理：

- 询问这些备忘是否要安排时间
- 支持安排时间 / 保持备忘 / 删除

## 10. Time Zone Strategy

### 10.1 User-Level Time Zone

- 初始保存当前主时区
- 支持通过自然语言切换
- 支持通过 Telegram 位置更新切换

### 10.2 Task-Level Time Zone

按 TickTick 语义区分：

- floating time：用户到达新时区后仍按当地同一时刻提醒
- fixed time：保持原时区意义，展示和提醒时换算到当前时区

### 10.3 Effective Time Zone

所有晨报、晚报、提醒都使用当前生效时区计算。

## 11. Safety Rules

### 11.1 Ambiguity Confirmation

以下场景必须先确认：

- 一句话可能对应多条任务
- 目标任务召回置信度不足
- 时间解析存在多种合理解释
- 重复任务判断不稳定
- 批量操作里个别条目不清楚

确认文案要求：

- 短
- 中文
- 可直接点按钮或自然语言回复

### 11.2 Duplicate Detection

写入前比较：

- 标题相似度
- 相近 list
- 相近时间
- 描述关键词

命中后询问：

- 覆盖
- 合并
- 新建

### 11.3 Conflict Detection

适用对象：

- 明确时间任务的新建和改时间

判断因素：

- 时间重叠
- 同时间段已有高优先级任务
- duration 导致的连续冲突

命中后询问：

- 改时间
- 保留
- 覆盖原安排

## 12. Failure Handling and Reliability

### 12.1 Idempotency

- 基于 Telegram update id、source_message_id、dedupe_key 防重复执行
- reminder event 必须可重复消费但只发送一次

### 12.2 Retry Policy

- Telegram 发送失败：短期重试
- TickTick 写入失败：短期重试；重试失败时通知用户
- 晨报生成失败：退化为简版文本，不允许静默丢失

### 12.3 Honest Degradation

当语义超出当前稳定能力范围时：

- 不伪装成功
- 明确告诉用户当前可安全落地的替代方式
- 必要时先转成待确认项

## 13. Implementation Boundaries

### 13.1 Phase 1 Deliverables

- Telegram webhook service
- TickTick adapter with official-first strategy
- Postgres persistence
- `gpt-5-mini` conversation planner
- Morning brief / evening review / T-5 reminders
- Windowed task handling
- Memo cleanup
- Snooze without changing task time
- Duplicate/conflict detection
- Structured memory system

### 13.2 Not Deferred Within Phase 1

以下能力第一版必须支持，不后置：

- 自然语言修改已有任务
- 描述补充与时间变更
- 批量多行混合输入
- 歧义确认
- 周几展示
- 时区切换

## 14. Testing Strategy

至少覆盖以下测试：

### 14.1 Unit Tests

- 中文时间解析与窗口分类
- 重复检测
- 冲突检测
- reminder schedule 生成
- snooze 逻辑
- 时区切换与 floating/fixed 计算

### 14.2 Integration Tests

- Telegram webhook 入站处理
- TickTick adapter 读写
- Postgres 持久化与事务
- 晨报/晚报任务调度

### 14.3 Conversation Regression Tests

使用真实中文样例回归：

- 改到明天下午
- 再补一句说明
- 放到 fun 那个 list
- 下周做完
- 这两周处理
- 1 小时后再提醒我
- 前两个做完了，第三个改到周四下午

### 14.4 Operational Checks

- 定时任务健康检查
- webhook 健康检查
- TickTick/Telegram 依赖状态检查

## 15. Deployment Model

部署方式：

- 云服务器常驻进程
- HTTPS webhook
- Postgres 数据库
- 独立 worker 执行 reminder schedule 和重试任务

推荐逻辑角色：

- API service：处理 Telegram webhook 和同步请求
- Worker service：处理 reminder queue、brief generation、重试
- Scheduler：生成晨报、晚报及未来 reminder event

## 16. Required Credentials and User Inputs

实现前需要用户提供：

- Telegram Bot Token
- Telegram chat id 或完成 bot 对话绑定
- TickTick 开发者接入凭据或可用授权方式
- OpenAI API key，模型固定为 `gpt-5-mini`
- 生产环境部署位置

建议用户在 TickTick 中配合准备：

- 一个专门给时间窗口任务的 list
- 一个备忘/收集类 list
- 根据需要关闭大部分与 Telegram 重复的 TickTick 原生提醒

## 17. Success Criteria

系统满足以下标准视为设计成功：

- 用户每天按当前生效时区稳定收到 08:00 晨报与 23:30 晚报
- 所有明确时间任务均能在开始前 5 分钟通过 Telegram 提醒
- 用户可用自然语言查询、新建、修改、完成 TickTick 任务
- 用户可通过上下文连续对话修改已有任务，不需要每次重复完整任务名
- 对粗略时间、无时间备忘、明确时间任务能分开建模和提醒
- 当解析不稳或存在冲突/重复时，系统会先确认而非误写
- 系统默认中文输出，必要英文专有名词保留原文

## 18. Source Notes

本设计基于以下已确认信息：

- TickTick 官方开发者站点可访问
- TickTick 帮助中心已发布 “Use TickTick MCP in AI Clients”，并明确说明当前以基础任务和项目操作为主
- TickTick 帮助中心已确认任务重复、提醒、多提醒、duration、time zone、notes 等原生概念存在
- Telegram Bot API 支持 webhook、按钮和位置消息
