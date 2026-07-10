/**
 * That Open Fragments 渲染场景（Phase A / A-06）
 *
 * 与现有 three.js 挤出/贴图场景（ModelViewer/sceneBuilder）**共存**的独立渲染主路径：
 * 从 `frag_key` 拉取后端产出的 `.frag`（程序化 IFC → Fragments），在本组件自持的
 * three.js world 中高性能渲染。容器/交互约定沿用 ModelViewer（OrbitControls + 阻尼、
 * 点击拾取容忍拖拽阈值、左上角状态徽标）。
 *
 * 暴露给并行 A-08（构件拾取/属性面板/成果标记对齐）的 seam：
 *  - props `onModelLoaded(model)`：模型就绪，A-08 可据此建立 localId 索引；
 *  - props `onItemSelected(item)`：点击拾取到构件（含 localId/itemId/point），A-08 接属性面板；
 *  - ref 句柄 `highlight / clearHighlight / focusItems / getModel`：供 A-08 外部驱动高亮与聚焦。
 *
 * 资源释放严格：模型按 id dispose、`model.object` 从场景移除，FragmentsModels 由
 * useFragmentsLoader 在卸载时整体 dispose，防止切换渲染模式时泄漏。
 */
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react'
import type { MutableRefObject } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { Alert, Spin } from 'antd'
import type { FragmentsModel, FragmentsModels } from '@thatopen/fragments'
import type { ModelScene } from '@/services/projectModel'
import { loadFragmentsModule, useFragmentsLoader } from './useFragmentsLoader'
import { alignMarkersToFragments, buildFloorPlacements } from './fragmentsMarkers'
import { SEVERITY_COLORS, disposeObjectTree } from './sceneBuilder'
import type { MarkerUserData } from './sceneBuilder'

const BACKGROUND_COLOR = '#f0f2f5'
const CLICK_MOVE_TOLERANCE_PX = 6
const HIGHLIGHT_COLOR = '#13c2c2'
/** 相机远裁剪面：真实米坐标下建筑包络可达数百米。 */
const CAMERA_FAR = 5000
/** devicePixelRatio 封顶，避免高 DPR 设备渲染成本失控。 */
const MAX_PIXEL_RATIO = 2
/** 标记球半径下限（米），防止极小 footprint 下不可见。 */
const MIN_MARKER_RADIUS = 0.05
/** 标记球半径 = max(footprint) / 该系数（与贴图模式 realScale 取值一致）。 */
const MARKER_RADIUS_DIVISOR = 90

/** 拾取结果（A-08 据此读取 IFC 属性 / 定位成果标记）。 */
export interface FragmentPickResult {
  modelId: string
  localId: number
  itemId: number
  point: { x: number; y: number; z: number }
}

/** 相机位姿快照（跨渲染模式切换时尽量保留 IFC 视角）。 */
export interface FragmentsCameraPose {
  position: [number, number, number]
  target: [number, number, number]
}

/** ref 句柄：供 A-08 外部驱动高亮/聚焦。 */
export interface FragmentsSceneHandle {
  /** 高亮一组构件（localId）。 */
  highlight: (localIds: number[]) => Promise<void>
  /** 清除全部高亮。 */
  clearHighlight: () => Promise<void>
  /** 相机聚焦到一组构件的合并包围盒。 */
  focusItems: (localIds: number[]) => Promise<void>
  /** 取当前已加载模型（未就绪为 null）。 */
  getModel: () => FragmentsModel | null
}

export interface FragmentsSceneProps {
  /** MinIO 中 `.frag` 的 key（scene.model_ifc.frag_key）。 */
  fragKey: string
  /** key → presigned URL（复用 projectModel.getModelAssetUrl）。 */
  resolveAssetUrl: (key: string) => Promise<string>
  /** 模型加载完成回调（A-08 建索引）。 */
  onModelLoaded?: (model: FragmentsModel) => void
  /** 构件点击拾取回调（A-08 接属性面板）；未命中传 null。 */
  onItemSelected?: (item: FragmentPickResult | null) => void
  /** 跨模式切换的相机位姿；组件挂载时读取、卸载时写回（best-effort）。 */
  cameraPoseRef?: MutableRefObject<FragmentsCameraPose | null>
  /** 左上角状态徽标文本（与 ModelViewer 风格一致）。 */
  statusLabel?: string
  /**
   * 成果标记叠加数据（当前单体范围的 floors + markers）；缺省不渲染标记。
   * Phase A 仅楼层级对齐：按楼层标高 + 模型 footprint 落位，构件级锚定见 Phase B。
   */
  markerScene?: Pick<ModelScene, 'floors' | 'markers'>
}

