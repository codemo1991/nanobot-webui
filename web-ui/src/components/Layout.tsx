import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { Dropdown, Button } from 'antd'
import type { MenuProps } from 'antd'
import { GlobalOutlined, LoginOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { SUPPORTED_LANGS, setLocale, type LocaleCode } from '../i18n'
import './Layout.css'

function Layout() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()

  const langMenuItems: MenuProps['items'] = SUPPORTED_LANGS.map(({ code, label }) => ({
    key: code,
    label,
    onClick: () => setLocale(code as LocaleCode),
  }))

  return (
    <div className="layout">
      <nav className="sidebar">
        <div className="sidebar-header">
          <h1>🐈 Nanobot</h1>
        </div>
        <div className="sidebar-nav">
          <NavLink to="/chat" className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'}>
            💬 {t('nav.chat')}
          </NavLink>
          <NavLink to="/mirror" className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'}>
            🪞 {t('nav.mirror')}
          </NavLink>
          <NavLink to="/calendar" className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'}>
            📅 {t('nav.calendar')}
          </NavLink>
          <NavLink to="/cron" className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'}>
            ⏰ {t('nav.cron')}
          </NavLink>
          <NavLink to="/config" className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'}>
            ⚙️ {t('nav.config')}
          </NavLink>
          <NavLink to="/trace" className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'}>
            🔍 {t('nav.trace')}
          </NavLink>
          <NavLink to="/system" className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'}>
            📊 {t('nav.system')}
          </NavLink>
        </div>
        <div className="sidebar-footer">
          <Button
            type="text"
            icon={<LoginOutlined />}
            className="lang-switcher"
            onClick={() => navigate('/login')}
            title={t('nav.login') ?? '登录'}
          >
            {t('nav.login') ?? '登录'}
          </Button>
          <Dropdown menu={{ items: langMenuItems }} placement="topRight">
            <Button type="text" icon={<GlobalOutlined />} className="lang-switcher">
              {SUPPORTED_LANGS.find((l) => l.code === i18n.language || i18n.language?.startsWith(l.code.split('-')[0]))?.label ?? SUPPORTED_LANGS[1].label}
            </Button>
          </Dropdown>
        </div>
      </nav>
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  )
}

export default Layout
