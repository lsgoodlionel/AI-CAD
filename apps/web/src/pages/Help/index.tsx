/**
 * 帮助中心 / 操作手册
 * 展示仓库 docs 手册(构建前拷至 public/manual/)。普通用户仅见用户手册,
 * 集团管理员额外可切换管理员手册。
 */
import { PageContainer, ProCard } from '@ant-design/pro-components'
import { useAccess } from '@umijs/max'
import { Result, Segmented, Skeleton } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import Markdown from './Markdown'
import './help.less'

interface ManualMeta {
  key: string
  label: string
  file: string
  adminOnly: boolean
}

const MANUALS: ManualMeta[] = [
  { key: 'user', label: '用户手册', file: '/manual/user.md', adminOnly: false },
  { key: 'admin', label: '管理员手册', file: '/manual/admin.md', adminOnly: true },
]

export default function HelpPage(): JSX.Element {
  const access = useAccess()
  const isAdmin = Boolean((access as { isAdmin?: boolean }).isAdmin)

  const available = useMemo(() => MANUALS.filter((m) => !m.adminOnly || isAdmin), [isAdmin])
  const [active, setActive] = useState<string>(available[0]?.key ?? 'user')
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const current = available.find((m) => m.key === active) ?? available[0]

  useEffect(() => {
    if (!current) return
    let cancelled = false
    setLoading(true)
    setError('')
    fetch(current.file)
      .then((res) => {
        if (!res.ok) throw new Error(`加载失败（${res.status}）`)
        return res.text()
      })
      .then((text) => {
        const head = text.slice(0, 200).toLowerCase()
        if (head.includes('<!doctype html') || head.includes('<html')) {
          throw new Error('手册文件未同步（public/manual/ 缺失）')
        }
        if (!cancelled) setContent(text)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : '手册加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [current])

  // 深链定位：内容渲染完成后若 URL 带 hash，滚动到对应锚点小节
  // （渲染是异步的，延时一帧等 DOM 就绪后再查找 id）
  useEffect(() => {
    if (loading || error || !content) return

    const scrollToHash = (): void => {
      const hash = window.location.hash.replace(/^#/, '')
      if (!hash) return
      const target = document.getElementById(decodeURIComponent(hash))
      target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }

    const timer = setTimeout(scrollToHash, 100)
    // 已在 /help 页内、由 HelpTip 深链切换到另一锚点时（hash 变化但页面不重挂载）也需定位
    window.addEventListener('hashchange', scrollToHash)
    return () => {
      clearTimeout(timer)
      window.removeEventListener('hashchange', scrollToHash)
    }
  }, [loading, error, content])

  return (
    <PageContainer
      title="帮助中心"
      subTitle="平台操作手册 · 随功能迭代更新"
      extra={
        available.length > 1 ? (
          <Segmented
            value={active}
            onChange={(v) => setActive(v as string)}
            options={available.map((m) => ({ label: m.label, value: m.key }))}
          />
        ) : undefined
      }
    >
      <ProCard bordered>
        {loading ? (
          <Skeleton active paragraph={{ rows: 12 }} />
        ) : error ? (
          <Result status="warning" title="手册暂时无法加载" subTitle={error} />
        ) : (
          <Markdown content={content} />
        )}
      </ProCard>
    </PageContainer>
  )
}
