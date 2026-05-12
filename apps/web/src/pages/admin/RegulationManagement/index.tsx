/**
 * 规范知识库管理
 * 三个 Tab：规范文件管理 / 外部 API 接入 / 规范搜索
 */
import { useState } from 'react'
import { PageContainer } from '@ant-design/pro-components'
import { Tabs } from 'antd'
import BookList from './BookList'
import ApiSourceList from './ApiSourceList'
import RegulationSearch from './RegulationSearch'

export default function RegulationManagement() {
  const [activeTab, setActiveTab] = useState('books')

  return (
    <PageContainer title="规范知识库管理">
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          { key: 'books',   label: '规范文件',   children: <BookList /> },
          { key: 'sources', label: '外部 API 接入', children: <ApiSourceList /> },
          { key: 'search',  label: '规范搜索',   children: <RegulationSearch /> },
        ]}
      />
    </PageContainer>
  )
}
