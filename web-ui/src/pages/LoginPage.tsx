import { useState, useEffect, useRef } from 'react'
import { Input, Button, message } from 'antd'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import './LoginPage.css'

type TabType = 'login' | 'register' | 'reset'

// 6位短信验证码输入（自动聚焦、自动提交）
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
      navigate('/chat')
    } catch (e: unknown) {
      const err = e as { message?: string }
      message.error(err.message || '验证失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  // 重新获取
  const handleResend = () => {
    if (countdown > 0) return
    setCaptchaKey((k) => k + 1)
    setCaptcha('')
    setStep(1)
  }

  // 切换 tab 时重置状态
  const switchTab = (t: TabType) => {
    setTab(t)
    setStep(1)
    setPhone('')
    setCaptcha('')
    setCountdown(0)
  }

  const maskedPhone = phone.replace(/(\d{3})\d{4}(\d{4})/, '$1****$2')

  return (
    <div className="login-page">
      <div className="login-card">
        {/* Logo / 标题区 */}
        <div className="login-header">
          <div className="login-logo">
            <svg width="56" height="56" viewBox="0 0 56 56" fill="none">
              <circle cx="28" cy="28" r="28" fill="#4F46E5" />
              <path d="M28 14c-7.732 0-14 6.268-14 14s6.268 14 14 14 14-6.268 14-14-6.268-14-14-14zm0 23c-4.971 0-9-4.029-9-9s4.029-9 9-9 9 4.029 9 9-4.029 9-9 9z" fill="white" />
              <circle cx="28" cy="28" r="4.5" fill="white" />
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
                prefix={<span style={{ fontSize: 14, color: '#888', marginRight: 2 }}>+86</span>}
              />
            </div>

            <div className="form-field">
              <label>图形验证码</label>
              <div className="captcha-row">
                <Input
                  className="captcha-input"
                  placeholder="请输入图形验证码"
                  value={captcha}
                  onChange={(e) => setCaptcha(e.target.value.replace(/\D/g, '').slice(0, 4))}
                  maxLength={4}
                  size="large"
                />
                <img
                  key={captchaKey}
                  className="captcha-img"
                  src={`/api/captcha?k=${captchaKey}&t=${Date.now()}`}
                  alt="验证码"
                  onClick={() => setCaptchaKey((k) => k + 1)}
                  title="点击刷新"
                />
              </div>
            </div>

            <Button
              type="primary"
              size="large"
              block
              loading={sending}
              disabled={!isValidPhone || !isValidCaptcha}
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
                <span className="sms-number">+86 {maskedPhone}</span>
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
