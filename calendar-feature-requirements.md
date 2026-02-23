# Nanobot 日历记事功能需求规划文档

## 文档信息
- **项目名称**: Nanobot Calendar（日历记事）
- **版本**: 1.0.0
- **创建日期**: 2026-02-23
- **状态**: 需求规划

---

## 1. 项目背景

### 1.1 项目概述

Nanobot 是一款 AI 助手产品，目前拥有 Web UI 界面。本需求文档旨在为 Nanobot Web UI 添加日历记事功能，允许用户记录事件、设置提醒、管理日程。

### 1.2 现有技术栈

| 类别 | 技术 | 版本 |
|------|------|------|
| 前端框架 | React | 18.2.0 |
| 语言 | TypeScript | 5.2.2 |
| UI 组件库 | Ant Design | 5.12.0 |
| 状态管理 | Zustand | 4.4.7 |
| 路由 | React Router DOM | 6.21.0 |
| 国际化 | i18next | 25.8.5 |
| 构建工具 | Vite | 5.0.8 |
| 图标 | @ant-design/icons | 5.2.6 |

---

## 2. 功能需求

### 2.1 核心功能概览

```
┌─────────────────────────────────────────────────────────────┐
│                     日历记事功能模块                         │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │  日历显示   │  │  事件管理   │  │  提醒系统   │         │
│  ├─────────────┤  ├─────────────┤  ├─────────────┤         │
│  │ • 月视图   │  │ • 添加事件  │  │ • 页面通知  │         │
│  │ • 周视图   │  │ • 编辑事件  │  │ • 声音提醒  │         │
│  │ • 日视图   │  │ • 删除事件  │  │ • 重复提醒  │         │
│  │ • 日期导航 │  │ • 拖拽调整  │  │ • 定时触发  │         │
│  │ • 事件标记 │  │ • 重复事件  │  │             │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
│  ┌─────────────┐  ┌─────────────┐                         │
│  │ 紧急程度    │  │  数据存储   │                         │
│  ├─────────────┤  ├─────────────┤                         │
│  │ • 高(红)   │  │ • 本地存储  │                         │
│  │ • 中(黄)   │  │ • 数据导出  │                         │
│  │ • 低(绿)   │  │ • 数据导入  │                         │
│  └─────────────┘  └─────────────┘                         │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 日历显示功能

#### 2.2.1 视图模式

| 视图 | 描述 | 适用场景 |
|------|------|----------|
| 月视图 | 显示整月日历格子，每天可显示事件标题 | 计划月度行程、查看整月概览 |
| 周视图 | 显示一周时间轴，每天按小时划分 | 规划本周工作、查看日程冲突 |
| 日视图 | 显示单日时间轴，精确到小时/分钟 | 安排当日具体时间点活动 |

#### 2.2.2 导航功能

- **上一月/周/日**: 切换到上一个时间周期
- **下一月/周/日**: 切换到下一个时间周期
- **今天**: 快速返回当前日期
- **日期选择器**: 直接跳转到指定日期

#### 2.2.3 日期标记

- 当前日期: 高亮显示（使用 Ant Design 主色）
- 有事件的日期: 显示事件数量徽章或圆点标记
- 周末: 可选区分显示

### 2.3 事件管理功能

#### 2.3.1 事件字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| 标题 | string | 是 | 事件名称，最长100字符 |
| 描述 | string | 否 | 事件详细说明，支持多行文本 |
| 开始时间 | datetime | 是 | 事件开始时间 |
| 结束时间 | datetime | 是 | 事件结束时间 |
| 紧急程度 | enum | 是 | high/medium/low |
| 提醒时间 | array | 否 | 提醒时间列表 |
| 重复规则 | object | 否 | 重复设置 |

#### 2.3.2 事件操作

**添加事件**
- 点击日期/时间格打开添加表单
- 快速添加: 仅需标题和时间
- 完整添加: 填写所有字段

**编辑事件**
- 点击已有事件打开编辑表单
- 支持修改所有字段

**删除事件**
- 单个删除: 删除当前选中的事件
- 批量删除: 删除重复事件系列

**拖拽调整**
- 在日历视图拖拽事件到新时间
- 自动更新事件时间

### 2.4 提醒功能

#### 2.4.1 提醒方式

| 方式 | 描述 | 兼容性 |
|------|------|--------|
| 页面通知 | 浏览器原生 Notification API | 现代浏览器 |
| 声音提醒 | 播放提示音 | 全部浏览器 |

#### 2.4.2 提醒时间设置

```
提醒选项:
├── 事件发生时
├── 提前 5 分钟
├── 提前 15 分钟
├── 提前 30 分钟
├── 提前 1 小时
├── 提前 1 天
└── 自定义时间
```

#### 2.4.3 重复提醒

- 用户确认前持续提醒
- 可设置提醒间隔（1分钟/5分钟/10分钟）

### 2.5 紧急程度

| 级别 | 标识颜色 | 使用场景 |
|------|----------|----------|
| 高 | #FF4D4F (红色) | 重要紧急事项，如会议截止日期 |
| 中 | #FAAD14 (黄色) | 重要但不紧急，如计划评审 |
| 低 | #52C41A (绿色) | 普通事项，如日常提醒 |

**显示规则**
- 月视图: 事件标题 + 颜色标记
- 周/日视图: 事件条带 + 颜色边框

---

## 3. 技术架构

### 3.1 日历组件选型

#### 方案对比

| 组件 | 优点 | 缺点 | 推荐度 |
|------|------|------|--------|
| FullCalendar | 功能全面、文档完善、支持多视图 | 体积较大、样式定制复杂 | ⭐⭐⭐ |
| react-big-calendar | React 原生、性能好 | 需自行实现部分功能 | ⭐⭐⭐ |
| antd Calendar | 与现有 UI 一致、轻量 | 功能相对基础 | ⭐⭐⭐⭐ |
| 自定义实现 | 完全可控、无依赖 | 开发周期长 | ⭐⭐ |

#### 推荐方案: FullCalendar

理由:
1. 功能最完善，支持所有视图和交互
2. 支持拖拽、事件编辑等开箱即用功能
3. 有 React 官方适配版本
4. 社区活跃、文档完善

**安装依赖:**
```bash
npm install @fullcalendar/react @fullcalendar/daygrid @fullcalendar/timegrid @fullcalendar/interaction @fullcalendar/core
```

### 3.2 数据模型设计

#### 事件数据结构 (TypeScript)

```typescript
// 紧急程度枚举
export type Priority = 'high' | 'medium' | 'low';

