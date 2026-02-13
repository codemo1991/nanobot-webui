import { Component, type ReactNode } from 'react'
import { Button, Typography } from 'antd'
import i18n from '../i18n'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export class ChatErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('Chat error boundary caught:', error, errorInfo)
  }

  retry = () => {
    this.setState({ hasError: false, error: null })
  }

  render() {
    if (this.state.hasError && this.state.error) {
      return (
        <div style={{ padding: 24, textAlign: 'center' }}>
          <Typography.Title level={4}>{i18n.t('chat.errorBoundaryTitle')}</Typography.Title>
          <Typography.Paragraph type="secondary">
            {this.state.error.message || i18n.t('chat.errorBoundaryUnknown')}
          </Typography.Paragraph>
          <Button type="primary" onClick={this.retry}>
            {i18n.t('chat.errorBoundaryRetry')}
          </Button>
        </div>
      )
    }
    return this.props.children
  }
}
