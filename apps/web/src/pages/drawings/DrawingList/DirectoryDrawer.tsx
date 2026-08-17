/**
 * 图纸目录树 —— 按上传的图纸目录展开整套图,点图名直接跳到该图纸。
 *
 * 目录来自图纸目录本身(那几十张目录图),表格已被 OCR 进档案层,按图名列取序。
 *
 * **诚实标注覆盖**:目录图 OCR 稀疏,实测只关联上约 189/2311 张。未被目录覆盖的
 * 图纸数在树顶显式写出,而不是让人误以为这棵树就是全部图纸。
 */
import { useEffect, useState } from 'react'
import { useNavigate } from '@umijs/max'
import { Alert, Button, Drawer, Empty, Spin, Tag, Tree, Typography, message } from 'antd'
import { UnorderedListOutlined } from '@ant-design/icons'
import { getDirectoryTree, type DirectorySheet } from '@/services/drawings'

const { Text } = Typography

interface DirectoryDrawerProps {
  projectId?: string
}

/** 目录树节点 key 形如 `d:<drawing_id>`,点击时解析出图纸 id */
const DRAWING_PREFIX = 'd:'

export default function DirectoryDrawer({ projectId }: DirectoryDrawerProps) {
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [sheets, setSheets] = useState<DirectorySheet[]>([])
  const [unlisted, setUnlisted] = useState(0)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open || !projectId) return
    setLoading(true)
    getDirectoryTree(projectId)
      .then((res) => { setSheets(res.sheets); setUnlisted(res.unlisted_count) })
      .catch(() => message.error('图纸目录加载失败'))
      .finally(() => setLoading(false))
  }, [open, projectId])

  const treeData = sheets.map((s) => ({
    key: `${DRAWING_PREFIX}${s.id}`,
    title: (
      <span>
        {s.title}
        {s.children.length ? (
          <Tag style={{ marginLeft: 6 }}>{s.children.length}</Tag>
        ) : (
          <Text type="secondary" style={{ marginLeft: 6, fontSize: 12 }}>未识别出条目</Text>
        )}
      </span>
    ),
    children: s.children.map((c) => ({
      key: `${DRAWING_PREFIX}${c.id}`,
      title: c.title || c.drawing_no,
      isLeaf: true,
    })),
  }))

  return (
    <>
      <Button icon={<UnorderedListOutlined />} onClick={() => setOpen(true)}>
        图纸目录
      </Button>
      <Drawer
        title="图纸目录"
        placement="right"
        width={460}
        open={open}
        onClose={() => setOpen(false)}
      >
        {!projectId ? (
          <Alert type="info" showIcon message="请先在上方搜索栏选择所属项目" />
        ) : loading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
        ) : sheets.length === 0 ? (
          <Empty description="未找到图纸目录" />
        ) : (
          <>
            <Alert
              type="info" showIcon style={{ marginBottom: 12 }}
              message={`目录图 ${sheets.length} 张;另有 ${unlisted} 张未被目录覆盖`}
              description="目录图的 OCR 是稀疏的,只有能对上图名的条目才会挂在树上。未覆盖的图纸仍在列表中,按专业 + 图号自然序排在目录之后。"
            />
            <Tree
              treeData={treeData}
              onSelect={(keys) => {
                const key = String(keys[0] ?? '')
                if (key.startsWith(DRAWING_PREFIX)) {
                  navigate(`/drawings/${key.slice(DRAWING_PREFIX.length)}`)
                  setOpen(false)
                }
              }}
            />
          </>
        )}
      </Drawer>
    </>
  )
}
