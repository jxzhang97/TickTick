# TickTick Telegram Assistant Local Hosting Design

**Date:** 2026-03-27

**Owner:** Codex + user

**Goal:** 将原先计划中的云端常驻 webhook 架构，调整为运行在用户长期在线的 Mac Studio 上的本地常驻架构：使用 Telegram polling、Mac 本地 Postgres、`launchd` 自启动，以及仅在 TickTick 首次 OAuth 授权时临时暴露 HTTPS 回调地址。

## 1. Why This Revision Exists

原方案依赖云端常驻服务和长期可用的公网 HTTPS webhook。用户现在明确选择：

- 不希望为部署付费
- 有一台长期在线、可 SSH 的 Mac Studio
- 更关心稳定提醒，而不是“必须云端”

在这个前提下，继续坚持 Render Free 或长期免费隧道都会引入不必要的不稳定性。因此本次设计改为“本地常驻优先”。

## 2. Revised Product Constraints

### Required

- 零额外云主机成本
- Telegram 继续作为唯一主入口和主提醒渠道
- TickTick 仍然是任务真源
- 晨报、晚报、T-5 提醒、snooze、时间窗口任务、长期记忆能力保留
- Mac Studio 重启后可自动恢复服务

### Accepted Trade-offs

- 只要 Mac Studio 断网、关机、休眠，提醒就会中断
- TickTick OAuth 首次授权不再依赖常驻公网域名，而是走“临时隧道 + 一次性回调”
- Phase 1 不再优先实现 Telegram webhook

## 3. Architecture Overview

系统改为 5 个运行单元：

### 3.1 Local API Process

职责：

- 提供 `/health`
- 预留未来管理接口
- 可选承接 TickTick OAuth callback
- 不再作为 Telegram 主接收入口

运行方式：

- 由 `uvicorn` 在 Mac Studio 本地监听 `127.0.0.1` 或局域网地址

### 3.2 Telegram Polling Runner

职责：

- 使用 Telegram `getUpdates` 长轮询获取新消息
- 将文本消息、按钮回调、位置消息转换为统一的内部 update 对象
- 驱动现有对话理解和执行流程

关键点：

- 与 webhook 互斥
- 需要保存 `last_update_id`
- 启动时应支持从数据库或状态表恢复 offset

### 3.3 Scheduler / Reminder Runner

职责：

- 调度晨报、晚报、T-5 提醒、窗口任务轻提醒、snooze
- 周期性扫描提醒事件表
- 保证重复发送保护

关键点：

- 与 polling runner 运行在同一守护进程内更简单
- 即使没有外部流量，提醒也能继续发送

### 3.4 Local Persistence

职责：

- 继续使用 Postgres 保存用户、记忆、上下文、任务影子、提醒事件、动作日志

运行方式：

- Mac Studio 本机 `docker compose` 启动 `postgres`

### 3.5 Temporary OAuth Exposure

职责：

- 仅在 TickTick 首次 OAuth 授权时，临时将本地 callback 暴露到公网

策略：

- 优先使用临时 HTTPS 隧道
- 授权完成并持久化 token 后即可关闭
- 日常 Telegram 消息和提醒流程不依赖公网入口

## 4. Telegram Integration Strategy

### Primary Mode: Polling

默认接收模式改为 Telegram polling。

优点：

- 不需要长期 HTTPS 域名
- 不依赖免费云服务不休眠
- 更适合单用户、单机常驻的个人助理

要求：

- 启动前若已设置 webhook，应先调用 `deleteWebhook`
- 使用 `getUpdates` 循环拉取消息
- 遇到短暂网络错误时自动退避重试

### Secondary Mode: Webhook

Webhook 保留为次要模式：

- 仅在未来切回云端或需要公网回调时复用
- 不作为本地部署的默认路径

## 5. Process Model

### 5.1 Runtime Composition

推荐单一常驻进程：

- 线程/协程 A：Telegram polling
- 线程/协程 B：scheduler tick
- 主应用容器：共享 settings、数据库 session factory、service graph

