import { useTranslation } from 'react-i18next'
import './SkillMarketPage.css'

function SkillMarketPage() {
  const { t } = useTranslation()
  return (
    <div className="skill-market-page">
      <div className="page-header">
        <h1>🛒 {t('skillMarket.title')}</h1>
        <p className="page-description">{t('skillMarket.description')}</p>
      </div>

      <div className="market-toolbar">
        <input
          type="text"
          className="input search-input"
          placeholder={t('skillMarket.searchPlaceholder')}
        />
        <select className="input">
          <option>{t('skillMarket.allTags')}</option>
          <option>{t('skillMarket.codeProcess')}</option>
          <option>{t('skillMarket.docProcess')}</option>
          <option>{t('skillMarket.dataAnalysis')}</option>
        </select>
        <select className="input">
          <option>{t('skillMarket.latestUpdate')}</option>
          <option>{t('skillMarket.mostDownloads')}</option>
          <option>{t('skillMarket.highestRating')}</option>
        </select>
      </div>

      <div className="market-content">
        <div className="empty-state">
          <h2>🚀 {t('skillMarket.comingSoon')}</h2>
          <p>{t('skillMarket.preparing')}</p>
          <p className="text-secondary">{t('skillMarket.useBuilder')}</p>
        </div>
      </div>
    </div>
  )
}

export default SkillMarketPage
