# SSE 自动重连设计文档

**日期**: 2026-03-30
**状态**: 已批准
**负责人**: linjifeng

## 1. 概述

解决 SSE 连接断开后 WebUI 请求卡死的问题。通过在前端 `api.subscribeToChatStream` 中添加自动重连机制，提升连接稳定性。

## 2. 问题分析

- **现象**: 切换 Chrome 应用或其他应用时，SSE 连接可能被浏览器节流/断开，导致界面一直显示加载中
- **原因**: SSE 是长连接，浏览器在资源紧张时可能主动断开
- **影响**: 用户体验差，需要手动刷新页面才能恢复

## 3. 解决方案

在 `api.subscribeToChatStream` 中添加自动重连逻辑：

### 3.1 重连策略

- **最大重试次数**: 3 次
- **重试间隔**: 指数递增（1s, 2s, 3s）
- **终止条件**:
  - 达到最大重试次数
  - AbortSignal 被取消
  - 非断连错误（如 HTTP 4xx）

### 3.2 重连判断

仅在以下错误时重连：
- `fetch` 抛出网络错误
- HTTP 5xx 错误（服务端错误，可能恢复）

不重连：
- HTTP 4xx 错误（客户端问题，不会自行恢复）
- AbortSignal 已取消

### 3.3 实现位置

- `web-ui/src/api.ts` - `subscribeToChatStream` 方法
- `web-ui/src/api.ts` - `subscribeTraceStream` 方法（同步添加）
- `web-ui/src/api.ts` - `subagentProgressStream` 方法（可选）

## 4. 变更文件

| 文件 | 变更 |
|------|------|
| `web-ui/src/api.ts` | 修改 `subscribeToChatStream`、`subscribeTraceStream` 添加重连逻辑 |

## 5. 风险评估

- **风险**: 重连期间服务端可能已有响应，导致重复处理
- **缓解**: 每次重连使用相同的 sessionId，后端已有幂等处理
- **风险**: 多次重连失败可能导致用户长时间等待
- **缓解**: 最多 3 次，约 6 秒后仍失败则抛出错误
