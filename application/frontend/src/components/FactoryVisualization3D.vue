<template>
  <div class="factory-3d-container">
    <div class="hud-header">
      <slot name="header"></slot>
    </div>
    <div ref="containerRef" class="three-wrapper"></div>
    <!-- 屏幕空间标签 -->
    <div class="labels-overlay" ref="labelsRef">
      <div
        v-for="l in screenLabels"
        :key="l.id"
        class="world-label"
        :style="{ left: l.x + 'px', top: l.y + 'px', color: l.color }"
      >{{ l.text }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { useFactoryStore } from '@/stores/factory'
import {
  BG_COLOR, GROUND_COLOR, GRID_COLOR_MAIN, GRID_COLOR_SUB,
  MACHINE_COLORS, STATUS_LIGHT,
  AGV_ACTIVE_COLOR, AGV_IDLE_COLOR,
  AGV_LIGHT_ACTIVE, AGV_LIGHT_IDLE,
  ZONE_COLORS,
  EDGE_COLOR, WAYPOINT_DOCK_COLOR, WAYPOINT_DEFAULT_COLOR,
  HIGHLIGHT_COLOR, HIGHLIGHT_COLLISION_COLOR,
  BACKGROUND_THEMES,
  createBollard, createRack, createCabinet, createSafetyBarrier,
  createPerimeterWall, getDecorationLayout,
  createMachine, createAGV,
  createFactoryBackground,
} from '@/utils/assets'

// ==================== Props ====================
const props = defineProps({
  staticConfig: { type: Object, required: false, default: () => ({ machines: {}, zones: [], waypoints: {} }) },
  baseGridSize: { type: Number, default: 40 },
  defaultGridWidth: { type: Number, default: 20 },
  defaultGridHeight: { type: Number, default: 14 },
  editMode: { type: Boolean, default: false },
  backgroundTheme: { type: String, default: 'clean' },
  backgroundSize: { type: Number, default: 2 },
})

// ==================== Emits ====================
const emit = defineEmits(['asset-placed', 'asset-moved'])

// ==================== Store ====================
const store = useFactoryStore()

// ==================== Refs ====================
const containerRef = ref(null)
const labelsRef = ref(null)
const screenLabels = ref([])

// ==================== Topology ====================
const topology = computed(() => {
  const config = props.staticConfig || {}
  const topoData = config.topology || config
  return {
    zones: topoData.zones || [],
    machines: topoData.machines || {},
    waypoints: topoData.waypoints || {},
    edges: topoData.edges || [],
    gridWidth: topoData.gridWidth || props.defaultGridWidth,
    gridHeight: topoData.gridHeight || props.defaultGridHeight,
  }
})

// ==================== 坐标工具 ====================
const GRID_SIZE = 1 // 每个格子 = 1 世界单位

function gridToWorld(gx, gy) {
  const w = topology.value.gridWidth
  const h = topology.value.gridHeight
  return new THREE.Vector3((gx - w / 2 + 0.5) * GRID_SIZE, 0, (gy - h / 2 + 0.5) * GRID_SIZE)
}

// ==================== Three.js 对象 ====================
let scene, camera, renderer, controls, clock
let groundPlane, gridHelper, backgroundGroup
let ambientLight, sunLight
let machineGroup, agvGroup, zoneGroup, waypointGroup, edgeGroup, decorGroup

// 对象映射
const machineMeshMap = new Map()   // machineId → { group, bodyMat, lightMat }
const agvDataList = []             // { group, chassisMat, lightMat, targetPos: Vector3 }
const waypointPosMap = new Map()   // waypointId → { x, z } (world)

// 动画状态
let waitTimeLeft = 0
const STEP_WAIT_TIME = 800

// ==================== 编辑模式交互 ====================
const raycaster = new THREE.Raycaster()
const mouse = new THREE.Vector2()
let highlightMesh = null
let draggingAsset = null        // { type, id, group }
let isDragging = false
let dragStartMouseWorld = null
let dragStartGridX = 0
let dragStartGridY = 0
let lastDragGridX = 0           // 拖拽过程中最新的网格 X
let lastDragGridY = 0           // 拖拽过程中最新的网格 Y

function snapToGrid(worldPos) {
  const w = topology.value.gridWidth
  const h = topology.value.gridHeight
  const gx = Math.round(worldPos.x / GRID_SIZE + w / 2 - 0.5)
  const gy = Math.round(worldPos.z / GRID_SIZE + h / 2 - 0.5)
  return {
    gx: Math.max(0, Math.min(gx, w - 1)),
    gy: Math.max(0, Math.min(gy, h - 1)),
  }
}

function createHighlightMesh() {
  const geom = new THREE.BoxGeometry(GRID_SIZE * 1.1, 0.08, GRID_SIZE * 1.1)
  const mat = new THREE.MeshBasicMaterial({
    color: HIGHLIGHT_COLOR,
    transparent: true,
    opacity: 0.35,
    depthWrite: false,
  })
  highlightMesh = new THREE.Mesh(geom, mat)
  highlightMesh.visible = false
  highlightMesh.renderOrder = 999
  return highlightMesh
}

function setHighlightColor(occupied) {
  if (!highlightMesh) return
  highlightMesh.material.color.set(occupied ? HIGHLIGHT_COLLISION_COLOR : HIGHLIGHT_COLOR)
}

function updateHighlight(worldPos, isOccupied = false) {
  if (!highlightMesh) createHighlightMesh()
  if (!scene) return

  if (!scene.getObjectById(highlightMesh.id)) {
    scene.add(highlightMesh)
  }

  const { gx, gy } = snapToGrid(worldPos)
  const snapped = gridToWorld(gx, gy)
  highlightMesh.position.set(snapped.x, 0.05, snapped.z)
  setHighlightColor(isOccupied)
  highlightMesh.visible = true
}

function hideHighlight() {
  if (highlightMesh) highlightMesh.visible = false
}

function getGroundIntersection(event) {
  if (!camera || !renderer || !groundPlane) return null
  const rect = renderer.domElement.getBoundingClientRect()
  mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
  mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1
  raycaster.setFromCamera(mouse, camera)

  const intersects = raycaster.intersectObject(groundPlane)
  return intersects.length > 0 ? intersects[0].point : null
}

function findAssetAtPoint(worldPoint) {
  // 检查机器（优先，因为体积最大）
  for (const [id, data] of machineMeshMap) {
    const pos = data.group.position
    const size = data.group.userData?.size || [2, 2]
    const halfW = (size[0] || 2) * GRID_SIZE * 0.5
    const halfH = (size[1] || 2) * GRID_SIZE * 0.5
    if (Math.abs(worldPoint.x - pos.x) < halfW + 0.1 &&
        Math.abs(worldPoint.z - pos.z) < halfH + 0.1) {
      return { type: 'machine', id, group: data.group }
    }
  }
  // 检查路由点
  for (const child of waypointGroup.children) {
    const pos = child.position
    if (Math.abs(worldPoint.x - pos.x) < GRID_SIZE * 0.5 &&
        Math.abs(worldPoint.z - pos.z) < GRID_SIZE * 0.5) {
      return { type: 'waypoint', id: child.userData.id, group: child }
    }
  }
  // 检查区域（支持普通 Mesh 和 THREE.Group 装饰物）
  for (const child of zoneGroup.children) {
    const pos = child.position
    const userData = child.userData || {}
    if (child.geometry) {
      child.geometry.computeBoundingBox()
      const box = child.geometry.boundingBox
      const hw = (box.max.x - box.min.x) / 2 + 0.1
      const hh = (box.max.z - box.min.z) / 2 + 0.1
      if (Math.abs(worldPoint.x - pos.x) < hw &&
          Math.abs(worldPoint.z - pos.z) < hh) {
        return { type: 'zone', id: userData.id, group: child }
      }
    } else if (child.isGroup || child.children?.length) {
      // THREE.Group（装饰物）：用 Box3 计算包围盒
      const box = new THREE.Box3().setFromObject(child)
      if (box.isEmpty()) continue
      if (worldPoint.x >= box.min.x && worldPoint.x <= box.max.x &&
          worldPoint.z >= box.min.z && worldPoint.z <= box.max.z) {
        return { type: 'zone', id: userData.id, group: child }
      }
    }
  }
  return null
}

function onCanvasPointerDown(event) {
  if (!props.editMode) return
  const point = getGroundIntersection(event)
  if (!point) return

  const asset = findAssetAtPoint(point)
  if (asset) {
    draggingAsset = asset
    isDragging = true
    controls.enabled = false
    // 记录起始状态：资产当前网格坐标 + 鼠标世界坐标
    const startGrid = snapToGrid(asset.group.position)
    dragStartGridX = startGrid.gx
    dragStartGridY = startGrid.gy
    dragStartMouseWorld = point.clone()
    renderer.domElement.style.cursor = 'grabbing'
  }
}

function onCanvasPointerMove(event) {
  if (!props.editMode) return
  const point = getGroundIntersection(event)
  if (!point) { hideHighlight(); return }

  if (isDragging && draggingAsset) {
    // 用鼠标相对于起始点的增量，计算网格偏移
    const dx = point.x - dragStartMouseWorld.x
    const dz = point.z - dragStartMouseWorld.z
    const gridDX = Math.round(dx / GRID_SIZE)
    const gridDZ = Math.round(dz / GRID_SIZE)

    const w = topology.value.gridWidth
    const h = topology.value.gridHeight
    const newGX = Math.max(0, Math.min(dragStartGridX + gridDX, w - 1))
    const newGY = Math.max(0, Math.min(dragStartGridY + gridDZ, h - 1))

    const snapped = gridToWorld(newGX, newGY)
    draggingAsset.group.position.set(snapped.x, 0, snapped.z)

    // 记录最新网格位置（给 pointerUp 使用）
    lastDragGridX = newGX
    lastDragGridY = newGY

    // 碰撞校验
    const occupied = store.isCellOccupied(newGX, newGY, draggingAsset.id, draggingAsset.type)
    updateHighlight(point, occupied)
    renderer.domElement.style.cursor = occupied ? 'not-allowed' : 'grabbing'
  } else {
    const asset = findAssetAtPoint(point)
    if (asset) {
      renderer.domElement.style.cursor = 'grab'
      updateHighlight(point, false)
    } else {
      renderer.domElement.style.cursor = 'crosshair'
      updateHighlight(point, false)
    }
  }
}

function onCanvasPointerUp(event) {
  if (!props.editMode) return

  if (isDragging && draggingAsset) {
    // 用 lastDragGridX/Y（pointerMove 已实时记录），不依赖 pointerUp 的射线检测
    const newGX = lastDragGridX
    const newGY = lastDragGridY

    const success = store.updateAssetPosition(draggingAsset.type, draggingAsset.id, newGX, newGY)

    if (success) {
      emit('asset-moved', { assetType: draggingAsset.type, assetId: draggingAsset.id, gridX: newGX, gridY: newGY })
    } else {
      // 碰撞：仅回弹被拖拽资产到起始位置，不做全量重建
      const pos = gridToWorld(dragStartGridX, dragStartGridY)
      draggingAsset.group.position.set(pos.x, 0, pos.z)
    }

    isDragging = false
    draggingAsset = null
    dragStartMouseWorld = null
    controls.enabled = true
    renderer.domElement.style.cursor = 'crosshair'
  }

  hideHighlight()
}

function setupEditModeListeners() {
  const canvas = renderer?.domElement
  if (!canvas) return
  canvas.addEventListener('pointerdown', onCanvasPointerDown)
  canvas.addEventListener('pointermove', onCanvasPointerMove)
  canvas.addEventListener('pointerup', onCanvasPointerUp)
}

function removeEditModeListeners() {
  const canvas = renderer?.domElement
  if (!canvas) return
  canvas.removeEventListener('pointerdown', onCanvasPointerDown)
  canvas.removeEventListener('pointermove', onCanvasPointerMove)
  canvas.removeEventListener('pointerup', onCanvasPointerUp)
  if (highlightMesh) {
    highlightMesh.visible = false
  }
  isDragging = false
  draggingAsset = null
  dragStartMouseWorld = null
  controls.enabled = true
  renderer.domElement.style.cursor = ''
}

// ==================== 配色常量（从 assets.js 导入） ====================

// ==================== 场景初始化 ====================
function initScene() {
  const container = containerRef.value
  if (!container) return

  // Renderer
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.setSize(container.clientWidth, container.clientHeight)
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.0
  container.appendChild(renderer.domElement)

  // Scene — 根据背景主题设置
  const theme = BACKGROUND_THEMES[props.backgroundTheme] || BACKGROUND_THEMES.clean
  scene = new THREE.Scene()
  scene.background = new THREE.Color(theme.bgColor)
  scene.fog = new THREE.Fog(theme.bgColor, theme.fogNear, theme.fogFar)

  // Camera
  camera = new THREE.PerspectiveCamera(50, container.clientWidth / container.clientHeight, 0.5, 200)
  const w = topology.value.gridWidth
  const h = topology.value.gridHeight
  const dist = Math.max(w, h) * 1.1
  camera.position.set(dist * 0.55, dist * 0.65, dist * 0.55)
  camera.lookAt(0, 0, 0)

  // Lights — 根据主题调整强度
  ambientLight = new THREE.AmbientLight(0xffffff, theme.ambientIntensity)
  scene.add(ambientLight)

  sunLight = new THREE.DirectionalLight(0xffffff, theme.sunIntensity)
  sunLight.position.set(20, 35, 15)
  sunLight.castShadow = true
  sunLight.shadow.mapSize.width = 2048
  sunLight.shadow.mapSize.height = 2048
  sunLight.shadow.camera.near = 0.5
  sunLight.shadow.camera.far = 150
  sunLight.shadow.camera.left = -40
  sunLight.shadow.camera.right = 40
  sunLight.shadow.camera.top = 40
  sunLight.shadow.camera.bottom = -40
  sunLight.bias = -0.0002
  scene.add(sunLight)

  const fill = new THREE.DirectionalLight(0xffffff, 0.8)
  fill.position.set(-10, 8, -10)
  scene.add(fill)

  // Controls
  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.maxPolarAngle = Math.PI / 2 - 0.05
  controls.minDistance = 3
  controls.maxDistance = 100
  controls.target.set(0, 0, 0)
  controls.update()

  // Groups
  zoneGroup = new THREE.Group()
  edgeGroup = new THREE.Group()
  waypointGroup = new THREE.Group()
  decorGroup = new THREE.Group()
  machineGroup = new THREE.Group()
  agvGroup = new THREE.Group()
  scene.add(zoneGroup, edgeGroup, waypointGroup, decorGroup, machineGroup, agvGroup)

  // Clock
  clock = new THREE.Clock()
}

// ==================== 构建静态元素 ====================
function buildGroundAndGrid() {
  if (groundPlane) { scene.remove(groundPlane); groundPlane.geometry?.dispose(); groundPlane.material?.dispose() }
  if (gridHelper) { scene.remove(gridHelper); gridHelper.geometry?.dispose(); gridHelper.material?.dispose() }

  const w = topology.value.gridWidth
  const h = topology.value.gridHeight
  const theme = BACKGROUND_THEMES[props.backgroundTheme] || BACKGROUND_THEMES.clean

  groundPlane = new THREE.Mesh(
    new THREE.PlaneGeometry(w * GRID_SIZE, h * GRID_SIZE),
    new THREE.MeshStandardMaterial({ color: theme.groundColor, roughness: 0.95, metalness: 0.0 }),
  )
  groundPlane.rotation.x = -Math.PI / 2
  groundPlane.receiveShadow = true
  scene.add(groundPlane)

  gridHelper = new THREE.GridHelper(Math.max(w, h) * GRID_SIZE, Math.max(w, h), GRID_COLOR_MAIN, GRID_COLOR_SUB)
  gridHelper.position.y = 0.005
  scene.add(gridHelper)
}

// ==================== 机器构建 ====================
function buildMachines() {
  machineMeshMap.forEach(({ group, bodyMat, lightMat }) => {
    group.traverse(ch => { ch.geometry?.dispose(); ch.material?.dispose() })
    bodyMat.dispose(); lightMat.dispose()
  })
  machineMeshMap.clear()
  while (machineGroup.children.length) machineGroup.remove(machineGroup.children[0])

  const U = GRID_SIZE
  const machines = topology.value.machines
  Object.values(machines).forEach((m) => {
    const pos = gridToWorld(m.location[0], m.location[1])
    const { group, bodyMat, lightMat } = createMachine(U, m)
    group.position.set(pos.x, 0, pos.z)
    machineGroup.add(group)
    machineMeshMap.set(m.id, { group, bodyMat, lightMat })
  })
}

// ==================== 区域 / 路径点 / 边 ====================
function buildZones() {
  while (zoneGroup.children.length) {
    const c = zoneGroup.children[0]
    c.traverse(ch => { ch.geometry?.dispose(); ch.material?.dispose() })
    zoneGroup.remove(c)
  }

  const zones = topology.value.zones
  const U = GRID_SIZE
  zones.forEach((z) => {
    const area = z.area
    const cx = (area.x - topology.value.gridWidth / 2 + area.w / 2) * U
    const cz = (area.y - topology.value.gridHeight / 2 + area.h / 2) * U

    let obj
    if (z.decor) {
      // 装饰资产 → 渲染对应 3D 模型
      if (z.decor === 'bollard') obj = createBollard(U)
      else if (z.decor === 'rack') obj = createRack(U)
      else if (z.decor === 'cabinet') obj = createCabinet(U)
      else if (z.decor === 'barrier') obj = createSafetyBarrier(U)
      else obj = null

      if (obj) {
        obj.position.set(cx, 0, cz)
        obj.traverse(ch => { ch.userData = { id: z.id, name: z.name, type: z.type, decor: z.decor } })
        zoneGroup.add(obj)
      }
    }

    // 普通区域：渲染半透明底板
    if (!z.decor) {
      const geom = new THREE.BoxGeometry(area.w * U, 0.06, area.h * U)
      const mat = new THREE.MeshStandardMaterial({
        color: ZONE_COLORS[z.type] || ZONE_COLORS.default,
        transparent: true, opacity: 0.15, roughness: 0.9,
      })
      const mesh = new THREE.Mesh(geom, mat)
      mesh.position.set(cx, 0.03, cz)
      mesh.receiveShadow = true
      mesh.userData = { id: z.id, name: z.name, type: z.type }
      zoneGroup.add(mesh)
    }
  })
}

function buildWaypoints() {
  waypointPosMap.clear()
  while (waypointGroup.children.length) {
    const c = waypointGroup.children[0]
    c.geometry?.dispose(); c.material?.dispose()
    waypointGroup.remove(c)
  }

  const wps = topology.value.waypoints
  Object.values(wps).forEach((wp) => {
    const pos = gridToWorld(wp.location[0], wp.location[1])
    waypointPosMap.set(wp.id, { x: pos.x, z: pos.z })

    let geom, mat
    if (wp.type === 'dock') {
      geom = new THREE.CylinderGeometry(GRID_SIZE * 0.3, GRID_SIZE * 0.3, GRID_SIZE * 0.06, 16)
      mat = new THREE.MeshStandardMaterial({ color: WAYPOINT_DOCK_COLOR, roughness: 0.3, metalness: 0.6 })
    } else {
      geom = new THREE.SphereGeometry(GRID_SIZE * 0.12, 8, 8)
      mat = new THREE.MeshStandardMaterial({ color: WAYPOINT_DEFAULT_COLOR, roughness: 0.4, metalness: 0.4 })
    }
    const mesh = new THREE.Mesh(geom, mat)
    mesh.position.set(pos.x, GRID_SIZE * 0.04, pos.z)
    mesh.receiveShadow = true
    mesh.userData = { id: wp.id, name: wp.name, type: wp.type }
    waypointGroup.add(mesh)
  })
}

function buildEdges() {
  while (edgeGroup.children.length) {
    const c = edgeGroup.children[0]; c.geometry?.dispose(); c.material?.dispose()
    edgeGroup.remove(c)
  }

  const edges = topology.value.edges
  if (!edges || edges.length === 0) return

  for (const edge of edges) {
    const [fromId, toId] = Array.isArray(edge) ? edge : [edge.from, edge.to]
    const from = waypointPosMap.get(fromId)
    const to = waypointPosMap.get(toId)
    if (!from || !to) continue
    const geom = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(from.x, 0.02, from.z),
      new THREE.Vector3(to.x, 0.02, to.z),
    ])
    const mat = new THREE.LineDashedMaterial({ color: EDGE_COLOR, dashSize: 0.8, gapSize: 1.0 })
    const line = new THREE.Line(geom, mat)
    line.computeLineDistances()
    edgeGroup.add(line)
  }
}

