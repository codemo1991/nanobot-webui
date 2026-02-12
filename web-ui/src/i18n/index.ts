import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import zhCN from './locales/zh-CN.json'
import en from './locales/en.json'

export const SUPPORTED_LANGS = [
  { code: 'zh-CN', label: '简体中文' },
  { code: 'en', label: 'English' },
] as const

export type LocaleCode = (typeof SUPPORTED_LANGS)[number]['code']

const STORAGE_KEY = 'nanobot-locale'

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      'zh-CN': { translation: zhCN },
      en: { translation: en },
    },
    fallbackLng: 'zh-CN',
    supportedLngs: ['zh-CN', 'en'],
    load: 'currentOnly',
    interpolation: { escapeValue: false },
    detection: {
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: STORAGE_KEY,
      caches: ['localStorage'],
      convertDetectedLanguage: (lng: string) =>
        lng.startsWith('zh') ? 'zh-CN' : lng.startsWith('en') ? 'en' : 'zh-CN',
    },
  })

export function setLocale(code: LocaleCode) {
  i18n.changeLanguage(code)
  localStorage.setItem(STORAGE_KEY, code)
}

export default i18n