// 提醒配置
export interface Reminder {
  id: string;
  time: number; // 提前分钟数，0表示事件发生时
  notified: boolean; // 是否已提醒
}

// 重复规则 (iCalendar RRULE 格式子集)
export interface RecurrenceRule {
  frequency: 'daily' | 'weekly' | 'monthly' | 'yearly';
  interval: number; // 重复间隔
  endType: 'never' | 'count' | 'until';
  endCount?: number; // 重复次数
  endDate?: string; // 结束日期 ISO string
  weekdays?: number[]; // 周几重复 [0-6]
}

// 日历事件
export interface CalendarEvent {
  id: string;
  title: string;
  description?: string;
  start: string; // ISO 8601 datetime
  end: string;   // ISO 8601 datetime
  priority: Priority;
  reminders: Reminder[];
  recurrence?: RecurrenceRule;
  recurrenceId?: string; // 原始重复事件的 ID
  isAllDay: boolean;
  createdAt: string;
  updatedAt: string;
}

// 用户设置
export interface CalendarSettings {
  defaultView: 'dayGridMonth' | 'timeGridWeek' | 'timeGridDay';
  defaultPriority: Priority;
  soundEnabled: boolean;
  notificationEnabled: boolean;
}
```

#### 存储方案

采用 **本地存储 + 可选后端同步** 的混合方案:

```
┌─────────────────────────────────────────────┐
│                 数据存储层                   │
├─────────────────────────────────────────────┤
│  IndexedDB (Zustand Persist)                │
│  ├── events - 事件数据                      │
│  ├── settings - 用户设置                    │
│  └── syncQueue - 待同步队列                  │
├─────────────────────────────────────────────┤
│  可选: 后端 API (Future)                    │
│  ├── POST /api/calendar/events              │
│  ├── PUT /api/calendar/events/:id           │
│  ├── DELETE /api/calendar/events/:id         │
│  └── GET /api/calendar/events               │
└─────────────────────────────────────────────┘
```

**本地存储选型理由:**
- 数据保存在用户浏览器，隐私安全
- 无需后端开发即可使用
- IndexedDB 容量大（250MB+），适合大量事件
- Zustand 已集成 persist middleware

### 3.3 状态管理

使用 Zustand 创建独立的日历 Store:

```typescript
// src/store/calendarStore.ts
import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import type { CalendarEvent, CalendarSettings } from '../types/calendar'