// ==================== 工厂背景层 ====================
function buildBackground() {
  // 清理旧背景
  if (backgroundGroup) {
    backgroundGroup.traverse(ch => { ch.geometry?.dispose(); ch.material?.dispose() })
    scene.remove(backgroundGroup)
    backgroundGroup = null
  }

  // 仅 factory 主题才构建背景层
  if (props.backgroundTheme !== 'factory') return

  const w = topology.value.gridWidth
  const h = topology.value.gridHeight
  backgroundGroup = createFactoryBackground(GRID_SIZE, w, h, props.backgroundSize)
  scene.add(backgroundGroup)
}

// ==================== 科技感围墙 ====================
function buildPerimeterWall() {
  const existingWall = scene.getObjectByName('perimeter-wall')
  if (existingWall) {
    existingWall.traverse(ch => { ch.geometry?.dispose(); ch.material?.dispose() })
    scene.remove(existingWall)
  }

  const w = topology.value.gridWidth
  const h = topology.value.gridHeight
  const wallGroup = createPerimeterWall(GRID_SIZE, w, h)
  scene.add(wallGroup)
}


// ==================== 装饰物构建 ====================
// function buildDecorations() {
//   while (decorGroup.children.length) {
//     const c = decorGroup.children[0]
//     c.traverse(ch => { ch.geometry?.dispose(); ch.material?.dispose() })
//     decorGroup.remove(c)
//   }