这样可以减少多进程协调、锁和状态同步的复杂度。

### 5.2 Startup Sequence

服务启动顺序：

1. 加载 `.env`
2. 初始化数据库连接
3. 确保表结构已迁移完成
4. 调用 Telegram `deleteWebhook`
5. 读取上次的 polling offset
6. 启动 polling loop
7. 启动 scheduler loop

### 5.3 Shutdown Sequence

关闭时：

- 记录最后确认的 `update_id`
- 优雅停止 scheduler
- 关闭数据库连接

## 6. Deployment Model on Mac Studio

### 6.1 Directory

用户指定部署目录：

- `~/doc_unsyn/TickTick_Codex`

该目录用于：

- Git 工作副本
- 本地 `.env`
- `docker compose`
- `launchd` 所需脚本
- 可选日志目录

### 6.2 Service Management

使用 `launchd` 做常驻和开机自启。

要求：

- 用户登录后自动启动
- 进程退出后自动拉起
- 标准输出和标准错误写入本地日志文件

### 6.3 Sleep / Power Settings

必须要求 Mac Studio：

- 不自动睡眠
- 重启后自动恢复网络和 `launchd`

否则提醒稳定性无法保证。

## 7. TickTick OAuth on Local Hosting

### 7.1 Why Temporary HTTPS Is Enough

TickTick OAuth 需要浏览器回跳到 `redirect_uri`。这个回跳在首次授权、token 失效重授权等少数场景才需要，不是持续消息通道。

因此：

- 不需要长期公网入口
- 只需要在授权时临时获得一个 HTTPS callback 地址

### 7.2 Local OAuth Flow

建议流程：

1. 本地 API 进程提供 `/auth/ticktick/callback`
2. 启动临时 HTTPS 隧道，将本地端口暴露出去
3. 把临时 URL 填入 TickTick 开发者后台 `OAuth redirect URL`
4. 用户点击授权链接完成登录
5. callback 接收 `code`
6. 后端换取 token 并持久化
7. 完成后关闭隧道

注意：

- 如果 TickTick 开发者后台要求固定 redirect URL，需在每次改动隧道地址时同步更新
- 后续可考虑换成自有域名或长期 tunnel，但 Phase 1 不强制

## 8. Implementation Boundaries

### Phase 1 In Scope

- Telegram polling runner
- 本地 scheduler runner
- 本地 Postgres 部署文档
- `launchd` 自启动脚本与文档
- webhook 降级为非默认模式
- 本地 TickTick OAuth callback 路由与临时隧道文档

### Phase 1 Out of Scope

- 长期公网服务
- 多用户支持
- 高可用/多进程横向扩展
- 完整 secrets 管理平台

## 9. Risks and Mitigations

### Risk: Mac Studio 休眠或断网

缓解：

- 配置不睡眠
- 增加本地健康检查与日志

### Risk: Polling offset 丢失造成重复处理

缓解：

- 每次处理成功后持久化最新 offset
- 处理逻辑要求幂等

### Risk: TickTick OAuth 临时隧道地址变化

缓解：

- 将授权流程显式做成一次性运维步骤
- 文档中明确说明如何更新 redirect URL

### Risk: 当前 reminder worker 只是空壳

缓解：

- 在本轮改造中把 reminder polling/scheduler 真正接入主进程
- 把“代码结构已存在但未运行”转成“本地守护进程可执行”

## 10. Success Criteria

以下条件同时满足，视为本轮改造成功：

- Mac Studio 上可通过单条启动命令启动完整本地服务
- 重启机器后可由 `launchd` 自动恢复运行
- Telegram 文本消息无需 webhook 即可被机器人接收
- 每天晨报、晚报和 T-5 提醒由本地 scheduler 发出
- 本地 Postgres 保存记忆、提醒和上下文数据
- TickTick OAuth 至少具备可执行的本地 callback + 临时 HTTPS 授权路径
