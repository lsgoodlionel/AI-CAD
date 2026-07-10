import path from 'node:path'

// A-18 前端组件测试专用配置（仅覆盖本目录，隔离于对方 agent 的 package.json）。
// - 用纯对象导出（不 import 'vitest/config'），以便经由 npx 缓存里的 vitest 直接加载。
// - esbuild.tsconfigRaw 内联 JSX 配置，绕开项目 tsconfig（其 extends ./.umi/tsconfig.json
//   在此环境缺失）导致的 tsconfck 解析失败。
// - 组件测试用 react-dom/server 静态渲染（不依赖 jsdom / @testing-library）；
//   @thatopen / WebGL 全部 mock，聚焦纯逻辑与属性展示断言。
export default {
  esbuild: {
    jsx: 'automatic',
    tsconfigRaw: {
      compilerOptions: {
        jsx: 'react-jsx',
        jsxImportSource: 'react',
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '../../../../../src'),
    },
  },
  test: {
    environment: 'node',
    include: [path.resolve(__dirname, '**/*.test.{ts,tsx}')],
  },
}
