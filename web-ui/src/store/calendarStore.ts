import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { v4 as uuidv4 } from 'uuid';
import type { CalendarEvent, CalendarSettings } from '../types/calendar';

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

  // Actions
  addEvent: (event: Omit<CalendarEvent, 'id' | 'createdAt' | 'updatedAt'>) => string;
  updateEvent: (id: string, updates: Partial<CalendarEvent>) => void;
  deleteEvent: (id: string) => void;
  setSelectedEvent: (event: CalendarEvent | null) => void;
  setEventModalOpen: (open: boolean) => void;
  setEditingEventId: (id: string | null) => void;
  setCurrentDate: (date: string) => void;
  setCurrentView: (view: 'dayGridMonth' | 'timeGridWeek' | 'timeGridDay') => void;
  updateSettings: (settings: Partial<CalendarSettings>) => void;
  markReminderNotified: (eventId: string, reminderId: string) => void;
}

const defaultSettings: CalendarSettings = {
  defaultView: 'dayGridMonth',
  defaultPriority: 'medium',
  soundEnabled: true,
  notificationEnabled: true,
};

export const useCalendarStore = create<CalendarStore>()(
  persist(
    (set) => ({
      events: [],
      selectedEvent: null,
      isEventModalOpen: false,
      editingEventId: null,
      settings: defaultSettings,
      currentDate: new Date().toISOString(),
      currentView: 'dayGridMonth',

      addEvent: (eventData) => {
        const id = uuidv4();
        const now = new Date().toISOString();
        const newEvent: CalendarEvent = {
          ...eventData,
          id,
          createdAt: now,
          updatedAt: now,
        };
        set((state) => ({ events: [...state.events, newEvent] }));
        return id;
      },

      updateEvent: (id, updates) => {
        set((state) => ({
          events: state.events.map((event) =>
            event.id === id
              ? { ...event, ...updates, updatedAt: new Date().toISOString() }
              : event
          ),
        }));
      },

      deleteEvent: (id) => {
        set((state) => ({
          events: state.events.filter((event) => event.id !== id),
          selectedEvent: state.selectedEvent?.id === id ? null : state.selectedEvent,
        }));
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

      updateSettings: (newSettings) => {
        set((state) => ({
          settings: { ...state.settings, ...newSettings },
        }));
      },

      markReminderNotified: (eventId, reminderId) => {
        set((state) => ({
          events: state.events.map((event) =>
            event.id === eventId
              ? {
                  ...event,
                  reminders: event.reminders.map((r) =>
                    r.id === reminderId ? { ...r, notified: true } : r
                  ),
                }
              : event
          ),
        }));
      },
    }),
    {
      name: 'nanobot-calendar-storage',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        events: state.events,
        settings: state.settings,
      }),
    }
  )
);