interface CalendarStore {
  // 事件状态
  events: CalendarEvent[];
  selectedEvent: CalendarEvent | null;

  // 设置状态
  settings: CalendarSettings;

  // 视图状态
  currentDate: Date;
  currentView: 'dayGridMonth' | 'timeGridWeek' | 'timeGridDay';

  // Actions
  addEvent: (event: CalendarEvent) => void;
  updateEvent: (id: string, updates: Partial<CalendarEvent>) => void;
  deleteEvent: (id: string) => void;
  setSelectedEvent: (event: CalendarEvent | null) => void;
  setCurrentDate: (date: Date) => void;
  setCurrentView: (view: 'dayGridMonth' | 'timeGridWeek' | 'timeGridDay') => void;
  updateSettings: (settings: Partial<CalendarSettings>) => void;
}
```

### 3.4 提醒系统架构

```
┌─────────────────────────────────────────────────────┐
│                  提醒系统                           │
├─────────────────────────────────────────────────────┤
│  ┌─────────────┐    ┌─────────────┐                │
│  │ 定时检查器  │───▶│ 通知管理器  │                │
│  │ (每分钟)   │    │             │                │
│  └─────────────┘    └─────────────┘                │
│         │                   │                      │
│         ▼                   ▼                      │
│  ┌─────────────┐    ┌─────────────┐                │
│  │ 事件匹配    │    │ 浏览器通知  │                │
│  │ 检查提醒   │    │ 声音播放    │                │
│  └─────────────┘    └─────────────┘                │
└─────────────────────────────────────────────────────┘
```

**实现要点:**
- 使用 `setInterval` 每分钟检查一次
- 对比当前时间与提醒时间
- 支持页面关闭后的 Service Worker 提醒（可选扩展）

---

## 4. 用户界面设计

### 4.1 主界面布局

```
┌────────────────────────────────────────────────────────────┐
│  Nanobot                                        [⚙️] [🌐] │
├─────────┬──────────────────────────────────────────────────┤
│         │  ┌──────────────────────────────────────────┐  │
│  💬 Chat│  │           📅 2026年2月                   │  │
│  🪞 Mirror│ │  ◀  [今天]  ▶    [月▼]                │  │
│  📅 Calendar│ └──────────────────────────────────────────┘  │
│  ⚙️ Config│ ┌──────────────────────────────────────────┐  │
│  📊 System│  │                                      │  │
│         │  │           日历视图区域                 │  │
│         │  │           (FullCalendar)               │  │
│         │  │                                      │  │
│         │  └──────────────────────────────────────────┘  │
│         │ ┌─────────────────┐ ┌─────────────────────┐    │
│         │ │ 📋 今日事件     │ │ + 添加事件          │    │
│         │ │ ─────────────── │ │                     │    │
│         │ │ 🔴 项目截止     │ │                     │    │
│         │ │ 🟡 周会         │ │                     │    │
│         │ │ 🟢 健身         │ │                     │    │
│         │ └─────────────────┘ └─────────────────────┘    │
└─────────┴──────────────────────────────────────────────────┘
```

### 4.2 响应式设计断点

| 断点 | 宽度 | 布局 |
|------|------|------|
| Mobile | < 576px | 底部导航 + 全屏日历 |
| Tablet | 576px - 992px | 侧边栏折叠 + 日历 |
| Desktop | > 992px | 完整侧边栏 + 日历 + 事件列表 |

### 4.3 事件表单设计

使用 Ant Design Modal + Form 组件:

```tsx
// 事件表单字段布局
<Form layout="vertical">
  <Form.Item label="标题" required>
    <Input placeholder="事件标题" />
  </Form.Item>

  <Row gutter={16}>
    <Col span={12}>
      <Form.Item label="开始时间" required>
        <DatePicker showTime />
      </Form.Item>
    </Col>
    <Col span={12}>
      <Form.Item label="结束时间" required>
        <DatePicker showTime />
      </Form.Item>
    </Col>
  </Row>

  <Form.Item label="紧急程度">
    <Radio.Group>
      <Radio.Button value="high">🔴 高</Radio.Button>
      <Radio.Button value="medium">🟡 中</Radio.Button>
      <Radio.Button value="low">🟢 低</Radio.Button>
    </Radio.Group>
  </Form.Item>

  <Form.Item label="提醒">
    <Select mode="multiple" placeholder="选择提醒时间">
      <Select.Option value={0}>事件发生时</Select.Option>
      <Select.Option value={5}>提前 5 分钟</Select.Option>
      <Select.Option value={15}>提前 15 分钟</Select.Option>
      <Select.Option value={30}>提前 30 分钟</Select.Option>
      <Select.Option value={60}>提前 1 小时</Select.Option>
    </Select>
  </Form.Item>

  <Form.Item label="描述">
    <TextArea rows={3} placeholder="事件详细描述..." />
  </Form.Item>
