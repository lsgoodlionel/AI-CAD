/**
 * 工程 3D 模型查看器（three.js 轻量封装，不引入 react-three-fiber）
 * - 楼层板片堆叠 + 图纸面板网格 + 问题标记球体（构建逻辑见 sceneBuilder.ts）
 * - OrbitControls 交互；Raycaster 点击命中图纸/标记回调
 * - filters / 楼层隔离变化仅更新可见性，不重建场景（元数据存 userData）
 * - 卸载时 dispose 全部 geometry / material / texture / renderer
 */
import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { ModelScene, SceneDrawing, SceneMarker } from '@/services/projectModel'
import {
  FLOOR_DEPTH,
  FLOOR_FADED_OPACITY,
  FLOOR_OPACITY,
  FLOOR_WIDTH,
  buildHighlightOutline,
  buildSceneGraph,
  disposeObjectTree,
} from './sceneBuilder'
import type {
  BuiltSceneGraph,
  DrawingUserData,
  FloorUserData,
  MarkerUserData,
} from './sceneBuilder'
import type { ElementUserData } from './elementsBuilder'
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
    renderMode, elementFilter,
  })
  filtersRef.current = {
    disciplineFilter, severityFilter, markerTypeFilter, isolatedFloorKey,
    renderMode, elementFilter,
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
    graph.markerMeshes.forEach((mesh) => {
      const data = mesh.userData as MarkerUserData
      const inFloor = !iso || data.marker.floor_key === iso
      const typeOk = !filters.markerTypeFilter || filters.markerTypeFilter.includes(data.marker.type)
      mesh.visible = inFloor && typeOk && filters.severityFilter.includes(data.marker.severity)
    })
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

    // 简化起见持续 RAF 渲染（蓝图允许）
    const animate = () => {
      rafRef.current = requestAnimationFrame(animate)
      controls.update()
      renderer.render(threeScene, camera)
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
      const candidates = [
        ...graph.markerMeshes,
        ...graph.drawingMeshes,
        ...graph.elementMeshes,
      ].filter((mesh) => mesh.visible)
      const hit = raycaster.intersectObjects(candidates, false)[0]
      if (!hit) return
      const data = hit.object.userData as DrawingUserData | MarkerUserData | ElementUserData
      if (data.kind === 'drawing') {
        onSelectDrawingRef.current(data.drawing)
      } else if (data.kind === 'marker') {
        onSelectMarkerRef.current(data.marker)
      } else if (onSelectElementRef.current) {
        onSelectElementRef.current(data)
      }
    }
    renderer.domElement.addEventListener('pointerdown', handlePointerDown)
    renderer.domElement.addEventListener('pointerup', handlePointerUp)

    return () => {
      cancelAnimationFrame(rafRef.current)
      window.removeEventListener('resize', handleResize)
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
    const ys = [...graph.floorYByKey.values()]
    const midY = ys.length
      ? (Math.min(...ys) + Math.max(...ys)) / 2
      : graph.totalHeight / 2
    const radius = Math.max(graph.fitRadius, 16)
    controls.target.set(0, midY, 0)
    camera.position.set(radius * 1.15, midY + radius * 0.85, radius * 1.45)
    controls.update()
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
  }, [disciplineFilter, severityFilter, markerTypeFilter, isolatedFloorKey, renderMode, elementFilter])

  // ── 焦点图纸变化 ───────────────────────────────────────────
  useEffect(() => {
    applyFocus()
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
    </div>
  )
}
