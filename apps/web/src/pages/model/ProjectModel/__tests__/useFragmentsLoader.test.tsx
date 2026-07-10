/**
 * A-18 · useFragmentsLoader 生命周期回归（评审 deferred #9）
 *
 * 覆盖对象：`useFragmentsLoader.ts` 中难以在 E2E 稳定复现的**生命周期分支**。
 *
 * 环境约束（与 vitest.config.mts 一致）：environment=node，无 jsdom / testing-library。
 * 因此用 `react-dom/server` 静态渲染一个探针组件来**捕获** hook 返回的句柄
 * （`useRef`/`useCallback` 在 SSR render 阶段即可求值），随后在渲染外直接驱动
 * `ensure`/`load`/`disposeModel`——它们是对持久 ref 的闭包，脱离 dispatcher 仍可调用。
 *
 * 已测（本文件）：
 *  - 模块级 promise 缓存**拒绝后重置**（HIGH 修复回归锁）：
 *      · workerBlobUrlPromise（resolveWorkerUrl，fetch 失败）——模块级
 *      · creatingRef（FragmentsModels 构造失败）——hook 级
 *    首次失败后再次调用会**重试**，而非永久返回被拒 promise。
 *  - loadFragmentsModule 成功路径的**进程级缓存去重**（同步两次调用同一 promise）。
 *  - ensure 去重（并发只建 1 个 FragmentsModels）+ 就绪后复用。
 *  - load 透传 buffer/{modelId} 到 fragments.load。
 *  - disposeModel：ensure 前 no-op；ensure 后调用 fragments.disposeModel；吞掉 reject。
 *
 * 留给 E2E / 说明（本文件不覆盖）：
 *  - `useEffect` 卸载清理里的 `fragments.dispose()`（whole-instance）——SSR 不跑 effect，
 *    需真实渲染器执行 effect cleanup；由 A-20 的「切换渲染模式卸载 FragmentsScene」覆盖。
 *  - FragmentsScene 的 WebGL world / RAF / 加载代际防护——WebGL 依赖，见 A-20。
 */
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FragmentsLoaderHandle } from '../useFragmentsLoader'

interface MockFragmentsModel {
  modelId: string
}

interface MockLoadOptions {
  modelId: string
}

interface MockController {
  onConstruct: (workerUrl: string) => void
  loadImpl: (buffer: ArrayBuffer | Uint8Array, options: MockLoadOptions) => MockFragmentsModel
  disposeModelImpl: (modelId: string) => void
  instances: MockFragmentsInstance[]
}

interface MockFragmentsInstance {
  workerUrl: string
  load: (buffer: ArrayBuffer | Uint8Array, options: MockLoadOptions) => Promise<MockFragmentsModel>
  dispose: () => Promise<void>
  disposeModel: (modelId: string) => Promise<void>
}

// @thatopen/fragments 全 mock：构造/加载/释放行为经 controller 逐测试注入。
const mock = vi.hoisted(() => {
  const controller: MockController = {
    onConstruct: () => {},
    loadImpl: () => ({ modelId: 'model' }),
    disposeModelImpl: () => {},
    instances: [],
  }

  class FragmentsModels implements MockFragmentsInstance {
    workerUrl: string
    load: MockFragmentsInstance['load']
    dispose: MockFragmentsInstance['dispose']
    disposeModel: MockFragmentsInstance['disposeModel']

    constructor(workerUrl: string) {
      controller.onConstruct(workerUrl)
      this.workerUrl = workerUrl
      this.load = vi.fn((buffer: ArrayBuffer | Uint8Array, options: MockLoadOptions) =>
        Promise.resolve(controller.loadImpl(buffer, options)),
      )
      this.dispose = vi.fn(() => Promise.resolve())
      this.disposeModel = vi.fn((modelId: string) =>
        Promise.resolve(controller.disposeModelImpl(modelId)),
      )
      controller.instances.push(this)
    }
  }

  return { controller, FragmentsModels }
})

vi.mock('@thatopen/fragments', () => ({
  FragmentsModels: mock.FragmentsModels,
  RenderedFaces: { TWO: 2 },
}))

// ── fetch / URL.createObjectURL 桩（resolveWorkerUrl 依赖）─────────────

interface FakeResponse {
  ok: boolean
  status: number
  text: () => Promise<string>
}

const WORKER_SOURCE = 'self.onmessage = () => {}'

function okResponse(): FakeResponse {
  return { ok: true, status: 200, text: () => Promise.resolve(WORKER_SOURCE) }
}

let createObjectUrl: ReturnType<typeof vi.fn>

function stubFetch(impl: () => Promise<FakeResponse>): void {
  vi.stubGlobal('fetch', vi.fn(impl) as unknown as typeof fetch)
}

/** SSR 渲染探针，捕获 hook 句柄（不触发 effect）。 */
async function makeLoader(): Promise<{ handle: FragmentsLoaderHandle }> {
  const module = await import('../useFragmentsLoader')
  let captured: FragmentsLoaderHandle | null = null
  function Probe(): null {
    captured = module.useFragmentsLoader()
    return null
  }
  renderToStaticMarkup(createElement(Probe))
  if (!captured) throw new Error('useFragmentsLoader 未在渲染阶段返回句柄')
  return { handle: captured }
}