//   const w = topology.value.gridWidth
//   const h = topology.value.gridHeight
//   const U = GRID_SIZE
//   const layout = getDecorationLayout(w, h)

//   // 1. 安全警示柱
//   layout.bollards.forEach(([gx, gy]) => {
//     const pos = gridToWorld(gx, gy)
//     const grp = createBollard(U)
//     grp.position.set(pos.x, 0, pos.z)
//     decorGroup.add(grp)
//   })

//   // 2. 储物架
//   layout.racks.forEach(([gx, gy]) => {
//     const pos = gridToWorld(gx, gy)
//     const grp = createRack(U)
//     grp.position.set(pos.x, 0, pos.z)
//     decorGroup.add(grp)
//   })

//   // 3. 配电箱
//   layout.cabinets.forEach(([gx, gy]) => {
//     const pos = gridToWorld(gx, gy)
//     const grp = createCabinet(U)
//     grp.position.set(pos.x, 0, pos.z)
//     decorGroup.add(grp)
//   })

//   // 4. 安全隔离栏
//   layout.barriers.forEach(([gx, gyOffset]) => {
//     const fenceX = gridToWorld(gx, 0).x
//     const fenceZ = gridToWorld(0, 0).z + gyOffset * U
//     const grp = createSafetyBarrier(U)
//     grp.position.set(fenceX, 0, fenceZ)
//     decorGroup.add(grp)
//   })
// }

