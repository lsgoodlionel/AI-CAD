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

const CLICK_MOVE_TOLERANCE_PX = 6
const BACKGROUND_COLOR = '#f0f2f5'

export interface ModelViewerProps {
  scene: ModelScene
  focusDrawingId?: string
  disciplineFilter: string[]
  severityFilter: string[]
  /** 标记类型开关（issue/cross）；不传视为全部显示 */
  markerTypeFilter?: string[]
  isolatedFloorKey?: string | null
  resolveAssetUrl: (key: string) => Promise<string>
  onSelectDrawing: (drawing: SceneDrawing) => void
  onSelectMarker: (marker: SceneMarker) => void
}

export default function ModelViewer({
  scene,
  focusDrawingId,
  disciplineFilter,
  severityFilter,
  markerTypeFilter,
  isolatedFloorKey,
  resolveAssetUrl,
  onSelectDrawing,
  onSelectMarker,
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
  const resolveAssetUrlRef = useRef(resolveAssetUrl)
  onSelectDrawingRef.current = onSelectDrawing
  onSelectMarkerRef.current = onSelectMarker
  resolveAssetUrlRef.current = resolveAssetUrl

  // ── 可见性（filters / 楼层隔离，仅切换 visible 与透明度）──
  const filtersRef = useRef({ disciplineFilter, severityFilter, markerTypeFilter, isolatedFloorKey })
  filtersRef.current = { disciplineFilter, severityFilter, markerTypeFilter, isolatedFloorKey }

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
    graph.drawingMeshes.forEach((mesh) => {
      const data = mesh.userData as DrawingUserData
      const inFloor = !iso || data.floorKey === iso
      mesh.visible = inFloor && filters.disciplineFilter.includes(data.drawing.discipline)
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
      500,
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
      const candidates = [...graph.markerMeshes, ...graph.drawingMeshes].filter(
        (mesh) => mesh.visible,
      )
      const hit = raycaster.intersectObjects(candidates, false)[0]
      if (!hit) return
      const data = hit.object.userData as DrawingUserData | MarkerUserData
      if (data.kind === 'drawing') {
        onSelectDrawingRef.current(data.drawing)
      } else {
        onSelectMarkerRef.current(data.marker)
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

    // 默认取景（有焦点图纸时由 applyFocus 覆盖）
    controls.target.set(0, graph.totalHeight / 2, 0)
    camera.position.set(FLOOR_WIDTH * 1.2, graph.totalHeight + 10, FLOOR_DEPTH * 1.6)
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
  }, [disciplineFilter, severityFilter, markerTypeFilter, isolatedFloorKey])

  // ── 焦点图纸变化 ───────────────────────────────────────────
  useEffect(() => {
    applyFocus()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusDrawingId])

  return <div ref={containerRef} style={{ width: '100%', height: '100%', minHeight: 420 }} />
}
