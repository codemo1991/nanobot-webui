import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Tabs, Modal } from 'antd'
import { EyeOutlined, BulbOutlined, ThunderboltOutlined, UserOutlined } from '@ant-design/icons'
import ShangTab from './mirror/ShangTab'
import WuTab from './mirror/WuTab'
import BianTab from './mirror/BianTab'
import WoTab from './mirror/WoTab'
import './MirrorPage.css'

const MIRROR_GUIDE_KEY = 'mirror-guide-seen'

function MirrorPage() {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState('shang')
  const [showGuide, setShowGuide] = useState(false)

  useEffect(() => {
    try {
      if (!localStorage.getItem(MIRROR_GUIDE_KEY)) setShowGuide(true)
    } catch {
      setShowGuide(false)
    }
  }, [])

  const handleCloseGuide = () => {
    setShowGuide(false)
    try {
      localStorage.setItem(MIRROR_GUIDE_KEY, '1')
    } catch { /* ignore */ }
  }

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
      <Modal
        open={showGuide}
        title={t('mirror.guideTitle')}
        onCancel={handleCloseGuide}
        onOk={handleCloseGuide}
        okText={t('mirror.guideGotIt')}
        cancelButtonProps={{ style: { display: 'none' } }}
        width={480}
      >
        <div style={{ lineHeight: 1.8 }}>
          <p><strong>{t('mirror.wu')}</strong> — {t('mirror.guideWu')}</p>
          <p><strong>{t('mirror.bian')}</strong> — {t('mirror.guideBian')}</p>
          <p><strong>{t('mirror.shang')}</strong> — {t('mirror.guideShang')}</p>
          <p><strong>{t('mirror.wo')}</strong> — {t('mirror.guideWo')}</p>
        </div>
      </Modal>
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
