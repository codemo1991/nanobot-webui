import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import './SkillBuilderPage.css'

function SkillBuilderPage() {
  const { t } = useTranslation()
  const [step, setStep] = useState(1)
  const totalSteps = 5

  return (
    <div className="skill-builder-page">
      <div className="page-header">
        <h1>🔧 {t('skillBuilder.title')}</h1>
        <p className="page-description">{t('skillBuilder.description')}</p>
      </div>

      <div className="builder-content">
        <div className="builder-sidebar">
          <div className="step-indicator">
            {[1, 2, 3, 4, 5].map((s) => (
              <div
                key={s}
                className={`step-item ${s === step ? 'active' : ''} ${s < step ? 'completed' : ''}`}
              >
                <div className="step-number">{s}</div>
                <div className="step-label">{getStepLabel(s, t)}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="builder-main">
          {step === 1 && <Step1BasicInfo />}
          {step === 2 && <Step2InputOutput />}
          {step === 3 && <Step3Runtime />}
          {step === 4 && <Step4Testing />}
          {step === 5 && <Step5Generate />}

          <div className="builder-actions">
            <button
              className="btn"
              onClick={() => setStep(Math.max(1, step - 1))}
              disabled={step === 1}
            >
              {t('skillBuilder.prev')}
            </button>
            <button
              className="btn btn-primary"
              onClick={() => setStep(Math.min(totalSteps, step + 1))}
              disabled={step === totalSteps}
            >
              {t('skillBuilder.next')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function getStepLabel(step: number, t: (k: string) => string): string {
  const keys: Record<number, string> = { 1: 'skillBuilder.step1', 2: 'skillBuilder.step2', 3: 'skillBuilder.step3', 4: 'skillBuilder.step4', 5: 'skillBuilder.step5' }
  return keys[step] ? t(keys[step]) : ''
}

function Step1BasicInfo() {
  const { t } = useTranslation()
  return (
    <div className="step-content">
      <h2>Step 1: {t('skillBuilder.step1')}</h2>
      <p className="step-description">{t('skillBuilder.basicInfo')}</p>
      
      <div className="form-group">
        <label>{t('skillBuilder.skillId')}</label>
        <input
          type="text"
          className="input"
          placeholder={t('skillBuilder.skillIdPlaceholder')}
        />
        <small>{t('skillBuilder.skillIdHint')}</small>
      </div>

      <div className="form-group">
        <label>{t('skillBuilder.skillName')}</label>
        <input
          type="text"
          className="input"
          placeholder={t('skillBuilder.skillNamePlaceholder')}
        />
      </div>

      <div className="form-group">
        <label>{t('skillBuilder.version')}</label>
        <input
          type="text"
          className="input"
          placeholder={t('skillBuilder.versionPlaceholder')}
          defaultValue="1.0.0"
        />
      </div>

      <div className="form-group">
        <label>{t('skillBuilder.description')}</label>
        <textarea
          className="input"
          rows={4}
          placeholder={t('skillBuilder.descPlaceholder')}
        />
      </div>

      <div className="form-group">
        <label>{t('skillBuilder.author')}</label>
        <input
          type="text"
          className="input"
          placeholder={t('skillBuilder.authorPlaceholder')}
        />
      </div>

      <div className="form-group">
        <label>{t('skillBuilder.tags')}</label>
        <input
          type="text"
          className="input"
          placeholder={t('skillBuilder.tagsPlaceholder')}
        />
      </div>
    </div>
  )
}

function Step2InputOutput() {
  const { t } = useTranslation()
  return (
    <div className="step-content">
      <h2>Step 2: {t('skillBuilder.step2')}</h2>
      <p className="step-description">{t('skillBuilder.inputOutput')}</p>
      
      <div className="empty-state">
        <p>{t('skillBuilder.ioSchema')}</p>
        <p className="text-secondary">{t('skillBuilder.ioSchemaHint')}</p>
      </div>
    </div>
  )
}

function Step3Runtime() {
  const { t } = useTranslation()
  return (
    <div className="step-content">
      <h2>Step 3: {t('skillBuilder.step3')}</h2>
      <p className="step-description">{t('skillBuilder.runtime')}</p>
      
      <div className="form-group">
        <label>{t('skillBuilder.runtimeLabel')}</label>
        <select className="input">
          <option>Python</option>
          <option>Node.js</option>
          <option>Shell</option>
        </select>
      </div>

      <div className="form-group">
        <label>{t('skillBuilder.entryLabel')}</label>
        <input
          type="text"
          className="input"
          placeholder={t('skillBuilder.entryPlaceholder')}
        />
      </div>

      <div className="form-group">
        <label>{t('skillBuilder.depsLabel')}</label>
        <textarea
          className="input"
          rows={4}
          placeholder={t('skillBuilder.depsPlaceholder')}
        />
      </div>
    </div>
  )
}

function Step4Testing() {
  const { t } = useTranslation()
  return (
    <div className="step-content">
      <h2>Step 4: {t('skillBuilder.step4')}</h2>
      <p className="step-description">{t('skillBuilder.addTestCase')}</p>
      
      <div className="empty-state">
        <p>{t('skillBuilder.addTestCase')}</p>
        <button className="btn btn-primary">{t('skillBuilder.newTestCase')}</button>
      </div>
    </div>
  )
}

function Step5Generate() {
  const { t } = useTranslation()
  return (
    <div className="step-content">
      <h2>Step 5: {t('skillBuilder.step5')}</h2>
      <p className="step-description">{t('skillBuilder.generateExport')}</p>
      
      <div className="card">
        <h3>{t('skillBuilder.preview')}</h3>
        <p className="text-secondary">{t('skillBuilder.previewHint')}</p>
        <ul style={{ marginTop: 12, marginLeft: 20 }}>
          <li>skill.json - Skill 元数据</li>
          <li>main.py - 入口文件</li>
          <li>README.md - 说明文档</li>
          <li>requirements.txt - 依赖列表</li>
        </ul>
      </div>

      <div style={{ marginTop: 24 }}>
        <button className="btn btn-primary" style={{ marginRight: 12 }}>
          {t('skillBuilder.generateSkill')}
        </button>
        <button className="btn">
          {t('skillBuilder.saveTemplate')}
        </button>
      </div>
    </div>
  )
}

export default SkillBuilderPage