function buildStaticElements() {
  buildBackground()
  buildGroundAndGrid()
  buildPerimeterWall()
  buildZones()
  buildEdges()
  buildWaypoints()
  // buildDecorations()
  buildMachines()
}


// ==================== AGV 构建 ====================
function rebuildAGVs(count) {
  agvDataList.forEach(d => {
    d.group?.traverse(ch => { ch.geometry?.dispose(); ch.material?.dispose() })
    d.chassisMat?.dispose(); d.lightMat?.dispose()
  })
  agvDataList.length = 0
  while (agvGroup.children.length) agvGroup.remove(agvGroup.children[0])

  const U = GRID_SIZE
  for (let i = 0; i < count; i++) {
    const { group, chassisMat, lightMat } = createAGV(U, i)
    group.position.set(0, 0, 0)
    agvGroup.add(group)
    agvDataList.push({ group, chassisMat, lightMat, targetPos: new THREE.Vector3() })
  }
}

// ==================== 动态更新 ====================
function updateFromStore() {
  const state = store.currentState
  if (!state) return

  const targets = state.grid_state?.positions_xy || []
  const isActive = state.grid_state?.is_active || []

  // AGV 数量同步
  if (agvDataList.length !== targets.length) rebuildAGVs(targets.length)

  // AGV 位置插值
  let allArrived = true
  targets.forEach(([gx, gy], i) => {
    const agvData = agvDataList[i]
    if (!agvData) return
    const target = gridToWorld(gx, gy)
    agvData.targetPos.copy(target)

    const grp = agvData.group
    const dx = target.x - grp.position.x
    const dz = target.z - grp.position.z
    const dist = Math.sqrt(dx * dx + dz * dz)

    const speed = store.isPlaying ? 0.12 : 0.45
    if (dist > 0.02) {
      grp.position.x += dx * speed
      grp.position.z += dz * speed
      allArrived = false
    } else {
      grp.position.x = target.x
      grp.position.z = target.z
    }

    // 活跃状态 → 底盘材质
    const active = isActive[i] !== false
    agvData.chassisMat.color.set(active ? AGV_ACTIVE_COLOR : AGV_IDLE_COLOR)
    agvData.chassisMat.needsUpdate = true
    agvData.lightMat.color.set(active ? AGV_LIGHT_ACTIVE : AGV_LIGHT_IDLE)
  })

  // 机器状态更新
  updateMachineStates(state.machines || {})

  // 自动步进
  const dt = clock.getDelta()
  if (store.isPlaying && allArrived) {
    waitTimeLeft -= dt * 1000
    if (waitTimeLeft <= 0) {
      if (store.nextStep()) waitTimeLeft = STEP_WAIT_TIME
    }
  } else if (!store.isPlaying) {
    waitTimeLeft = 0
  }

  updateScreenLabels()
}

