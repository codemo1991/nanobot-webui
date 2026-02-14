# 技能系统Token优化方案

## 问题分析

通过分析 Terminal 日志和源代码，发现以下问题：

1. **技能摘要过长**：日志显示技能信息被截断（"... (截断, 共 2162 字)"）
2. **重复文件读取**：每次构建system prompt都读取所有SKILL.md文件提取description
3. **无长度限制**：技能description字段没有长度限制
4. **无智能匹配**：所有技能信息无差别传递给LLM

## 优化方案

### 1. 技能信息三级分级系统

| 级别 | 内容 | Token消耗 | 使用场景 |
|------|------|-----------|----------|
| Level 0 | 名称+极简描述(≤50字符) | ~20 tokens | 默认展示 |
| Level 1 | 名称+短描述+状态+关键词 | ~50 tokens | 关键词匹配后 |
| Level 2 | 完整SKILL.md内容 | ~500+ tokens | 技能触发时 |

### 2. 动态技能加载机制

- **智能匹配**：根据用户消息关键词预筛选相关技能
- **按需加载**：只有匹配的技能才传递详细信息
- **核心技能优先**：`always=true`的技能始终完整加载

### 3. 技能元数据缓存

- 启动时扫描并缓存所有技能元数据
- 避免每次请求重复读取文件
- 支持热更新机制

### 4. 描述长度强制限制

- description字段限制50字符
- 新增`short_description`字段用于摘要展示
- 超长描述自动截断

## 实施步骤

### Step 1: 扩展SKILL.md元数据格式
- 新增 `short_description` 字段
- 新增 `keywords` 字段用于关键词匹配
- 新增 `category` 字段用于分类

### Step 2: 实现技能缓存系统
- 在 `SkillsLoader` 中添加元数据缓存
- 实现 `_build_metadata_cache()` 方法
- 支持缓存刷新

### Step 3: 实现智能匹配算法
- 添加 `_match_skills_by_keywords()` 方法
- 根据用户消息匹配相关技能
- 支持中英文关键词

### Step 4: 重构 `build_skills_summary()`
- 实现分级输出
- 支持按匹配度排序
- 限制总token消耗

### Step 5: 修改 `ContextBuilder.build_system_prompt()`
- 集成智能匹配
- 实现动态加载
- 保持向后兼容

## 预期效果

| 指标 | 优化前 | 优化后 | 改善 |
|------|--------|--------|------|
| 技能摘要Token | ~500 | ~100 | -80% |
| 文件读取次数 | 15次/请求 | 1次/启动 | -93% |
| System Prompt总Token | ~2000 | ~800 | -60% |

## 文件修改清单

1. `nanobot/agent/skills.py` - 核心优化
2. `nanobot/agent/context.py` - 集成优化
3. `nanobot/skills/*/SKILL.md` - 元数据更新（可选）