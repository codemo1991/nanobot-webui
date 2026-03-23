import { useState, useEffect, useRef } from 'react'
import { Input, Button, message } from 'antd'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import './LoginPage.css'

type TabType = 'login' | 'register' | 'reset'

// 获取图形验证码
function CaptchaInput({ onSend, loading }: { onSend: (code: string) => void; loading: boolean }) {
  const [code, setCode] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)

  const src = `/api/captcha?k=${refreshKey}&t=${Date.now()}`

  return (
    <div className="captcha-row">
      <Input
        className="captcha-input"
        placeholder="请输入右侧验证码"
        value={code}
        onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 4))}
        maxLength={4}
      />
      <img
        key={refreshKey}
        className="captcha-img"
        src={src}
        alt="验证码"
        onClick={() => setRefreshKey((k) => k + 1)}
        title="点击刷新"
      />
    </div>
  )
}

// 6位短信验证码输入
function SmsCodeInput({ onComplete }: { onComplete: (code: string) => void }) {
  const [code, setCode] = useState(['', '', '', '', '', ''])
  const inputsRef = useRef<(HTMLInputElement | null)[]>([])

  const focus = (i: number) => inputsRef.current[i]?.focus()

  const handleChange = (i: number, v: string) => {
    const digit = v.replace(/\D/g, '').slice(-1)
    const next = [...code]
    next[i] = digit
    setCode(next)
    if (digit && i < 5) focus(i + 1)
    if (next.every((d) => d)) onComplete(next.join(''))
  }

  const handleKeyDown = (i: number, e: React.KeyboardEvent) => {
    if (e.key === 'Backspace' && !code[i] && i > 0) {
      focus(i - 1)
    }
  }

  return (
    <div className="sms-input-row">
      {code.map((d, i) => (
        <input
          key={i}
          ref={(el) => { inputsRef.current[i] = el }}
          className="sms-digit"
          type="text"
          inputMode="numeric"
          maxLength={1}
          value={d}
          onChange={(e) => handleChange(i, e.target.value)}
          onKeyDown={(e) => handleKeyDown(i, e)}
          onFocus={(e) => e.target.select()}
        />
      ))}
    </div>
  )
}

