declare module '*.svg' {
  const content: string
  export default content
}

declare module '*.png' {
  const content: string
  export default content
}

// 全局类型扩展
interface Window {
  __CAD_ENV__?: string
}