function updateMachineStates(machineStates) {
  machineMeshMap.forEach(({ group, bodyMat, lightMat }, machineId) => {
    const dyn = machineStates[machineId] || {}
    const newStatus = dyn.status || null
    if (newStatus && group.userData.status !== newStatus) {
      group.userData.status = newStatus
      const mc = MACHINE_COLORS[newStatus] || MACHINE_COLORS.IDLE
      bodyMat.color.set(mc.body)
      bodyMat.emissive.set(mc.emissive)
      bodyMat.needsUpdate = true
      lightMat.color.set(STATUS_LIGHT[newStatus] || STATUS_LIGHT.IDLE)
    }
  })
}

// ==================== 屏幕标签 ====================
function updateScreenLabels() {
  if (!renderer || !labelsRef.value) return
  const cw = renderer.domElement.clientWidth
  const ch = renderer.domElement.clientHeight
  const rect = renderer.domElement.getBoundingClientRect()
  const labels = []

  // 机器标签
  machineMeshMap.forEach(({ group }, id) => {
    const vec = group.position.clone()
    vec.y += GRID_SIZE * 0.7
    vec.project(camera)
    if (vec.z > 1) return
    labels.push({
      id: `machine-${id}`,
      x: (vec.x * 0.5 + 0.5) * cw + rect.left - (containerRef.value?.getBoundingClientRect().left || 0),
      y: (-vec.y * 0.5 + 0.5) * ch + rect.top - (containerRef.value?.getBoundingClientRect().top || 0),
      text: group.userData.name || id,
      color: '#445566',
    })
  })

  // AGV 标签
  agvDataList.forEach(({ group }, i) => {
    const vec = group.position.clone()
    vec.y += GRID_SIZE * 0.2
    vec.project(camera)
    if (vec.z > 1) return
    labels.push({
      id: `agv-${i}`,
      x: (vec.x * 0.5 + 0.5) * cw + rect.left - (containerRef.value?.getBoundingClientRect().left || 0),
      y: (-vec.y * 0.5 + 0.5) * ch + rect.top - (containerRef.value?.getBoundingClientRect().top || 0),
      text: `A${i + 1}`,
      color: '#0088bb',
    })
  })

  screenLabels.value = labels
}

