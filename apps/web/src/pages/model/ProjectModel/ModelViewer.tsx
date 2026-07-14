/**
 * 工程 3D 模型查看器（three.js 轻量封装，不引入 react-three-fiber）
 * - 楼层板片堆叠 + 图纸面板网格 + 问题标记球体（构建逻辑见 sceneBuilder.ts）
 * - OrbitControls 交互；Raycaster 点击命中图纸/标记回调
 * - filters / 楼层隔离变化仅更新可见性，不重建场景（元数据存 userData）
 * - 卸载时 dispose 全部 geometry / material / texture / renderer
 */
import { useEffect, useRef } from 'react'
import { Button, Tooltip } from 'antd'
import {
  ArrowDownOutlined,
  ArrowLeftOutlined,
  ArrowRightOutlined,
  ArrowUpOutlined,
  MinusOutlined,
  PlusOutlined,
  RedoOutlined,
  ReloadOutlined,
  UndoOutlined,
} from '@ant-design/icons'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { ModelScene, SceneDrawing, SceneMarker } from '@/services/projectModel'
import {
  FLOOR_DEPTH,
  FLOOR_FADED_OPACITY,
  FLOOR_OPACITY,
  FLOOR_WIDTH,
  applyMarkerVisibility,
  buildHighlightOutline,
  buildSceneGraph,
  disposeObjectTree,
} from './sceneBuilder'
import type {
  BuiltSceneGraph,
  DrawingUserData,
  FloorUserData,
} from './sceneBuilder'
import type { ElementUserData } from './elementsBuilder'
import { resolveEquipmentPick } from './elementsBuilder'
import type { ModelLodMode } from './types'

const CLICK_MOVE_TOLERANCE_PX = 6
const BACKGROUND_COLOR = '#f0f2f5'

export type RenderMode = 'elements' | 'texture' | 'mixed'

export interface ModelViewerProps {
  scene: ModelScene
  focusDrawingId?: string
  disciplineFilter: string[]
  severityFilter: string[]
  /** 标记类型开关（issue/cross）；不传视为全部显示 */
  markerTypeFilter?: string[]
  isolatedFloorKey?: string | null
  /** V2 渲染模式（schema_version=2 生效）：构件 / 贴图 / 混合，缺省混合 */
  renderMode?: RenderMode
  /** V2 构件图层：['columns','walls','beams','slabs','pipes:给排水',...,'equipment']；不传全显 */
  elementFilter?: string[]
  /** 楼层板片（蓝色半透明堆叠体）显隐；缺省 true */
  showFloorBoards?: boolean
  resolveAssetUrl: (key: string) => Promise<string>
  onSelectDrawing: (drawing: SceneDrawing) => void
  onSelectMarker: (marker: SceneMarker) => void
  /** V2 构件点击回调（合批网格返回类别级元数据，设备含 label） */
  onSelectElement?: (element: ElementUserData) => void
  lodMode?: ModelLodMode
  lodLabel?: string
  buildingLabel?: string
  pendingAnnotationCount?: number
}

