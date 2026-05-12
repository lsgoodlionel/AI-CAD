import { Button, Result } from 'antd'
import { history } from '@umijs/max'

export default function NotFound() {
  return (
    <Result
      status="404"
      title="404"
      subTitle="页面不存在或您暂无访问权限"
      extra={
        <Button type="primary" onClick={() => history.push('/drawings')}>
          返回图纸列表
        </Button>
      }
    />
  )
}