beforeEach(() => {
  vi.resetModules()
  mock.controller.instances.length = 0
  mock.controller.onConstruct = () => {}
  mock.controller.loadImpl = () => ({ modelId: 'model' })
  mock.controller.disposeModelImpl = () => {}

  stubFetch(() => Promise.resolve(okResponse()))
  createObjectUrl = vi.fn(() => 'blob:worker-1')
  ;(globalThis.URL as unknown as { createObjectURL: unknown }).createObjectURL = createObjectUrl
})

afterEach(() => {
  vi.unstubAllGlobals()
  delete (globalThis.URL as unknown as { createObjectURL?: unknown }).createObjectURL
})

describe('loadFragmentsModule', () => {
  it('caches the dynamic import (same promise for concurrent callers)', async () => {
    const module = await import('../useFragmentsLoader')
    const p1 = module.loadFragmentsModule()
    const p2 = module.loadFragmentsModule()
    expect(p1).toBe(p2)
    await expect(p1).resolves.toHaveProperty('FragmentsModels')
  })
})

describe('useFragmentsLoader · ensure', () => {
  it('lazily creates exactly one FragmentsModels for concurrent callers', async () => {
    const { handle } = await makeLoader()

    const [a, b] = await Promise.all([handle.ensure(), handle.ensure()])

    expect(a).toBe(b)
    expect(mock.controller.instances).toHaveLength(1)
    expect(a).toBe(mock.controller.instances[0])
  })

  it('reuses the cached instance on subsequent calls (no re-construction)', async () => {
    const { handle } = await makeLoader()

    const first = await handle.ensure()
    const second = await handle.ensure()

    expect(second).toBe(first)
    expect(mock.controller.instances).toHaveLength(1)
  })

  it('passes the locally hosted worker blob URL to FragmentsModels', async () => {
    const { handle } = await makeLoader()
    const instance = (await handle.ensure()) as unknown as MockFragmentsInstance
    expect(instance.workerUrl).toBe('blob:worker-1')
  })

  // ── HIGH 回归锁 1：hook 级 creatingRef 拒绝后重置 ──────────────
  it('resets creatingRef after a rejected construction so the next ensure retries', async () => {
    const { handle } = await makeLoader()

    let attempts = 0
    mock.controller.onConstruct = () => {
      attempts += 1
      if (attempts === 1) throw new Error('boom-first-construct')
    }

    await expect(handle.ensure()).rejects.toThrow('boom-first-construct')

    // 若被拒 promise 被永久缓存，这里会再次抛同一个错误（回归即失败）。
    const recovered = await handle.ensure()
    expect(recovered).toBe(mock.controller.instances[mock.controller.instances.length - 1])
    expect(attempts).toBe(2)
  })

  // ── HIGH 回归锁 2：模块级 workerBlobUrlPromise 拒绝后重置 ──────
  it('resets the worker blob cache after a failed fetch so IFC mode is not locked out', async () => {
    let calls = 0
    stubFetch(() => {
      calls += 1
      if (calls === 1) return Promise.resolve({ ok: false, status: 503, text: () => Promise.resolve('') })
      return Promise.resolve(okResponse())
    })

    const { handle } = await makeLoader()

    await expect(handle.ensure()).rejects.toThrow(/503/)

    // 第一次 fetch 失败必须清空 workerBlobUrlPromise；否则第二次不会再 fetch、永久锁死。
    const recovered = await handle.ensure()
    expect(recovered).toBe(mock.controller.instances[0])
    expect(calls).toBe(2)
  })
})

describe('useFragmentsLoader · load', () => {
  it('forwards the buffer and modelId to fragments.load', async () => {
    const { handle } = await makeLoader()
    const buffer = new Uint8Array([1, 2, 3])
    mock.controller.loadImpl = () => ({ modelId: 'ifc:key-1' })

    const model = await handle.load(buffer, 'ifc:key-1')

    const instance = mock.controller.instances[0]
    expect(instance.load).toHaveBeenCalledWith(buffer, { modelId: 'ifc:key-1' })
    expect(model.modelId).toBe('ifc:key-1')
  })

  it('lazily ensures the FragmentsModels when load is called first', async () => {
    const { handle } = await makeLoader()
    await handle.load(new Uint8Array([9]), 'm1')
    expect(mock.controller.instances).toHaveLength(1)
  })
})

describe('useFragmentsLoader · disposeModel', () => {
  it('is a no-op before ensure (nothing to dispose)', async () => {
    const { handle } = await makeLoader()
    await expect(handle.disposeModel('missing')).resolves.toBeUndefined()
    expect(mock.controller.instances).toHaveLength(0)
  })

  it('disposes a single model by id after ensure', async () => {
    const { handle } = await makeLoader()
    await handle.ensure()
    await handle.disposeModel('ifc:key-1')

    const instance = mock.controller.instances[0]
    expect(instance.disposeModel).toHaveBeenCalledWith('ifc:key-1')
  })

  it('swallows disposeModel rejections (model may already be released)', async () => {
    const { handle } = await makeLoader()
    await handle.ensure()
    mock.controller.disposeModelImpl = () => {
      throw new Error('already disposed')
    }
    await expect(handle.disposeModel('ifc:key-1')).resolves.toBeUndefined()
  })
})
