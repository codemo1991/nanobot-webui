## Token优化方案实施计划

### 目标
将简单请求的token消耗从 ~42,000 降低到 ~8,000-12,000

### 实施步骤

#### 1. 添加Token估算工具函数
- 在 `nanobot/utils/helpers.py` 中添加 `estimate_tokens()` 函数
- 使用简单的字符计数估算（中文约1.5字符/token，英文约4字符/token）

#### 2. 实现Token预算系统
修改 `nanobot/agent/context.py`：
- 为 `build_system_prompt()` 添加 `max_tokens` 参数
- 按优先级分配预算：身份(必须) → Bootstrap → Memory → Skills
- 超出预算时智能裁剪

#### 3. 实现工具定义按需加载
修改 `nanobot/agent/loop.py`：
- 添加 `_select_tools_for_message()` 方法
- 根据消息关键词选择相关工具
- 基础工具始终加载，其他按需加载

#### 4. 添加配置参数
修改 `nanobot/config/schema.py`：
- 添加 `token_budget` 配置项
- 支持运行时热更新

#### 5. 预期效果
| 组件 | 优化前 | 优化后 |
|------|--------|--------|
| System Prompt | ~3000 | ~1500 |
| 工具定义 | ~3000 | ~800 |
| Memory | ~6000 | ~2000 |
| Skills | ~1000 | ~500 |
| **总计** | ~42000 | ~10000 |