// ==================== 渲染循环 ====================
let animationId

function animate() {
  animationId = requestAnimationFrame(animate)
  if (!renderer || !scene || !camera) return

  updateFromStore()
  controls.update()
  renderer.render(scene, camera)
}

// ==================== 大小适配 ====================
function onResize() {
  const container = containerRef.value
  if (!container || !renderer || !camera) return
  const w = container.clientWidth
  const h = container.clientHeight
  if (w === 0 || h === 0) return
  renderer.setSize(w, h)
  camera.aspect = w / h
  camera.updateProjectionMatrix()
}

// ==================== 生命周期 ====================
let resizeObserver = null

onMounted(async () => {
  await nextTick()
  if (!containerRef.value) return

  initScene()
  buildStaticElements()
  rebuildAGVs(0)
  animate()

  resizeObserver = new ResizeObserver(onResize)
  resizeObserver.observe(containerRef.value)

  // 编辑模式：初始化监听器
  if (props.editMode) {
    setupEditModeListeners()
  }
})

onBeforeUnmount(() => {
  cancelAnimationFrame(animationId)
  resizeObserver?.disconnect()
  removeEditModeListeners()
  controls?.dispose()

  // 遍历并清理所有 mesh
  const disposeMesh = (obj) => {
    obj.traverse?.(child => {
      child.geometry?.dispose()
      if (child.material) {
        if (Array.isArray(child.material)) child.material.forEach(m => m.dispose())
        else child.material.dispose()
      }
    })
  }
  if (scene) { disposeMesh(scene); scene.clear() }

  renderer?.dispose()
  renderer?.domElement?.remove()
})