</Form>
```

### 4.4 事件颜色映射

```typescript
const priorityColors = {
  high: {
    bg: '#fff2f0',
    border: '#ffccc7',
    text: '#cf1322',
    dot: '#FF4D4F'
  },
  medium: {
    bg: '#fffbe6',
    border: '#ffe58f',
    text: '#d48806',
    dot: '#FAAD14'
  },
  low: {
    bg: '#f6ffed',
    border: '#b7eb8f',
    text: '#389e0d',
    dot: '#52C41A'
  }
}
```

---

## 5. 文件结构规划

### 5.1 目录结构

```
web-ui/src/
├── components/
│   └── calendar/
│       ├── CalendarView.tsx       # 主日历组件
│       ├── EventModal.tsx         # 事件添加/编辑弹窗
│       ├── EventList.tsx         # 事件列表侧边栏
│       ├── TodayEvents.tsx       # 今日事件组件
│       └── CalendarHeader.tsx    # 日历头部导航
├── pages/
│   └── CalendarPage.tsx           # 日历页面
├── store/
│   └── calendarStore.ts           # 日历状态管理
├── types/
│   └── calendar.ts                # 日历类型定义
├── hooks/
│   ├── useCalendarEvents.ts       # 日历事件 Hook
│   ├── useNotifications.ts       # 通知提醒 Hook
│   └── useEventDrag.ts            # 拖拽功能 Hook
├── utils/
│   ├── calendar.ts                # 日历工具函数
│   ├── date.ts                    # 日期处理函数
│   └── notification.ts            # 通知工具
├── i18n/
│   └── locales/
│       ├── en.json                # 英文翻译
│       └── zh-CN.json             # 中文翻译
└── styles/
    └── calendar.css               # 日历样式
```

### 5.2 路由配置

在 `App.tsx` 中添加日历路由:

```tsx
// src/App.tsx
import CalendarPage from './pages/CalendarPage'

<Route path="calendar" element={<CalendarPage />} />
```

在导航中添加链接:

```tsx
// src/components/Layout.tsx
<NavLink to="/calendar" className={...}>
  📅 {t('nav.calendar')}
</NavLink>
```

---

## 6. 开发计划

### 6.1 分阶段实施

#### 第一阶段: 基础功能 (MVP)

**目标**: 实现可用的日历记事功能

| 任务 | 描述 | 预计工时 |
|------|------|----------|
| T1.1 | 创建日历页面路由和布局 | 0.5d |
| T1.2 | 集成 FullCalendar 组件 | 1d |
| T1.3 | 实现事件 CRUD 操作 | 1.5d |
| T1.4 | 实现本地数据存储 | 1d |
| T1.5 | 添加紧急程度标记 | 0.5d |

**交付物**: 可添加、编辑、删除事件的月视图日历

#### 第二阶段: 高级功能

**目标**: 完善用户体验

| 任务 | 描述 | 预计工时 |
|------|------|----------|
| T2.1 | 添加周视图和日视图 | 1d |
| T2.2 | 实现浏览器通知提醒 | 1d |
| T2.3 | 实现事件拖拽调整 | 0.5d |
| T2.4 | 添加重复事件功能 | 1.5d |
| T2.5 | 响应式布局适配 | 1d |

**交付物**: 功能完整的日历记事系统

#### 第三阶段: 优化体验

**目标**: 提升用户体验和扩展性

| 任务 | 描述 | 预计工时 |
|------|------|----------|
| T3.1 | 添加声音提醒 | 0.5d |
| T3.2 | 数据导出/导入功能 | 1d |
| T3.3 | 快捷键支持 | 0.5d |
| T3.4 | 后端 API 对接 (可选) | 2d |
| T3.5 | 多设备同步 (可选) | 3d |

### 6.2 依赖安装清单

```bash
# 核心依赖
npm install @fullcalendar/react @fullcalendar/daygrid @fullcalendar/timegrid @fullcalendar/interaction @fullcalendar/core date-fns uuid

