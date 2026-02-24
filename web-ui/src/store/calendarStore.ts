import { create } from 'zustand';
import { api } from '../api';
import type { CalendarEvent, CalendarSettings } from '../types';

// 后端返回的类型 (snake_case)
interface BackendCalendarEvent {
  id: string
  title: string
  description?: string
  start_time: string
  end_time: string
  is_all_day: number  // SQLite stores boolean as 0/1
  priority: string
  reminders_json?: string
  recurrence_json?: string
  recurrence_id?: string
  created_at: string
  updated_at: string
}

interface BackendCalendarSettings {
  default_view: string
  default_priority: string
  sound_enabled: number  // SQLite stores boolean as 0/1
  notification_enabled: number
}

// 转换后端事件到前端类型
function toFrontendEvent(backend: BackendCalendarEvent): CalendarEvent {
  let reminders: CalendarEvent['reminders'] = []
  try {
    reminders = backend.reminders_json ? JSON.parse(backend.reminders_json) : []
  } catch {
    reminders = []
  }

  let recurrence: CalendarEvent['recurrence'] | undefined
  try {
    recurrence = backend.recurrence_json ? JSON.parse(backend.recurrence_json) : undefined
  } catch {
    recurrence = undefined
  }

  return {
    id: backend.id,
    title: backend.title,
    description: backend.description,
    start: backend.start_time,
    end: backend.end_time,
    isAllDay: backend.is_all_day === 1,
    priority: (backend.priority || 'medium') as CalendarEvent['priority'],
    reminders,
    recurrence,
    recurrenceId: backend.recurrence_id,
    createdAt: backend.created_at,
    updatedAt: backend.updated_at,
  }
}

// 转换前端事件到后端类型
function toBackendEvent(event: Partial<CalendarEvent>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  if (event.id !== undefined) result.id = event.id
  if (event.title !== undefined) result.title = event.title
  if (event.description !== undefined) result.description = event.description
  if (event.start !== undefined) result.start_time = event.start
  if (event.end !== undefined) result.end_time = event.end
  if (event.isAllDay !== undefined) result.is_all_day = event.isAllDay ? 1 : 0
  if (event.priority !== undefined) result.priority = event.priority
  if (event.reminders !== undefined) result.reminders_json = JSON.stringify(event.reminders)
  if (event.recurrence !== undefined) result.recurrence_json = JSON.stringify(event.recurrence)
  if (event.recurrenceId !== undefined) result.recurrence_id = event.recurrenceId
  return result
}

// 转换后端设置到前端类型
function toFrontendSettings(backend: BackendCalendarSettings): CalendarSettings {
  return {
    defaultView: (backend.default_view || 'dayGridMonth') as CalendarSettings['defaultView'],
    defaultPriority: (backend.default_priority || 'medium') as CalendarSettings['defaultPriority'],
    soundEnabled: Boolean(backend.sound_enabled),
    notificationEnabled: Boolean(backend.notification_enabled),
  }
}

// 转换前端设置到后端类型
function toBackendSettings(settings: Partial<CalendarSettings>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  if (settings.defaultView !== undefined) result.default_view = settings.defaultView
  if (settings.defaultPriority !== undefined) result.default_priority = settings.defaultPriority
  if (settings.soundEnabled !== undefined) result.sound_enabled = settings.soundEnabled ? 1 : 0
  if (settings.notificationEnabled !== undefined) result.notification_enabled = settings.notificationEnabled ? 1 : 0
  return result
}

interface CalendarStore {
  // 事件状态
  events: CalendarEvent[];
  selectedEvent: CalendarEvent | null;
  isEventModalOpen: boolean;
  editingEventId: string | null;

  // 设置状态
  settings: CalendarSettings;

  // 视图状态
  currentDate: string;
  currentView: 'dayGridMonth' | 'timeGridWeek' | 'timeGridDay';

  // 加载状态
  loading: boolean;
  error: string | null;

  // Actions
  loadEvents: (start?: string, end?: string) => Promise<void>;
  addEvent: (event: Omit<CalendarEvent, 'id' | 'createdAt' | 'updatedAt'>) => Promise<string>;
  updateEvent: (id: string, updates: Partial<CalendarEvent>) => Promise<void>;
  deleteEvent: (id: string) => Promise<void>;
  setSelectedEvent: (event: CalendarEvent | null) => void;
  setEventModalOpen: (open: boolean) => void;
  setEditingEventId: (id: string | null) => void;
  setCurrentDate: (date: string) => void;
  setCurrentView: (view: 'dayGridMonth' | 'timeGridWeek' | 'timeGridDay') => void;
  loadSettings: () => Promise<void>;
  updateSettings: (settings: Partial<CalendarSettings>) => Promise<void>;
  markReminderNotified: (eventId: string, reminderId: string) => void;
}