// ==================== 监听拓扑变化 ====================
watch(() => props.staticConfig, (newVal, oldVal) => {
  // 配置引用变化（加载新配置）→ 全量重建
  if (newVal !== oldVal) {
    machineMeshMap.forEach(({ bodyMat, lightMat }) => {
      bodyMat?.dispose(); lightMat?.dispose()
    })
    machineMeshMap.clear()
    while (machineGroup?.children.length) machineGroup.remove(machineGroup.children[0])
    while (agvGroup?.children.length) agvGroup.remove(agvGroup.children[0])
    agvDataList.length = 0

    nextTick(() => {
      buildStaticElements()
      rebuildAGVs(0)
    })
    return
  }

  // 同一引用的深度变更（增删资产）→ 根据 hint 精准重建
  const hint = store.rebuildHint

  // 消费完 hint 后延迟清除，防止残留影响后续操作
  if (hint) {
    nextTick(() => { store.rebuildHint = null })
  }

  if (hint === 'zone') {
    buildZones()
  } else if (hint === 'machine') {
    machineMeshMap.forEach(({ bodyMat, lightMat }) => {
      bodyMat?.dispose(); lightMat?.dispose()
    })
    machineMeshMap.clear()
    while (machineGroup?.children.length) machineGroup.remove(machineGroup.children[0])
    buildMachines()
  } else if (hint === 'waypoint') {
    buildWaypoints()
    buildEdges()
  } else if (hint === 'agv') {
    // AGV 数据不在 topology 中，由 updateFromStore 自动同步数量
  } else if (!hint) {
    // 无 hint（纯数据修正等场景）→ 仅同步重建静态元素
    buildStaticElements()
  }
}, { deep: true })

