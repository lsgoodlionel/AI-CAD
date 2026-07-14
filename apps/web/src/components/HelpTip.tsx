/**
 * 就地帮助气泡：标题旁的小号「?」图标，hover/click 弹出简短解释，
 * 可选深链跳转到 /help 手册对应锚点小节。
 * 用于各专业面板标题旁做「就地化」帮助，不替代 /help 整本手册。
 */
import { QuestionCircleOutlined } from '@ant-design/icons'
import { history } from '@umijs/max'
import { Popover } from 'antd'
import type { CSSProperties, ReactNode } from 'react'

export interface HelpTipProps {
  /** 气泡标题，可省略 */
  title?: string
  /** 气泡正文，建议 1~2 句中文解释 */
  content: ReactNode
  /**
   * 深链锚点（不含 #），如 '12-1-模型质量'；给出后气泡底部出现「查看手册 →」。
   * 传空字符串表示手册暂无对应小节，仅跳转到 /help 首屏。
   */
  anchor?: string
  /** 覆盖图标默认样式 */
  iconStyle?: CSSProperties
}

const DEFAULT_ICON_STYLE: CSSProperties = {
  color: 'rgba(0, 0, 0, 0.45)',
  fontSize: 13,
  marginLeft: 4,
  cursor: 'help',
}

export default function HelpTip({ title, content, anchor, iconStyle }: HelpTipProps): JSX.Element {
  const handleGoToManual = (): void => {
    history.push(anchor ? `/help#${anchor}` : '/help')
  }

  const popoverContent = (
    <div style={{ maxWidth: 280 }}>
      <div>{content}</div>
      {anchor !== undefined ? (
        <a
          onClick={handleGoToManual}
          style={{ display: 'inline-block', marginTop: 8, cursor: 'pointer' }}
        >
          查看手册 →
        </a>
      ) : null}
    </div>
  )

  return (
    <Popover title={title} content={popoverContent} trigger={['hover', 'click']}>
      <QuestionCircleOutlined style={{ ...DEFAULT_ICON_STYLE, ...iconStyle }} />
    </Popover>
  )
}
