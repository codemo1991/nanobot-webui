# Memory 迁移至 SQLite 实施方案（已确认）

## 核心决策确认

| 项目 | 决策 |
|-----|------|
| Embedding 功能 | 现阶段不实现 |
| 数据迁移 | 自动迁移（启动时检测） |
| 镜室 Memory | 包含悟/辩/赏所有数据 |
| 定时总结频率 | 保持 60 分钟配置不变 |

## 实施计划

### Phase 1: 创建 MemoryRepository 存储层
- 创建 `nanobot/storage/memory_repository.py`
- 实现 SQLite 表结构（memory_entries, daily_notes, mirror_shang_records, memory_fts）
- 实现基础 CRUD 和 FTS5 全文搜索

### Phase 2: 修改 MemoryStore 兼容层
- 修改 `nanobot/agent/memory.py`
- 内部使用 MemoryRepository，保持现有 API 不变

### Phase 3: 修改 MemoryMaintenanceService
- 修改 `nanobot/services/memory_maintenance.py`
- 使用新 Repository，保全定时总结和每日合并功能
- 支持多 scope（global, mirror-wu, mirror-bian, mirror-shang）

### Phase 4: 修改 MirrorService
- 修改 `nanobot/services/mirror_service.py`
- 迁移赏记录存储到 SQLite
- 修改分析写入逻辑

### Phase 5: 实现数据自动迁移
- 自动检测旧版文件
- 迁移全局记忆、Agent 隔离记忆、镜室记忆
- 保留原文件为 .backup

### Phase 6: 测试验证
- 数据迁移测试
- 定时总结功能测试
- 每日合并功能测试

---

用户已确认方案，准备开始实施。