// ==================== 监听编辑模式切换 ====================
watch(() => props.editMode, (val) => {
  if (val) {
    setupEditModeListeners()
  } else {
    removeEditModeListeners()
  }
})

// ==================== 监听背景主题/尺寸切换 ====================
watch([() => props.backgroundTheme, () => props.backgroundSize], () => {
  if (!scene) return
  const theme = BACKGROUND_THEMES[props.backgroundTheme] || BACKGROUND_THEMES.clean
  scene.background = new THREE.Color(theme.bgColor)
  scene.fog.color.set(theme.bgColor)
  scene.fog.near = theme.fogNear
  scene.fog.far = theme.fogFar
  if (ambientLight) ambientLight.intensity = theme.ambientIntensity
  if (sunLight) sunLight.intensity = theme.sunIntensity
  buildBackground()
  buildGroundAndGrid()
})

// ==================== 暴露方法 ====================
defineExpose({
  /** 获取当前可用的背景主题列表 */
  getBackgroundThemes() {
    return Object.entries(BACKGROUND_THEMES).map(([key, val]) => ({ key, name: val.name }))
  },
})
</script>

<style scoped>
.factory-3d-container {
  width: 100%;
  height: 100%;
  position: relative;
  overflow: hidden;
  background: #0a0e1c;
}

.hud-header {
  position: absolute;
  top: 16px;
  left: 16px;
  right: 16px;
  z-index: 10;
  pointer-events: none;
}

.hud-header :deep(*) {
  pointer-events: auto;
}

.three-wrapper {
  width: 100%;
  height: 100%;
}

.labels-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 5;
}

.world-label {
  position: absolute;
  font-family: "Consolas", "Monaco", monospace;
  font-size: 10px;
  font-weight: bold;
  transform: translate(-50%, -50%);
  text-shadow: 0 1px 4px rgba(0, 0, 0, 0.8), 0 0 8px rgba(0, 0, 0, 0.5);
  white-space: nowrap;
  pointer-events: none;
}
</style>