export default function ModelViewer({
  scene,
  focusDrawingId,
  disciplineFilter,
  severityFilter,
  markerTypeFilter,
  isolatedFloorKey,
  renderMode = 'mixed',
  elementFilter,
  showFloorBoards = true,
  resolveAssetUrl,
  onSelectDrawing,
  onSelectMarker,
  onSelectElement,
  lodMode = 'review_skeleton',
  lodLabel = '审图骨架',
  buildingLabel,
  pendingAnnotationCount,
}: ModelViewerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const threeSceneRef = useRef<THREE.Scene | null>(null)
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null)
  const controlsRef = useRef<OrbitControls | null>(null)
  const graphRef = useRef<BuiltSceneGraph | null>(null)
  const highlightRef = useRef<THREE.LineSegments | null>(null)
  const rafRef = useRef<number>(0)
  /** 按需渲染：交互/场景变化时才画一帧，空闲不占 CPU/GPU、不产生 RAF 垃圾 */
  const requestRenderRef = useRef<() => void>(() => {})
  /** 场景代际号：贴图异步返回时若代际已变则丢弃，防止贴到已释放的材质上 */
  const buildGenerationRef = useRef(0)
  const pointerDownRef = useRef<{ x: number; y: number } | null>(null)

  // 回调与过滤器存入 ref，避免 pointer 事件重复绑定
  const onSelectDrawingRef = useRef(onSelectDrawing)
  const onSelectMarkerRef = useRef(onSelectMarker)
  const onSelectElementRef = useRef(onSelectElement)
  const resolveAssetUrlRef = useRef(resolveAssetUrl)
  onSelectDrawingRef.current = onSelectDrawing
  onSelectMarkerRef.current = onSelectMarker
  onSelectElementRef.current = onSelectElement
  resolveAssetUrlRef.current = resolveAssetUrl

  // ── 可见性（filters / 楼层隔离 / 渲染模式，仅切换 visible 与透明度）──
  const filtersRef = useRef({
    disciplineFilter, severityFilter, markerTypeFilter, isolatedFloorKey,
    renderMode, elementFilter, showFloorBoards,
  })
  filtersRef.current = {
    disciplineFilter, severityFilter, markerTypeFilter, isolatedFloorKey,
    renderMode, elementFilter, showFloorBoards,
  }

  const applyVisibility = () => {
    const graph = graphRef.current
    if (!graph) return
    const filters = filtersRef.current
    const iso = filters.isolatedFloorKey ?? null

    graph.floorMeshes.forEach((mesh) => {
      const data = mesh.userData as FloorUserData
      const material = mesh.material as THREE.MeshLambertMaterial
      material.opacity = iso && data.floorKey !== iso ? FLOOR_FADED_OPACITY : FLOOR_OPACITY
      mesh.visible = filters.showFloorBoards
    })
    // 有构件的楼层集合：构件模式下这些层隐藏贴图面板（无构件层保留贴图回退）
    const elementFloorKeys = new Set(
      graph.elementMeshes.map((mesh) => (mesh.userData as ElementUserData).floorKey),
    )
    graph.drawingMeshes.forEach((mesh) => {
      const data = mesh.userData as DrawingUserData
      const inFloor = !iso || data.floorKey === iso
      const modeOk =
        filters.renderMode !== 'elements' || !elementFloorKeys.has(data.floorKey)
      mesh.visible =
        inFloor && modeOk && filters.disciplineFilter.includes(data.drawing.discipline)
    })
    graph.elementMeshes.forEach((mesh) => {
      const data = mesh.userData as ElementUserData
      const inFloor = !iso || data.floorKey === iso
      const modeOk = filters.renderMode !== 'texture'
      const filterOk =
        !filters.elementFilter || filters.elementFilter.includes(data.elementType)
      mesh.visible = inFloor && modeOk && filterOk
    })
    if (graph.markerInstances) {
      applyMarkerVisibility(graph.markerInstances, (marker) => {
        const inFloor = !iso || marker.floor_key === iso
        const typeOk =
          !filters.markerTypeFilter || filters.markerTypeFilter.includes(marker.type)
        return inFloor && typeOk && filters.severityFilter.includes(marker.severity)
      })
    }
    requestRenderRef.current() // 可见性变化不移动相机，需显式请求一帧
  }

  // ── 焦点图纸：相机对准 + 高亮描边 ──────────────────────────
  const clearHighlight = () => {
    const highlight = highlightRef.current
    if (!highlight) return
    highlight.parent?.remove(highlight)
    highlight.geometry.dispose()
    ;(highlight.material as THREE.Material).dispose()
    highlightRef.current = null
  }

  const applyFocus = () => {
    const graph = graphRef.current
    const controls = controlsRef.current
    const camera = cameraRef.current
    clearHighlight()
    if (!graph || !controls || !camera || !focusDrawingId) return
    const target = graph.drawingMeshes.find(
      (mesh) => (mesh.userData as DrawingUserData).drawing.drawing_id === focusDrawingId,
    )
    if (!target) return
    const position = new THREE.Vector3()
    target.getWorldPosition(position)
    controls.target.copy(position)
    camera.position.set(position.x + 7, position.y + 6, position.z + 9)
    controls.update()
    const outline = buildHighlightOutline(target)
    target.add(outline)
    highlightRef.current = outline
    requestRenderRef.current()
  }

  // ── 默认取景（scene 变化与「复位」按钮共用）──────────────────
  const frameDefault = () => {
    const graph = graphRef.current
    const controls = controlsRef.current
    const camera = cameraRef.current
    if (!graph || !controls || !camera) return
    const ys = [...graph.floorYByKey.values()]
    const midY = ys.length
      ? (Math.min(...ys) + Math.max(...ys)) / 2
      : graph.totalHeight / 2
    const radius = Math.max(graph.fitRadius, 16)
    controls.target.set(0, midY, 0)
    camera.position.set(radius * 1.15, midY + radius * 0.85, radius * 1.45)
    controls.update()
    requestRenderRef.current()
  }

  // ── 按钮控制：环绕旋转 / 推拉缩放 / 屏幕平移（鼠标之外的显式操作）──
  const orbit = (deltaAzimuth: number, deltaPolar: number) => {
    const camera = cameraRef.current
    const controls = controlsRef.current
    if (!camera || !controls) return
    const offset = camera.position.clone().sub(controls.target)
    const spherical = new THREE.Spherical().setFromVector3(offset)
    spherical.theta += deltaAzimuth
    spherical.phi = Math.max(0.1, Math.min(Math.PI - 0.1, spherical.phi + deltaPolar))
    offset.setFromSpherical(spherical)
    camera.position.copy(controls.target).add(offset)
    controls.update()
  }
  const dolly = (factor: number) => {
    const camera = cameraRef.current
    const controls = controlsRef.current
    if (!camera || !controls) return
    const offset = camera.position.clone().sub(controls.target).multiplyScalar(factor)
    camera.position.copy(controls.target).add(offset)
    controls.update()
  }
  const pan = (dxFrac: number, dyFrac: number) => {
    const camera = cameraRef.current
    const controls = controlsRef.current
    if (!camera || !controls) return
    const distance = camera.position.distanceTo(controls.target)
    const scale = distance * 0.18
    const right = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 0)
    const up = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 1)
    const move = right.multiplyScalar(dxFrac * scale).add(up.multiplyScalar(dyFrac * scale))
    camera.position.add(move)
    controls.target.add(move)
    controls.update()
  }

  // ── 初始化 renderer / camera / controls / 灯光 / RAF / resize ──
  useEffect(() => {
    const container = containerRef.current
    if (!container) return undefined

    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(window.devicePixelRatio)
    renderer.setSize(container.clientWidth, container.clientHeight)
    container.appendChild(renderer.domElement)

    const threeScene = new THREE.Scene()
    threeScene.background = new THREE.Color(BACKGROUND_COLOR)
    threeScene.add(new THREE.AmbientLight(0xffffff, 0.85))
    const directional = new THREE.DirectionalLight(0xffffff, 0.6)
    directional.position.set(20, 40, 25)
    threeScene.add(directional)

    const camera = new THREE.PerspectiveCamera(
      50,
      container.clientWidth / Math.max(container.clientHeight, 1),
      0.1,
      5000, // 真实米坐标模式下建筑包络可达数百米
    )
    camera.position.set(FLOOR_WIDTH * 1.2, 16, FLOOR_DEPTH * 1.6)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true

    rendererRef.current = renderer
    threeSceneRef.current = threeScene
    cameraRef.current = camera
    controlsRef.current = controls

    // ── 按需渲染：仅在需要时排队一帧 ─────────────────────────────
    // controls.update() 在 damping 未收敛时会再次派发 'change' → 自动续帧，
    // 收敛后停止；用户拖拽/滚轮同样经 'change' 触发。空闲时零帧。
    let renderQueued = false
    const requestRender = () => {
      if (renderQueued) return
      renderQueued = true
      rafRef.current = requestAnimationFrame(() => {
        renderQueued = false
        controls.update()
        renderer.render(threeScene, camera)
      })
    }
    requestRenderRef.current = requestRender
    controls.addEventListener('change', requestRender)
    requestRender() // 首帧

    const handleResize = () => {
      const width = container.clientWidth
      const height = container.clientHeight
      if (width === 0 || height === 0) return
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      renderer.setSize(width, height)
      requestRender()
    }
    window.addEventListener('resize', handleResize)

    // 点击（区分拖拽）：pointerdown 记录位置，pointerup 位移小于阈值才拾取
    const handlePointerDown = (event: PointerEvent) => {
      pointerDownRef.current = { x: event.clientX, y: event.clientY }
    }
    const raycaster = new THREE.Raycaster()
    const pointerNdc = new THREE.Vector2()
    const handlePointerUp = (event: PointerEvent) => {
      const down = pointerDownRef.current
      pointerDownRef.current = null
      if (!down) return
      const moved = Math.hypot(event.clientX - down.x, event.clientY - down.y)
      if (moved > CLICK_MOVE_TOLERANCE_PX) return
      const graph = graphRef.current
      if (!graph) return
      const rect = renderer.domElement.getBoundingClientRect()
      pointerNdc.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
      pointerNdc.y = -((event.clientY - rect.top) / rect.height) * 2 + 1
      raycaster.setFromCamera(pointerNdc, camera)
      const candidates: THREE.Object3D[] = [
        ...graph.drawingMeshes,
        ...graph.elementMeshes,
      ].filter((mesh) => mesh.visible)
      // 标记为合批 InstancedMesh：隐藏实例已置零缩放，不会命中
      if (graph.markerInstances) candidates.push(graph.markerInstances.mesh)
      const hit = raycaster.intersectObjects(candidates, false)[0]
      if (!hit) return

      // 标记命中：instanceId 反查
      if (graph.markerInstances && hit.object === graph.markerInstances.mesh) {
        const marker = graph.markerInstances.markers[hit.instanceId ?? -1]
        if (marker) onSelectMarkerRef.current(marker)
        return
      }

      const data = hit.object.userData as DrawingUserData | ElementUserData
      if (data.kind === 'drawing') {
        onSelectDrawingRef.current(data.drawing)
      } else if (onSelectElementRef.current) {
        // 设备合批：faceIndex 反查具体设备的 label / 来源图纸
        if (data.elementType === 'equipment' && data.equipmentPicks && hit.faceIndex != null) {
          const pick = resolveEquipmentPick(data.equipmentPicks, hit.faceIndex)
          onSelectElementRef.current({ ...data, count: 1, label: pick?.label, src: pick?.src })
        } else {
          onSelectElementRef.current(data)
        }
      }
    }
    renderer.domElement.addEventListener('pointerdown', handlePointerDown)
    renderer.domElement.addEventListener('pointerup', handlePointerUp)

    return () => {
      cancelAnimationFrame(rafRef.current)
      requestRenderRef.current = () => {}
      window.removeEventListener('resize', handleResize)
      controls.removeEventListener('change', requestRender)
      renderer.domElement.removeEventListener('pointerdown', handlePointerDown)
      renderer.domElement.removeEventListener('pointerup', handlePointerUp)
      controls.dispose()
      clearHighlight()
      if (graphRef.current) {
        threeScene.remove(graphRef.current.root)
        disposeObjectTree(graphRef.current.root)
        graphRef.current = null
      }
      renderer.dispose()
      if (renderer.domElement.parentElement === container) {
        container.removeChild(renderer.domElement)
      }
      rendererRef.current = null
      threeSceneRef.current = null
      cameraRef.current = null
      controlsRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── scene 变化：重建对象树 + 异步贴图 + 相机取景/焦点 ──────
  useEffect(() => {
    const threeScene = threeSceneRef.current
    const controls = controlsRef.current
    const camera = cameraRef.current
    if (!threeScene || !controls || !camera) return undefined

    buildGenerationRef.current += 1
    const generation = buildGenerationRef.current

    const graph = buildSceneGraph(scene)
    graphRef.current = graph
    threeScene.add(graph.root)
    applyVisibility()

    // 默认取景（有焦点图纸时由 applyFocus 覆盖）：按取景半径与楼层跨度适配
    frameDefault()
    applyFocus()

    // 有 image_key 的图纸面板：换取 presigned URL 后异步贴图
    const textureLoader = new THREE.TextureLoader()
    graph.drawingMeshes.forEach((mesh) => {
      const data = mesh.userData as DrawingUserData
      if (data.drawing.image_key === '') return
      resolveAssetUrlRef
        .current(data.drawing.image_key)
        .then((url) => textureLoader.loadAsync(url))
        .then((texture) => {
          if (buildGenerationRef.current !== generation) {
            // 场景已重建/卸载，丢弃并释放贴图
            texture.dispose()
            return
          }
          texture.colorSpace = THREE.SRGBColorSpace
          const material = mesh.material as THREE.MeshBasicMaterial
          material.map = texture
          material.color.set('#ffffff')
          material.opacity = 1
          material.needsUpdate = true
          requestRenderRef.current() // 贴图异步返回，请求重绘
        })
        .catch(() => {
          // 贴图失败降级为线框占位，不影响整体渲染
        })
    })

    return () => {
      buildGenerationRef.current += 1
      clearHighlight()
      threeScene.remove(graph.root)
      disposeObjectTree(graph.root)
      if (graphRef.current === graph) graphRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene])

  // ── filters / 楼层隔离：仅更新可见性 ────────────────────────
  useEffect(() => {
    applyVisibility()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [disciplineFilter, severityFilter, markerTypeFilter, isolatedFloorKey, renderMode, elementFilter, showFloorBoards])

  // ── 焦点图纸变化 ───────────────────────────────────────────
  useEffect(() => {
    applyFocus()
    requestRenderRef.current() // 清除高亮的提前返回分支也需重绘
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusDrawingId])

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
        <div
          style={{
            padding: '6px 10px',
            borderRadius: 6,
            background: 'rgba(255,255,255,0.9)',
            border: '1px solid rgba(5, 5, 5, 0.08)',
            fontSize: 12,
            color: '#595959',
          }}
        >
          LOD: {lodLabel}
          {lodMode === 'realistic_proxy' ? '（近似）' : ''}
        </div>
        <div
          style={{
            padding: '6px 10px',
            borderRadius: 6,
            background: 'rgba(255,255,255,0.9)',
            border: '1px solid rgba(5, 5, 5, 0.08)',
            fontSize: 12,
            color: '#595959',
          }}
        >
          单体: {buildingLabel ?? '总体'}
        </div>
        {typeof pendingAnnotationCount === 'number' ? (
          <div
            style={{
              padding: '6px 10px',
              borderRadius: 6,
              background: 'rgba(255,255,255,0.9)',
              border: '1px solid rgba(5, 5, 5, 0.08)',
              fontSize: 12,
              color: '#595959',
            }}
          >
            待人工识别: {pendingAnnotationCount}
          </div>
        ) : null}
      </div>

      {/* 视角控制：鼠标之外的显式按钮（旋转 / 平移 / 缩放 / 复位）*/}
      <div
        style={{
          position: 'absolute',
          right: 12,
          bottom: 12,
          padding: 8,
          borderRadius: 8,
          background: 'rgba(255,255,255,0.92)',
          border: '1px solid rgba(5,5,5,0.1)',
          boxShadow: '0 2px 8px rgba(0,0,0,0.12)',
          display: 'flex',
          gap: 10,
          alignItems: 'flex-start',
        }}
      >
        <ControlPad
          label="旋转"
          up={<ArrowUpOutlined />}
          down={<ArrowDownOutlined />}
          left={<UndoOutlined />}
          right={<RedoOutlined />}
          center={<Tooltip title="复位视角"><ReloadOutlined /></Tooltip>}
          onUp={() => orbit(0, -ORBIT_STEP)}
          onDown={() => orbit(0, ORBIT_STEP)}
          onLeft={() => orbit(-ORBIT_STEP, 0)}
          onRight={() => orbit(ORBIT_STEP, 0)}
          onCenter={frameDefault}
        />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: '#8c8c8c' }}>缩放</span>
          <Tooltip title="放大"><Button size="small" icon={<PlusOutlined />} onClick={() => dolly(0.8)} /></Tooltip>
          <Tooltip title="缩小"><Button size="small" icon={<MinusOutlined />} onClick={() => dolly(1.25)} /></Tooltip>
        </div>
        <ControlPad
          label="平移"
          up={<ArrowUpOutlined />}
          down={<ArrowDownOutlined />}
          left={<ArrowLeftOutlined />}
          right={<ArrowRightOutlined />}
          onUp={() => pan(0, PAN_STEP)}
          onDown={() => pan(0, -PAN_STEP)}
          onLeft={() => pan(-PAN_STEP, 0)}
          onRight={() => pan(PAN_STEP, 0)}
        />
      </div>
    </div>
  )
}

const ORBIT_STEP = 0.26
const PAN_STEP = 0.5

interface ControlPadProps {
  label: string
  up: JSX.Element
  down: JSX.Element
  left: JSX.Element
  right: JSX.Element
  center?: JSX.Element
  onUp: () => void
  onDown: () => void
  onLeft: () => void
  onRight: () => void
  onCenter?: () => void
}

function ControlPad(props: ControlPadProps): JSX.Element {
  const cell: React.CSSProperties = { width: 28, height: 28 }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'center' }}>
      <span style={{ fontSize: 11, color: '#8c8c8c' }}>{props.label}</span>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 28px)', gap: 4 }}>
        <span />
        <Button size="small" style={cell} icon={props.up} onClick={props.onUp} />
        <span />
        <Button size="small" style={cell} icon={props.left} onClick={props.onLeft} />
        {props.center ? (
          <Button size="small" style={cell} icon={props.center} onClick={props.onCenter} />
        ) : (
          <span />
        )}
        <Button size="small" style={cell} icon={props.right} onClick={props.onRight} />
        <span />
        <Button size="small" style={cell} icon={props.down} onClick={props.onDown} />
        <span />
      </div>
    </div>
  )
}
