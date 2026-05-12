import { useState } from 'react'
import { history, useModel, request } from '@umijs/max'
import { Form, Input, Button, Card, message, Typography } from 'antd'
import { UserOutlined, LockOutlined, SafetyOutlined } from '@ant-design/icons'

const { Title, Text } = Typography

export default function LoginPage() {
  const [loading, setLoading] = useState(false)
  const { refresh } = useModel('@@initialState')

  const handleLogin = async (values: { username: string; password: string }) => {
    setLoading(true)
    try {
      const res = await request('/api/v1/auth/login', {
        method: 'POST',
        data: values,
        skipErrorHandler: true,
      } as any)

      localStorage.setItem('cad_token', res.access_token)
      message.success('登录成功')

      // 刷新 initialState（重新解析 token）
      await refresh()

      const redirect = new URLSearchParams(location.search).get('redirect')
      history.push(redirect ?? '/drawings')
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      message.error(typeof detail === 'string' ? detail : '用户名或密码错误')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'linear-gradient(135deg, #1677ff 0%, #0958d9 100%)',
      }}
    >
      <Card
        style={{ width: 400, borderRadius: 12, boxShadow: '0 8px 32px rgba(0,0,0,0.15)' }}
        bordered={false}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <SafetyOutlined style={{ fontSize: 40, color: '#1677ff', marginBottom: 12 }} />
          <Title level={4} style={{ margin: 0 }}>CAD 图纸深化管理平台</Title>
          <Text type="secondary" style={{ fontSize: 13 }}>请使用企业账号登录</Text>
        </div>

        <Form layout="vertical" onFinish={handleLogin} size="large">
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} placeholder="用户名" autoComplete="username" />
          </Form.Item>

          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="密码" autoComplete="current-password" />
          </Form.Item>

          <Button
            type="primary"
            htmlType="submit"
            block
            loading={loading}
            style={{ height: 44, borderRadius: 8, marginTop: 8 }}
          >
            登 录
          </Button>
        </Form>
      </Card>
    </div>
  )
}