const defaultSettings: CalendarSettings = {
  defaultView: 'dayGridMonth',
  defaultPriority: 'medium',
  soundEnabled: true,
  notificationEnabled: true,
};

export const useCalendarStore = create<CalendarStore>()(
  (set) => ({
    events: [],
    selectedEvent: null,
    isEventModalOpen: false,
    editingEventId: null,
    settings: defaultSettings,
    currentDate: new Date().toISOString(),
    currentView: 'dayGridMonth',
    loading: false,
    error: null,

    loadEvents: async (start?: string, end?: string) => {
      set({ loading: true, error: null });
      try {
        const backendEvents = await api.getCalendarEvents(start, end) as unknown as BackendCalendarEvent[];
        const events = backendEvents.map(toFrontendEvent);
        set({ events, loading: false });
      } catch (error) {
        console.error('Failed to load calendar events:', error);
        set({ error: (error as Error).message, loading: false });
      }
    },

    addEvent: async (eventData) => {
      set({ loading: true, error: null });
      try {
        const backendEvent = toBackendEvent(eventData);
        const created = await api.createCalendarEvent(backendEvent as any);
        const event = toFrontendEvent(created as unknown as BackendCalendarEvent);
        set((state) => ({
          events: [...state.events, event],
          loading: false,
        }));
        return event.id;
      } catch (error) {
        console.error('Failed to create calendar event:', error);
        set({ error: (error as Error).message, loading: false });
        throw error;
      }
    },

    updateEvent: async (id, updates) => {
      set({ loading: true, error: null });
      try {
        const backendUpdates = toBackendEvent(updates);
        const updated = await api.updateCalendarEvent(id, backendUpdates as any);
        if (updated) {
          const event = toFrontendEvent(updated as unknown as BackendCalendarEvent);
          set((state) => ({
            events: state.events.map((e) => (e.id === id ? event : e)),
            loading: false,
          }));
        }
      } catch (error) {
        console.error('Failed to update calendar event:', error);
        set({ error: (error as Error).message, loading: false });
        throw error;
      }
    },

    deleteEvent: async (id) => {
      set({ loading: true, error: null });
      try {
        await api.deleteCalendarEvent(id);
        set((state) => ({
          events: state.events.filter((e) => e.id !== id),
          selectedEvent: state.selectedEvent?.id === id ? null : state.selectedEvent,
          loading: false,
        }));
      } catch (error) {
        console.error('Failed to delete calendar event:', error);
        set({ error: (error as Error).message, loading: false });
        throw error;
      }
    },

    setSelectedEvent: (event) => {
      set({ selectedEvent: event });
    },

    setEventModalOpen: (open) => {
      set({ isEventModalOpen: open });
      if (!open) {
        set({ editingEventId: null, selectedEvent: null });
      }
    },

    setEditingEventId: (id) => {
      set({ editingEventId: id });
    },

    setCurrentDate: (date) => {
      set({ currentDate: date });
    },

    setCurrentView: (view) => {
      set({ currentView: view });
    },

    loadSettings: async () => {
      try {
        const backendSettings = await api.getCalendarSettings();
        const settings = toFrontendSettings(backendSettings as unknown as BackendCalendarSettings);
        set({ settings });
      } catch (error) {
        console.error('Failed to load calendar settings:', error);
      }
    },

    updateSettings: async (newSettings) => {
      try {
        const backendSettings = toBackendSettings(newSettings);
        const updated = await api.updateCalendarSettings(backendSettings as any);
        const settings = toFrontendSettings(updated as unknown as BackendCalendarSettings);
        set({ settings });
      } catch (error) {
        console.error('Failed to update calendar settings:', error);
        throw error;
      }
    },

    markReminderNotified: (eventId, reminderId) => {
      set((state) => ({
        events: state.events.map((event) =>
          event.id === eventId
            ? {
                ...event,
                reminders: (event.reminders || []).map((r) =>
                  r.id === reminderId ? { ...r, notified: true } : r
                ),
              }
            : event
        ),
      }));
    },
  })
);
