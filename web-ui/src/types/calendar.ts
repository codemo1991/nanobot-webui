// 日历相关类型定义

// 紧急程度
export type Priority = 'high' | 'medium' | 'low';

// 提醒配置
export interface Reminder {
  id: string;
  time: number; // 提前分钟数，0表示事件发生时
  notified: boolean; // 是否已提醒
}

// 重复规则
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

// 紧急程度颜色映射
export const priorityColors = {
  high: {
    bg: '#fff2f0',
    border: '#ffccc7',
    text: '#cf1322',
    dot: '#FF4D4F',
    css: '#FF4D4F'
  },
  medium: {
    bg: '#fffbe6',
    border: '#ffe58f',
    text: '#d48806',
    dot: '#FAAD14',
    css: '#FAAD14'
  },
  low: {
    bg: '#f6ffed',
    border: '#b7eb8f',
    text: '#389e0d',
    dot: '#52C41A',
    css: '#52C41A'
  }
};

// 提醒时间选项
export const reminderOptions = [
  { label: '事件发生时', value: 0 },
  { label: '提前 5 分钟', value: 5 },
  { label: '提前 15 分钟', value: 15 },
  { label: '提前 30 分钟', value: 30 },
  { label: '提前 1 小时', value: 60 },
  { label: '提前 1 天', value: 1440 },
];