interface WorldRefs {
  renderer: THREE.WebGLRenderer
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  controls: OrbitControls
}

const badgeStyle: React.CSSProperties = {
  padding: '6px 10px',
  borderRadius: 6,
  background: 'rgba(255,255,255,0.9)',
  border: '1px solid rgba(5, 5, 5, 0.08)',
  fontSize: 12,
  color: '#595959',
}

/** 相机聚焦到 Box3（留出 1.4 倍余量），保持 OrbitControls target 一致。 */
function frameBox(world: WorldRefs, box: THREE.Box3): void {
  if (box.isEmpty()) return
  const center = new THREE.Vector3()
  const size = new THREE.Vector3()
  box.getCenter(center)
  box.getSize(size)
  const radius = Math.max(size.length() / 2, 1)
  const distance = radius * 2.4
  world.controls.target.copy(center)
  world.camera.position.set(
    center.x + distance,
    center.y + distance * 0.8,
    center.z + distance,
  )
  world.camera.near = Math.max(radius / 100, 0.05)
  world.camera.far = Math.max(distance * 8, CAMERA_FAR)
  world.camera.updateProjectionMatrix()
  world.controls.update()
}

const FragmentsScene = forwardRef<FragmentsSceneHandle, FragmentsSceneProps>(
  function FragmentsScene(
    {
      fragKey,
      resolveAssetUrl,
      onModelLoaded,
      onItemSelected,
      cameraPoseRef,
      statusLabel,
      markerScene,
    },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement | null>(null)
    const worldRef = useRef<WorldRefs | null>(null)
    const fragmentsRef = useRef<FragmentsModels | null>(null)
    const modelRef = useRef<FragmentsModel | null>(null)
    const rafRef = useRef<number>(0)
    /** 加载代际：异步返回时若代际已变则丢弃（防贴到已释放世界）。 */
    const loadGenerationRef = useRef(0)
    /** 拾取代际：快速连点时旧 raycast 后 resolve 也不覆盖新选中。 */
    const pickGenerationRef = useRef(0)
    const pointerDownRef = useRef<{ x: number; y: number } | null>(null)

    const loader = useFragmentsLoader()

    // 回调存 ref，避免重复绑定 pointer 事件
    const onItemSelectedRef = useRef(onItemSelected)
    const onModelLoadedRef = useRef(onModelLoaded)
    const resolveAssetUrlRef = useRef(resolveAssetUrl)
    const cameraPoseRefProp = useRef(cameraPoseRef)
    onItemSelectedRef.current = onItemSelected
    onModelLoadedRef.current = onModelLoaded
    resolveAssetUrlRef.current = resolveAssetUrl
    cameraPoseRefProp.current = cameraPoseRef

    const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
    const [errorMessage, setErrorMessage] = useState<string | null>(null)

    // ── seam：暴露高亮/聚焦句柄给 A-08 ──────────────────────────
    useImperativeHandle(
      ref,
      (): FragmentsSceneHandle => ({
        highlight: async (localIds: number[]) => {
          const model = modelRef.current
          const fragments = fragmentsRef.current
          if (!model || !fragments || localIds.length === 0) return
          const fragmentsModule = await loadFragmentsModule()
          await model.highlight(localIds, {
            color: new THREE.Color(HIGHLIGHT_COLOR),
            renderedFaces: fragmentsModule.RenderedFaces.TWO,
            opacity: 1,
            transparent: false,
          })
          await fragments.update(true)
        },
        clearHighlight: async () => {
          const model = modelRef.current
          const fragments = fragmentsRef.current
          if (!model || !fragments) return
          await model.resetHighlight()
          await fragments.update(true)
        },
        focusItems: async (localIds: number[]) => {
          const model = modelRef.current
          const world = worldRef.current
          if (!model || !world || localIds.length === 0) return
          const box = await model.getMergedBox(localIds)
          frameBox(world, box)
        },
        getModel: () => modelRef.current,
      }),
      [],
    )

    // ── 初始化 three world（renderer/camera/controls/光照/RAF/交互）──
    useEffect(() => {
      const container = containerRef.current
      if (!container) return undefined

      const renderer = new THREE.WebGLRenderer({ antialias: true })
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, MAX_PIXEL_RATIO))
      renderer.setSize(container.clientWidth, container.clientHeight)
      container.appendChild(renderer.domElement)

      const scene = new THREE.Scene()
      scene.background = new THREE.Color(BACKGROUND_COLOR)
      scene.add(new THREE.AmbientLight(0xffffff, 0.85))
      const directional = new THREE.DirectionalLight(0xffffff, 0.6)
      directional.position.set(20, 40, 25)
      scene.add(directional)

      const camera = new THREE.PerspectiveCamera(
        50,
        container.clientWidth / Math.max(container.clientHeight, 1),
        0.1,
        CAMERA_FAR,
      )
      camera.position.set(30, 24, 36)

      const controls = new OrbitControls(camera, renderer.domElement)
      controls.enableDamping = true

      const world: WorldRefs = { renderer, scene, camera, controls }
      worldRef.current = world

      // 相机移动 → 触发 fragments LOD/裁剪刷新（instance 就绪才有效）+
      // 持续记录相机位姿（跨渲染模式切换/卸载保留 IFC 视角，避免依赖清理顺序）
      const handleControlsChange = () => {
        void fragmentsRef.current?.update()
        const poseRef = cameraPoseRefProp.current
        if (poseRef) {
          poseRef.current = {
            position: camera.position.toArray() as [number, number, number],
            target: controls.target.toArray() as [number, number, number],
          }
        }
      }
      controls.addEventListener('change', handleControlsChange)

      const animate = () => {
        rafRef.current = requestAnimationFrame(animate)
        controls.update()
        renderer.render(scene, camera)
      }
      animate()

      const handleResize = () => {
        const width = container.clientWidth
        const height = container.clientHeight
        if (width === 0 || height === 0) return
        camera.aspect = width / height
        camera.updateProjectionMatrix()
        renderer.setSize(width, height)
      }
      window.addEventListener('resize', handleResize, { passive: true })

      // 点击拾取（区分拖拽）：pointerdown 记录，pointerup 位移小于阈值才拾取
      const handlePointerDown = (event: PointerEvent) => {
        pointerDownRef.current = { x: event.clientX, y: event.clientY }
      }
      const mouse = new THREE.Vector2()
      const handlePointerUp = (event: PointerEvent) => {
        const down = pointerDownRef.current
        pointerDownRef.current = null
        if (!down) return
        const moved = Math.hypot(event.clientX - down.x, event.clientY - down.y)
        if (moved > CLICK_MOVE_TOLERANCE_PX) return
        const model = modelRef.current
        const callback = onItemSelectedRef.current
        if (!model || !callback) return
        const rect = renderer.domElement.getBoundingClientRect()
        mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
        mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1
        pickGenerationRef.current += 1
        const pickGeneration = pickGenerationRef.current
        void model
          .raycast({ camera, mouse, dom: renderer.domElement })
          .then((hit) => {
            // 旧代际的迟到 raycast 不覆盖更新的选中
            if (pickGenerationRef.current !== pickGeneration) return
            if (!hit) {
              callback(null)
              return
            }
            callback({
              modelId: model.modelId,
              localId: hit.localId,
              itemId: hit.itemId,
              point: { x: hit.point.x, y: hit.point.y, z: hit.point.z },
            })
          })
          .catch(() => {
            // 拾取失败不影响渲染
          })
      }
      renderer.domElement.addEventListener('pointerdown', handlePointerDown, { passive: true })
      renderer.domElement.addEventListener('pointerup', handlePointerUp, { passive: true })

      return () => {
        cancelAnimationFrame(rafRef.current)
        window.removeEventListener('resize', handleResize)
        controls.removeEventListener('change', handleControlsChange)
        renderer.domElement.removeEventListener('pointerdown', handlePointerDown)
        renderer.domElement.removeEventListener('pointerup', handlePointerUp)
        controls.dispose()
        renderer.dispose()
        if (renderer.domElement.parentElement === container) {
          container.removeChild(renderer.domElement)
        }
        worldRef.current = null
      }
    }, [])

    // ── 加载 .frag（fragKey 变化时重载；严格代际防护 + 释放旧模型）──
    useEffect(() => {
      const world = worldRef.current
      if (!world || !fragKey) return undefined

      loadGenerationRef.current += 1
      const generation = loadGenerationRef.current
      const modelId = `ifc:${fragKey}`
      let cancelled = false

      setStatus('loading')
      setErrorMessage(null)

      const run = async () => {
        try {
          const url = await resolveAssetUrlRef.current(fragKey)
          const response = await fetch(url)
          if (!response.ok) {
            throw new Error(`拉取 .frag 失败（${response.status}）`)
          }
          const buffer = await response.arrayBuffer()
          if (cancelled || loadGenerationRef.current !== generation) return

          const fragments = await loader.ensure()
          fragmentsRef.current = fragments
          const model = await loader.load(buffer, modelId)
          if (cancelled || loadGenerationRef.current !== generation) {
            // 世界已切换/卸载：释放刚加载的模型，避免泄漏
            await loader.disposeModel(modelId)
            return
          }

          model.useCamera(world.camera)
          world.scene.add(model.object)
          await fragments.update(true)

          modelRef.current = model

          // 相机：优先恢复上次位姿（模式切换保留视角），否则按包围盒取景
          const pose = cameraPoseRef?.current
          if (pose) {
            world.camera.position.set(...pose.position)
            world.controls.target.set(...pose.target)
            world.controls.update()
          } else {
            frameBox(world, model.box)
          }
          await fragments.update(true)

          setStatus('ready')
          onModelLoadedRef.current?.(model)
        } catch (error: unknown) {
          if (cancelled || loadGenerationRef.current !== generation) return
          const message = error instanceof Error ? error.message : '未知错误'
          setErrorMessage(message)
          setStatus('error')
        }
      }
      void run()

      return () => {
        cancelled = true
        loadGenerationRef.current += 1
        // 相机位姿由 controls 'change' 持续写入 cameraPoseRef，此处无需再写
        // （卸载时世界可能已被 world-init 清理置空，依赖它不可靠）
        const model = modelRef.current
        modelRef.current = null
        const activeWorld = worldRef.current
        if (model && activeWorld) activeWorld.scene.remove(model.object)
        void loader.disposeModel(modelId)
      }
      // resolveAssetUrl 走 ref；loader 稳定；cameraPoseRef 稳定（父层 ref）
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [fragKey])

    // ── 成果标记叠加（A-08，楼层级对齐）──────────────────────────
    // 模型就绪后按其 world footprint + 楼层标高把 markers 落位为标记球。
    // 依赖 status（ready 时 modelRef/box 已就绪）与 markerScene（单体切换重建）。
    useEffect(() => {
      const world = worldRef.current
      const model = modelRef.current
      if (!world || !model || status !== 'ready') return undefined
      const markers = markerScene?.markers
      const floors = markerScene?.floors
      if (!markers?.length || !floors?.length) return undefined

      // 用模型 world 包围盒推导平面跨度与中心（楼层级近似锚定）
      const size = new THREE.Vector3()
      const center = new THREE.Vector3()
      model.box.getSize(size)
      model.box.getCenter(center)
      const planWidth = Math.max(size.x, 1)
      const planDepth = Math.max(size.z, 1)

      const placements = buildFloorPlacements(
        { floors },
        { planWidth, planDepth, center: [center.x, center.z] },
      )
      const { aligned } = alignMarkersToFragments(markers, placements)
      if (aligned.length === 0) return undefined

      const radius = Math.max(
        Math.max(planWidth, planDepth) / MARKER_RADIUS_DIVISOR,
        MIN_MARKER_RADIUS,
      )
      const group = new THREE.Group()
      group.name = 'fragments-markers'
      for (const item of aligned) {
        const geometry = new THREE.SphereGeometry(radius, 16, 12)
        const material = new THREE.MeshLambertMaterial({
          color: SEVERITY_COLORS[item.marker.severity] ?? SEVERITY_COLORS.info,
        })
        const sphere = new THREE.Mesh(geometry, material)
        sphere.position.set(item.position.x, item.position.y, item.position.z)
        const userData: MarkerUserData = { kind: 'marker', marker: item.marker }
        sphere.userData = userData
        group.add(sphere)
      }
      world.scene.add(group)

      return () => {
        world.scene.remove(group)
        disposeObjectTree(group)
      }
    }, [markerScene, status])

    return (
      <div style={{ position: 'relative', width: '100%', height: '100%', minHeight: 420 }}>
        <div ref={containerRef} style={{ width: '100%', height: '100%', minHeight: 420 }} />

        <div
          style={{
            position: 'absolute',
            top: 12,
            left: 12,
            display: 'flex',
            gap: 8,
            flexWrap: 'wrap',
            pointerEvents: 'none',
          }}
        >
          <div style={badgeStyle}>渲染: IFC · Fragments</div>
          {statusLabel ? <div style={badgeStyle}>{statusLabel}</div> : null}
        </div>

        {status === 'loading' ? (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              pointerEvents: 'none',
            }}
          >
            <Spin tip="加载 IFC 模型（Fragments）…" />
          </div>
        ) : null}

        {status === 'error' ? (
          <div style={{ position: 'absolute', top: 56, left: 12, right: 12 }}>
            <Alert
              type="warning"
              showIcon
              message="IFC 模型加载失败，已可切换至贴图/构件模式"
              description={errorMessage ?? undefined}
            />
          </div>
        ) : null}
      </div>
    )
  },
)

export default FragmentsScene
