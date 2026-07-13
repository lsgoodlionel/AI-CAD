/**
 * 零依赖 Markdown 渲染组件(操作手册语法子集)。
 * 支持:标题、段落、粗体、行内代码、链接、有序/无序列表、引用、代码块、表格、分隔线。
 * 直接生成 React 节点(不用 dangerouslySetInnerHTML),无 XSS,无第三方依赖。
 */
import { Typography } from 'antd'
import type { ReactNode } from 'react'

const { Title, Paragraph, Text } = Typography

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[`*_~]/g, '')
    .replace(/[^\w一-龥]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)]+\))/g
  let lastIndex = 0
  let match: RegExpExecArray | null
  let i = 0
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index))
    const token = match[0]
    const key = `${keyPrefix}-i${i++}`
    if (token.startsWith('`')) {
      nodes.push(<Text key={key} code>{token.slice(1, -1)}</Text>)
    } else if (token.startsWith('**')) {
      nodes.push(<Text key={key} strong>{token.slice(2, -2)}</Text>)
    } else {
      const link = /\[([^\]]+)\]\(([^)]+)\)/.exec(token)
      if (link) {
        const [, label, href] = link
        const external = /^https?:\/\//.test(href)
        nodes.push(
          <a key={key} href={href} target={external ? '_blank' : undefined} rel={external ? 'noreferrer' : undefined}>
            {label}
          </a>,
        )
      } else {
        nodes.push(token)
      }
    }
    lastIndex = pattern.lastIndex
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex))
  return nodes
}

function splitRow(line: string): string[] {
  return line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
}

interface MarkdownProps {
  content: string
}

export default function Markdown({ content }: MarkdownProps): JSX.Element {
  const lines = content.replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  let key = 0

  while (i < lines.length) {
    const line = lines[i]

    if (line.trim().startsWith('```')) {
      const code: string[] = []
      i += 1
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        code.push(lines[i]); i += 1
      }
      i += 1
      blocks.push(<pre key={`b${key++}`} className="help-md-pre"><code>{code.join('\n')}</code></pre>)
      continue
    }
    if (line.trim() === '') { i += 1; continue }
    if (/^\s*---+\s*$/.test(line)) { blocks.push(<hr key={`b${key++}`} className="help-md-hr" />); i += 1; continue }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    if (heading) {
      const level = Math.min(heading[1].length, 4) as 1 | 2 | 3 | 4
      const raw = heading[2].trim()
      blocks.push(
        <Title key={`b${key++}`} level={level} id={slugify(raw)} className="help-md-title">
          {renderInline(raw, `h${key}`)}
        </Title>,
      )
      i += 1
      continue
    }

    if (line.includes('|') && i + 1 < lines.length &&
        /^\s*\|?[\s:-]*-[\s:|-]*\|?\s*$/.test(lines[i + 1]) && lines[i + 1].includes('|')) {
      const header = splitRow(line)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && lines[i].includes('|') && lines[i].trim() !== '') {
        rows.push(splitRow(lines[i])); i += 1
      }
      blocks.push(
        <div key={`b${key++}`} className="help-md-table-wrap">
          <table className="help-md-table">
            <thead><tr>{header.map((c, ci) => <th key={ci}>{renderInline(c, `th${key}-${ci}`)}</th>)}</tr></thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri}>{header.map((_, ci) => <td key={ci}>{renderInline(row[ci] ?? '', `td${key}-${ri}-${ci}`)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    if (/^\s*>\s?/.test(line)) {
      const quote: string[] = []
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) { quote.push(lines[i].replace(/^\s*>\s?/, '')); i += 1 }
      blocks.push(
        <blockquote key={`b${key++}`} className="help-md-quote">
          {quote.map((q, qi) => <div key={qi}>{renderInline(q, `q${key}-${qi}`)}</div>)}
        </blockquote>,
      )
      continue
    }

    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line)
      const items: ReactNode[] = []
      let li = 0
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        const itemText = lines[i].replace(/^\s*([-*]|\d+\.)\s+/, '')
        items.push(<li key={li++}>{renderInline(itemText, `li${key}-${li}`)}</li>)
        i += 1
      }
      blocks.push(
        ordered
          ? <ol key={`b${key++}`} className="help-md-list">{items}</ol>
          : <ul key={`b${key++}`} className="help-md-list">{items}</ul>,
      )
      continue
    }

    const para: string[] = []
    while (
      i < lines.length && lines[i].trim() !== '' &&
      !/^\s*(#{1,6}\s|>|[-*]\s|\d+\.\s|```|---+\s*$)/.test(lines[i]) &&
      !(lines[i].includes('|') && i + 1 < lines.length && /^\s*\|?[\s:-]*-[\s:|-]*\|?\s*$/.test(lines[i + 1]))
    ) {
      para.push(lines[i]); i += 1
    }
    if (para.length > 0) {
      blocks.push(<Paragraph key={`b${key++}`} className="help-md-p">{renderInline(para.join(' '), `p${key}`)}</Paragraph>)
      continue
    }
    blocks.push(<Paragraph key={`b${key++}`} className="help-md-p">{renderInline(line, `p${key}`)}</Paragraph>)
    i += 1
  }

  return <div className="help-md">{blocks}</div>
}
