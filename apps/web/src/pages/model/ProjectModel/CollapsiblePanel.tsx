/**
 * 可折叠工作台面板：标题栏可展开/收起；内容超长时限高滚动，避免占据页面过长。
 * 用于工程模型右栏「语义审查 / 待人工识别」等长内容区。
 */
import { Button, Card } from 'antd'
import { DownOutlined, UpOutlined } from '@ant-design/icons'
import { useState, type CSSProperties, type ReactNode } from 'react'

interface CollapsiblePanelProps {
  title: ReactNode
  extra?: ReactNode
  defaultOpen?: boolean
  /** 展开时内容区最大高度(px)，超出滚动 */
  maxBodyHeight?: number
  style?: CSSProperties
  children: ReactNode
}

export default function CollapsiblePanel({
  title,
  extra,
  defaultOpen = true,
  maxBodyHeight = 360,
  style,
  children,
}: CollapsiblePanelProps): JSX.Element {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <Card
      size="small"
      title={title}
      style={style}
      extra={
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          {extra}
          <Button
            type="text"
            size="small"
            icon={open ? <UpOutlined /> : <DownOutlined />}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? '收起' : '展开'}
          </Button>
        </span>
      }
      styles={open ? undefined : { body: { display: 'none' } }}
    >
      {/* 折叠时卸载子树而非仅 CSS 隐藏——释放其 DOM/组件实例内存 */}
      {open ? (
        <div style={{ maxHeight: maxBodyHeight, overflowY: 'auto', paddingRight: 2 }}>
          {children}
        </div>
      ) : null}
    </Card>
  )
}
