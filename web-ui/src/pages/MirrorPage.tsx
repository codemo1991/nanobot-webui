import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Tabs } from 'antd'
import { EyeOutlined, BulbOutlined, ThunderboltOutlined, UserOutlined } from '@ant-design/icons'
import ShangTab from './mirror/ShangTab'
import WuTab from './mirror/WuTab'
import BianTab from './mirror/BianTab'
import WoTab from './mirror/WoTab'
import './MirrorPage.css'

function MirrorPage() {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState('shang')

  const tabItems = [
    {
      key: 'shang',
      label: (
        <span className="mirror-tab-label">
          <EyeOutlined /> {t('mirror.shang')}
        </span>
      ),
      children: <ShangTab />,
    },
    {
      key: 'wu',
      label: (
        <span className="mirror-tab-label">
          <BulbOutlined /> {t('mirror.wu')}
        </span>
      ),
      children: <WuTab />,
    },
    {
      key: 'bian',
      label: (
        <span className="mirror-tab-label">
          <ThunderboltOutlined /> {t('mirror.bian')}
        </span>
      ),
      children: <BianTab />,
    },
    {
      key: 'wo',
      label: (
        <span className="mirror-tab-label">
          <UserOutlined /> {t('mirror.wo')}
        </span>
      ),
      children: <WoTab />,
    },
  ]

  return (
    <div className="mirror-page">
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={tabItems}
        className="mirror-tabs"
        size="large"
        centered
      />
    </div>
  )
}

export default MirrorPage