# 类型定义
npm install -D @types/uuid
```

---

## 7. API 设计 (可选 - 后端扩展)

### 7.1 REST API 端点

| 方法 | 端点 | 描述 |
|------|------|------|
| GET | /api/calendar/events | 获取所有事件 |
| GET | /api/calendar/events/:id | 获取单个事件 |
| POST | /api/calendar/events | 创建事件 |
| PUT | /api/calendar/events/:id | 更新事件 |
| DELETE | /api/calendar/events/:id | 删除事件 |
| GET | /api/calendar/events/export | 导出事件 |
| POST | /api/calendar/events/import | 导入事件 |

### 7.2 API 响应格式

```typescript
// 响应包装
interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: {
    code: string;
    message: string;
  };
}

// 事件列表响应
interface EventsResponse {
  events: CalendarEvent[];
  total: number;
  page: number;
  pageSize: number;
}
```

---

## 8. 集成考虑

### 8.1 与现有系统集成

1. **导航集成**: 在 Layout 侧边栏添加日历入口
2. **样式统一**: 使用 Ant Design 组件保持视觉一致
3. **国际化**: 使用现有 i18next 系统
4. **状态管理**: 使用独立的 Zustand store，通过 persist 中间件持久化

### 8.2 用户系统集成

当前版本使用本地存储，无需用户认证。后续可扩展:
- 添加用户 ID 字段支持多用户
- 对接后端用户系统
- 实现数据隔离

### 8.3 数据备份

提供手动导出/导入功能:
- 导出格式: JSON
- 包含: 所有事件和设置
- 导入时合并或替换选择

---

## 9. 验收标准

### 9.1 功能验收

- [ ] 可以查看月/周/日三种视图
- [ ] 可以添加、编辑、删除事件
- [ ] 事件可以设置紧急程度（高/中/低）并正确显示颜色
- [ ] 可以设置提醒时间并收到浏览器通知
- [ ] 数据保存在浏览器本地，刷新后不丢失
- [ ] 拖拽事件可以调整时间

### 9.2 视觉验收

- [ ] 日历样式与现有界面风格一致
- [ ] 紧急程度颜色正确区分
- [ ] 响应式布局在不同屏幕尺寸下正常显示
- [ ] 动画过渡流畅

### 9.3 性能验收

- [ ] 日历渲染无明显卡顿
- [ ] 1000+ 事件下仍保持流畅
- [ ] 页面加载时间 < 2s

---

## 10. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| FullCalendar 样式与 antd 冲突 | UI 不一致 | 使用 CSS Modules 或调整选择器 |
| 浏览器通知权限被拒绝 | 提醒失效 | 优雅降级到声音提醒 |
| 本地存储空间不足 | 数据丢失 | 提示用户导出数据 |
| Service Worker 不支持 | 后台提醒失效 | 降级到页面内提醒 |

---

## 附录

### A. 快捷键列表

| 快捷键 | 功能 |
|--------|------|
| N | 新建事件 |
| T | 跳转到今天 |
| ←/→ | 上一个/下一个周期 |
| M | 月视图 |
| W | 周视图 |
| D | 日视图 |
| Esc | 关闭弹窗 |

### B. 颜色变量定义

```css
:root {
  --calendar-primary: #1677ff;
  --priority-high: #FF4D4F;
  --priority-medium: #FAAD14;
  --priority-low: #52C41A;
  --calendar-bg: #ffffff;
  --calendar-border: #f0f0f0;
}
```

---

*文档版本: 1.0.0*
*最后更新: 2026-02-23*