export default function LoginPage() {
  const navigate = useNavigate()
  const [tab, setTab] = useState<TabType>('login')

  // Step1 状态
  const [phone, setPhone] = useState('')
  const [captcha, setCaptcha] = useState('')
  const [captchaKey, setCaptchaKey] = useState(0)
  const [sending, setSending] = useState(false)

  // Step2 状态
  const [step, setStep] = useState(1)
  const [smsCode, setSmsCode] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [countdown, setCountdown] = useState(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const isValidPhone = phone.length === 11 && /^1[3-9]\d{9}$/.test(phone)
  const isValidCaptcha = captcha.length === 4

  // 倒计时
  useEffect(() => {
    if (countdown > 0) {
      timerRef.current = setInterval(() => setCountdown((c) => c - 1), 1000)
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [countdown])

  const getTabLabel = (t: TabType) => ({ login: '登录', register: '注册', reset: '找回密码' }[t])

  // 发送短信验证码
  const handleSendCode = async () => {
    if (!isValidPhone || !isValidCaptcha) {
      message.warning('请填写正确的手机号和图形验证码')
      return
    }
    setSending(true)
    try {
      const action = tab === 'login' ? 'login' : tab === 'register' ? 'register' : 'reset'
      await api.sendSmsCode(phone, captcha, captchaKey, action)
      setStep(2)
      setCountdown(59)
      message.success('验证码已发送')
    } catch (e: unknown) {
      const err = e as { message?: string }
      message.error(err.message || '发送失败，请重试')
      setCaptchaKey((k) => k + 1)
    } finally {
      setSending(false)
    }
  }

  // 提交（登录/注册/重置）
  const handleSubmit = async (code: string) => {
    setSubmitting(true)
    try {
      const action = tab === 'login' ? 'login' : tab === 'register' ? 'register' : 'reset'
      await api.verifySmsCode(phone, code, action)
      message.success(`${getTabLabel(tab)}成功`)
      // 成功后跳转，可根据需求改为返回上一页或打开主界面
      navigate('/chat')
    } catch (e: unknown) {
      const err = e as { message?: string }
      message.error(err.message || '验证失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  // 重发
  const handleResend = async () => {
    if (countdown > 0) return
    setCaptchaKey((k) => k + 1)
    setCaptcha('')
    setStep(1)
  }

  // 切换 tab 时重置
  const switchTab = (t: TabType) => {
    setTab(t)
    setStep(1)
    setPhone('')
    setCaptcha('')
    setSmsCode('')
    setCountdown(0)
  }

  return (
    <div className="login-page">
      <div className="login-card">
        {/* Logo / 标题区 */}
        <div className="login-header">
          <div className="login-logo">
            <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
              <circle cx="24" cy="24" r="24" fill="#4F46E5" />
              <path d="M24 12c-6.627 0-12 5.373-12 12s5.373 12 12 12 12-5.373 12-12S30.627 12 24 12zm0 20c-4.418 0-8-3.582-8-8s3.582-8 8-8 8 3.582 8 8-3.582 8-8 8z" fill="white" />
              <circle cx="24" cy="24" r="4" fill="white" />
            </svg>
          </div>
          <h1 className="login-title">欢迎回来</h1>
          <p className="login-subtitle">使用手机号登录 Nanobot</p>
        </div>

        {/* Tab 切换 */}
        <div className="login-tabs">
          {(['login', 'register', 'reset'] as TabType[]).map((t) => (
            <button
              key={t}
              className={`login-tab ${tab === t ? 'active' : ''}`}
              onClick={() => switchTab(t)}
            >
              {getTabLabel(t)}
            </button>
          ))}
        </div>

        {/* Step 1: 手机号 + 图形验证码 */}
        {step === 1 && (
          <div className="login-form">
            <div className="form-field">
              <label>手机号</label>
              <Input
                size="large"
                placeholder="请输入手机号"
                value={phone}
                onChange={(e) => setPhone(e.target.value.replace(/\D/g, '').slice(0, 11))}
                maxLength={11}
                prefix={<span className="input-prefix">+86</span>}
              />
            </div>

            <div className="form-field">
              <label>图形验证码</label>
              <CaptchaInput
                onSend={setCaptcha}
                loading={false}
              />
            </div>

            <div className="captcha-row-login">
              <Input
                className="captcha-input-only"
                placeholder="请输入图形验证码"
                value={captcha}
                onChange={(e) => setCaptcha(e.target.value.replace(/\D/g, '').slice(0, 4))}
                maxLength={4}
                size="large"
              />
            </div>

            <Button
              type="primary"
              size="large"
              block
              loading={sending}
              disabled={!isValidPhone}
              onClick={handleSendCode}
              className="send-code-btn"
            >
              {sending ? '发送中...' : '获取验证码'}
            </Button>

            <div className="login-tip">
              {tab === 'login' && '未注册的手机号将自动创建账号'}
              {tab === 'register' && '注册即表示同意《用户协议》和《隐私政策》'}
              {tab === 'reset' && '通过手机号验证身份后重置密码'}
            </div>
          </div>
        )}

        {/* Step 2: 短信验证码 */}
        {step === 2 && (
          <div className="login-form">
            <div className="sms-header">
              <div className="sms-phone">
                <span className="sms-label">验证码已发送至</span>
                <span className="sms-number">+86 {phone.replace(/(\d{3})\d{4}(\d{4})/, '$1****$2')}</span>
              </div>
              <Button type="link" size="small" onClick={handleResend} disabled={countdown > 0}>
                {countdown > 0 ? `${countdown}s` : '重新获取'}
              </Button>
            </div>

            <div className="form-field">
              <label>短信验证码</label>
              <SmsCodeInput onComplete={handleSubmit} />
            </div>

            <div className="sms-tip">
              验证码将自动提交，请注意查看手机
            </div>

            {submitting && (
              <div className="login-loading">
                <span className="loading-spinner" />
                验证中...
              </div>
            )}
          </div>
        )}

        {/* 底部 */}
        <div className="login-footer">
          <Button type="link" size="small" onClick={() => navigate('/')}>
            返回首页
          </Button>
        </div>
      </div>
    </div>
  )
}
