# 统一图片处理路径设计

## 背景

当前 nanobot-webui 在处理图片时采用分裂架构：
- 主模型支持 vision → base64 直传 LLM
- 主模型不支持 vision → spawn 子 agent 专门分析图片

这种方式的问题是：
1. 架构不统一，增加了复杂度
2. 子 agent 需要额外的模型调用，增加延迟和成本
3. 与上游 nanobot 仓库的简洁设计不一致

## 目标

将图片处理统一为单一路径：直接 base64 传给 LLM，失败时 fallback 到 subagent。

## 架构变化

### 当前架构

```
用户发送图片
    │
    ▼
检查 _is_vision_model(self.model)
    │
    ├─ 支持 ──→ base64 直传 LLM
    │
    └─ 不支持 ──→ spawn vision subagent
                      │
                      ▼
                 返回图片描述
```

### 新架构

```
用户发送图片
    │
    ▼
直接 base64 传给 LLM
    │
    ├─ 成功 ──→ 返回结果
    │
    └─ 失败 ──→ fallback: spawn vision subagent
                      │
                      ▼
                 返回图片描述
```

## 改动详情

### 1. nanobot/agent/loop.py

#### 删除
- `_is_vision_model()` 方法（用于判断模型是否支持 vision 的关键词匹配逻辑）
- 图片场景下的 subagent 预判断分支（当前在检测到图片时先判断是否 spawn subagent）

#### 新增
- LLM 调用时的异常捕获：当收到图片不支持相关的错误时，自动 fallback 到 subagent 分析
- 保留 `subagent.run_vision_analysis()` 作为 fallback 路径

### 2. nanobot/agent/context.py

**保持不变** — 已有的 `_build_user_content()` 方法已实现 base64 编码逻辑。

### 3. nanobot/agent/subagent.py

**保持不变** — `run_vision_analysis()` 方法继续作为 fallback 使用。

## 行为变化

| 场景 | 旧行为 | 新行为 |
|------|--------|--------|
| Vision 模型 + 图片 | base64 直传 | base64 直传（不变） |
| 非 vision 模型 + 图片 | 直接 spawn subagent | 先尝试 base64 → 失败则 subagent |

### 关键改进

1. **统一路径**：所有图片都走 base64 路径，不再预判
2. **按需 fallback**：只有当 LLM 真正报错时才调用 subagent
3. **行为一致**：与上游 nanobot 仓库保持一致
4. **简化逻辑**：删除 `_is_vision_model()` 关键词匹配

## 异常处理

当 LLM 返回图片不支持错误时（如 400 Bad Request、model not support images 等），捕获异常并：
1. 从原始消息中提取图片文件路径
2. 调用 `subagent.run_vision_analysis()` 分析图片
3. 用图片文字描述替换消息中的图片
4. 重新调用 LLM 完成推理

## 测试场景

1. Vision 模型（qwen-vl、gpt-4o） + 图片 → 应直接成功
2. 非 vision 模型 + 图片 → 应先尝试，失败则 fallback
3. 非 vision 模型 + 图片（subagent 也失败） → 应返回明确错误

## 文件清单

- `nanobot/agent/loop.py` — 移除预判逻辑，添加 fallback 异常处理
