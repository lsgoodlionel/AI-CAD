/**
 * That Open Fragments 加载器生命周期管理（Phase A / A-06）
 *
 * - 每个 hook 实例懒创建 **一个** `FragmentsModels`，绑定**本站托管**的 worker
 *   （`public/thatopen/fragments-worker.mjs`，由 scripts/copy-thatopen-assets.mjs 拷贝）。
 *   不使用 `FragmentsModels.getWorker()`（它从 unpkg CDN 拉取，违反 CSP / 无外链约定）。
 * - `@thatopen/fragments` 通过 **动态 import** 拉起，避免进入首屏主包（bundle 预算）。
 * - 卸载时严格释放 `FragmentsModels`，防 WebGL/worker 泄漏。
 *
 * 版本必须与 apps/model-convert（`@thatopen/fragments@3.4.6` + `web-ifc@0.0.77`）
 * 对齐，否则读不了它产出的 `.frag`。
 */
import { useCallback, useEffect, useRef } from 'react'
import type { FragmentsModel, FragmentsModels } from '@thatopen/fragments'

/** 本站同源 worker 路径（构建期由 copy-thatopen-assets 落到 public/）。 */
const FRAGMENTS_WORKER_PATH = '/thatopen/fragments-worker.mjs'

type FragmentsModule = typeof import('@thatopen/fragments')

/** 进程级缓存：动态 import 的 fragments 模块命名空间。 */
let fragmentsModulePromise: Promise<FragmentsModule> | null = null

/** 进程级缓存：本地 worker 的 blob URL（module worker，跨实例复用）。 */
let workerBlobUrlPromise: Promise<string> | null = null

/** 懒加载 `@thatopen/fragments` 命名空间（供 FragmentsModels / RenderedFaces 等复用）。 */
export function loadFragmentsModule(): Promise<FragmentsModule> {
  if (!fragmentsModulePromise) {
    fragmentsModulePromise = import('@thatopen/fragments')
  }
  return fragmentsModulePromise
}

/**
 * 从本站拉取自包含的 fragments worker，包成 blob URL（module worker）。
 * 等价于 `FragmentsModels.getWorker()`，但来源是本地而非 unpkg。进程级缓存。
 */
async function resolveWorkerUrl(): Promise<string> {
  if (!workerBlobUrlPromise) {
    workerBlobUrlPromise = (async () => {
      const res = await fetch(FRAGMENTS_WORKER_PATH)
      if (!res.ok) {
        throw new Error(
          `Fragments worker 不可达（${res.status}）：${FRAGMENTS_WORKER_PATH}。` +
            '请确认已执行 npm run copy-thatopen-assets。',
        )
      }
      const source = await res.text()
      const blob = new Blob([source], { type: 'text/javascript' })
      return URL.createObjectURL(blob)
    })()
  }
  return workerBlobUrlPromise
}

export interface FragmentsLoaderHandle {
  /** 懒创建（仅一次）并返回本实例的 FragmentsModels。 */
  ensure: () => Promise<FragmentsModels>
  /** 加载一个 `.frag` buffer 为模型，resolve 已加载的 FragmentsModel。 */
  load: (buffer: ArrayBuffer | Uint8Array, modelId: string) => Promise<FragmentsModel>
  /** 按 id 释放单个模型（best-effort，模型可能已释放）。 */
  disposeModel: (modelId: string) => Promise<void>
}

/**
 * 管理 FragmentsModels 实例的生命周期。三维世界（相机/光照/渲染）由调用方
 * （FragmentsScene）持有，本 hook 只负责 fragments 侧的创建、加载与释放。
 */
export function useFragmentsLoader(): FragmentsLoaderHandle {
  const fragmentsRef = useRef<FragmentsModels | null>(null)
  const creatingRef = useRef<Promise<FragmentsModels> | null>(null)

  const ensure = useCallback(async () => {
    if (fragmentsRef.current) return fragmentsRef.current
    if (!creatingRef.current) {
      creatingRef.current = (async () => {
        const [workerUrl, fragmentsModule] = await Promise.all([
          resolveWorkerUrl(),
          loadFragmentsModule(),
        ])
        const instance = new fragmentsModule.FragmentsModels(workerUrl)
        fragmentsRef.current = instance
        return instance
      })()
    }
    return creatingRef.current
  }, [])

  const load = useCallback(
    async (buffer: ArrayBuffer | Uint8Array, modelId: string) => {
      const fragments = await ensure()
      return fragments.load(buffer, { modelId })
    },
    [ensure],
  )

  const disposeModel = useCallback(async (modelId: string) => {
    const fragments = fragmentsRef.current
    if (!fragments) return
    try {
      await fragments.disposeModel(modelId)
    } catch {
      // 模型可能已被整体 dispose 释放，忽略
    }
  }, [])

  useEffect(() => {
    return () => {
      const fragments = fragmentsRef.current
      fragmentsRef.current = null
      creatingRef.current = null
      // 异步释放；卸载路径 fire-and-forget（worker + GPU 资源随之回收）
      if (fragments) void fragments.dispose()
    }
  }, [])

  return { ensure, load, disposeModel }
}
