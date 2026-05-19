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

// ==================== Props ====================
const props = defineProps({
  staticConfig: { type: Object, required: false, default: () => ({ machines: {}, zones: [], waypoints: {} }) },
  baseGridSize: { type: Number, default: 40 },
  defaultGridWidth: { type: Number, default: 20 },
  defaultGridHeight: { type: Number, default: 14 },
  editMode: { type: Boolean, default: false },
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
  return new THREE.Vector3((gx - w / 2) * GRID_SIZE, 0, (gy - h / 2) * GRID_SIZE)
}

// ==================== Three.js 对象 ====================
let scene, camera, renderer, controls, clock
let groundPlane, gridHelper
let machineGroup, agvGroup, zoneGroup, waypointGroup, edgeGroup, decorGroup

// 对象映射
const machineMeshMap = new Map()   // machineId → { group, bodyMat, lightMat }
const agvDataList = []             // { group, chassisMat, lightMat, targetPos: Vector3 }
const waypointPosMap = new Map()   // waypointId → { x, z } (world)

// 共享几何体 (机器)
let machineBodyGeom, machinePillarGeom, machineTopGeom, machineScreenGeom, statusLightGeom
// 共享几何体 (AGV)
let agvChassisGeom, agvWheelGeom, agvForkGeom, agvArrowGeom, agvLightGeom

// 动画状态
let waitTimeLeft = 0
const STEP_WAIT_TIME = 800

// ==================== 编辑模式交互 ====================
const raycaster = new THREE.Raycaster()
const mouse = new THREE.Vector2()
let highlightMesh = null
let draggingAsset = null        // { type, id, group }
let isDragging = false
let dragStartMouseWorld = null   // 拖拽开始时鼠标的世界坐标
let dragStartGridX = 0           // 拖拽开始时资产的网格 X
let dragStartGridY = 0           // 拖拽开始时资产的网格 Y

function snapToGrid(worldPos) {
  const w = topology.value.gridWidth
  const h = topology.value.gridHeight
  const gx = Math.round(worldPos.x / GRID_SIZE + w / 2)
  const gy = Math.round(worldPos.z / GRID_SIZE + h / 2)
  return {
    gx: Math.max(0, Math.min(gx, w - 1)),
    gy: Math.max(0, Math.min(gy, h - 1)),
  }
}

function createHighlightMesh() {
  const geom = new THREE.BoxGeometry(GRID_SIZE * 1.1, 0.08, GRID_SIZE * 1.1)
  const mat = new THREE.MeshBasicMaterial({
    color: 0x667eea,
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
  highlightMesh.material.color.set(occupied ? 0xff3355 : 0x667eea)
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
  // 检查区域
  for (const child of zoneGroup.children) {
    const pos = child.position
    const userData = child.userData || {}
    // 用 geometry 尺寸估算
    if (child.geometry) {
      child.geometry.computeBoundingBox()
      const box = child.geometry.boundingBox
      const hw = (box.max.x - box.min.x) / 2 + 0.1
      const hh = (box.max.z - box.min.z) / 2 + 0.1
      if (Math.abs(worldPoint.x - pos.x) < hw &&
          Math.abs(worldPoint.z - pos.z) < hh) {
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
    // 用和 pointermove 同样的增量逻辑计算最终位置
    const point = getGroundIntersection(event)
    if (point) {
      const dx = point.x - dragStartMouseWorld.x
      const dz = point.z - dragStartMouseWorld.z
      const gridDX = Math.round(dx / GRID_SIZE)
      const gridDZ = Math.round(dz / GRID_SIZE)

      const w = topology.value.gridWidth
      const h = topology.value.gridHeight
      const newGX = Math.max(0, Math.min(dragStartGridX + gridDX, w - 1))
      const newGY = Math.max(0, Math.min(dragStartGridY + gridDZ, h - 1))

      const success = store.updateAssetPosition(draggingAsset.type, draggingAsset.id, newGX, newGY)
      if (success) {
        emit('asset-moved', { assetType: draggingAsset.type, assetId: draggingAsset.id, gridX: newGX, gridY: newGY })
      } else {
        // 碰撞：回弹到原位
        nextTick(() => buildStaticElements())
      }
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

// ==================== 配色常量 ====================
const BG_COLOR = 0xf0f2f5
const MACHINE_COLORS = {
  WORKING:     { body: 0x4a9eff, emissive: 0x1a5fbb },
  IDLE:        { body: 0xb0b8c8, emissive: 0x000000 },
  BROKEN:      { body: 0xff5555, emissive: 0xcc2222 },
  MAINTENANCE: { body: 0xffaa33, emissive: 0xcc8800 },
}
const STATUS_LIGHT = { WORKING: 0x00e87b, IDLE: 0x8899aa, BROKEN: 0xff2d55, MAINTENANCE: 0xffb300 }
const MACHINE_PILLAR = 0x8899aa
const MACHINE_TOP = 0x556677
const MACHINE_SCREEN = 0x223344
const AGV_ACTIVE_COLOR = 0x3a6ea5
const AGV_IDLE_COLOR = 0xaabbcc
const AGV_FORK = 0x667788
const AGV_WHEEL = 0x333333

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

  // Scene — 白色背景
  scene = new THREE.Scene()
  scene.background = new THREE.Color(BG_COLOR)
  scene.fog = new THREE.Fog(BG_COLOR, 60, 150)

  // Camera
  camera = new THREE.PerspectiveCamera(50, container.clientWidth / container.clientHeight, 0.5, 200)
  const w = topology.value.gridWidth
  const h = topology.value.gridHeight
  const dist = Math.max(w, h) * 1.1
  camera.position.set(dist * 0.55, dist * 0.65, dist * 0.55)
  camera.lookAt(0, 0, 0)

  // Lights — 调整为白色场景
  const ambient = new THREE.AmbientLight(0xffffff, 1.8)
  scene.add(ambient)

  const sun = new THREE.DirectionalLight(0xffffff, 3.0)
  sun.position.set(20, 35, 15)
  sun.castShadow = true
  sun.shadow.mapSize.width = 2048
  sun.shadow.mapSize.height = 2048
  sun.shadow.camera.near = 0.5
  sun.shadow.camera.far = 150
  sun.shadow.camera.left = -40
  sun.shadow.camera.right = 40
  sun.shadow.camera.top = 40
  sun.shadow.camera.bottom = -40
  sun.bias = -0.0002
  scene.add(sun)

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

// ==================== 共享几何体初始化 ====================
function initGeometries() {
  const U = GRID_SIZE
  // 机器
  machineBodyGeom = new THREE.BoxGeometry(U * 1.2, U * 0.5, U * 0.9)
  machinePillarGeom = new THREE.CylinderGeometry(U * 0.035, U * 0.035, U * 0.5, 6)
  machineTopGeom = new THREE.BoxGeometry(U * 0.55, U * 0.07, U * 0.35)
  machineScreenGeom = new THREE.BoxGeometry(U * 0.3, U * 0.15, U * 0.02)
  statusLightGeom = new THREE.SphereGeometry(U * 0.07, 8, 8)
  // AGV
  agvChassisGeom = new THREE.BoxGeometry(U * 0.55, U * 0.06, U * 0.38)
  agvWheelGeom = new THREE.CylinderGeometry(U * 0.055, U * 0.055, U * 0.05, 8)
  agvForkGeom = new THREE.BoxGeometry(U * 0.04, U * 0.025, U * 0.18)
  agvArrowGeom = new THREE.ConeGeometry(U * 0.04, U * 0.08, 4)
  agvLightGeom = new THREE.SphereGeometry(U * 0.04, 6, 6)
}

// ==================== 构建静态元素 ====================
function buildGroundAndGrid() {
  if (groundPlane) { scene.remove(groundPlane); groundPlane.geometry?.dispose(); groundPlane.material?.dispose() }
  if (gridHelper) { scene.remove(gridHelper); gridHelper.geometry?.dispose(); gridHelper.material?.dispose() }

  const w = topology.value.gridWidth
  const h = topology.value.gridHeight

  groundPlane = new THREE.Mesh(
    new THREE.PlaneGeometry(w * GRID_SIZE, h * GRID_SIZE),
    new THREE.MeshStandardMaterial({ color: 0xe8eaed, roughness: 0.95, metalness: 0.0 }),
  )
  groundPlane.rotation.x = -Math.PI / 2
  groundPlane.receiveShadow = true
  scene.add(groundPlane)

  gridHelper = new THREE.GridHelper(Math.max(w, h) * GRID_SIZE, Math.max(w, h), 0xc8ccd4, 0xdce0e6)
  gridHelper.position.y = 0.005
  scene.add(gridHelper)
}

// ==================== 机器构建（精巧复合体） ====================
function buildMachineGroup(m) {
  const U = GRID_SIZE
  const group = new THREE.Group()
  const status = m.status || 'IDLE'
  const mc = MACHINE_COLORS[status] || MACHINE_COLORS.IDLE

  // 1. 主体
  const bodyMat = new THREE.MeshStandardMaterial({ color: mc.body, emissive: mc.emissive, roughness: 0.35, metalness: 0.5 })
  const body = new THREE.Mesh(machineBodyGeom, bodyMat)
  body.position.y = U * 0.28
  body.castShadow = true
  body.receiveShadow = true
  group.add(body)

  // 2. 四根立柱
  const pillarMat = new THREE.MeshStandardMaterial({ color: MACHINE_PILLAR, roughness: 0.4, metalness: 0.6 })
  const corners = [[-U*0.5, U*0.28, -U*0.35], [U*0.5, U*0.28, -U*0.35], [-U*0.5, U*0.28, U*0.35], [U*0.5, U*0.28, U*0.35]]
  corners.forEach(([x, y, z]) => {
    const p = new THREE.Mesh(machinePillarGeom, pillarMat)
    p.position.set(x, y, z)
    p.castShadow = true
    group.add(p)
  })

  // 3. 顶部控制面板
  const topMat = new THREE.MeshStandardMaterial({ color: MACHINE_TOP, roughness: 0.25, metalness: 0.7 })
  const top = new THREE.Mesh(machineTopGeom, topMat)
  top.position.set(0, U * 0.57, -U * 0.1)
  top.castShadow = true
  group.add(top)

  // 4. 前面板（小屏幕）
  const screenMat = new THREE.MeshStandardMaterial({ color: MACHINE_SCREEN, emissive: 0x112244, roughness: 0.1, metalness: 0.3 })
  const screen = new THREE.Mesh(machineScreenGeom, screenMat)
  screen.position.set(0, U * 0.32, U * 0.46)
  group.add(screen)

  // 5. 状态指示灯
  const lightMat = new THREE.MeshBasicMaterial({ color: STATUS_LIGHT[status] || STATUS_LIGHT.IDLE })
  const light = new THREE.Mesh(statusLightGeom, lightMat)
  light.position.set(0, U * 0.62, -U * 0.1)
  group.add(light)

  group.userData = { id: m.id, name: m.name, status, load: 0 }
  return { group, bodyMat, lightMat }
}

function buildMachines() {
  machineMeshMap.forEach(({ group, bodyMat, lightMat }) => {
    bodyMat.dispose(); lightMat.dispose()
  })
  machineMeshMap.clear()
  while (machineGroup.children.length) machineGroup.remove(machineGroup.children[0])

  if (!machineBodyGeom) initGeometries()

  const machines = topology.value.machines
  Object.values(machines).forEach((m) => {
    const pos = gridToWorld(m.location[0], m.location[1])
    const { group, bodyMat, lightMat } = buildMachineGroup(m)
    group.position.set(pos.x, 0, pos.z)
    machineGroup.add(group)
    machineMeshMap.set(m.id, { group, bodyMat, lightMat })
  })
}

// ==================== 区域 / 路径点 / 边 ====================
function buildZones() {
  while (zoneGroup.children.length) {
    const c = zoneGroup.children[0]
    c.geometry?.dispose(); c.material?.dispose()
    zoneGroup.remove(c)
  }

  const zones = topology.value.zones
  zones.forEach((z) => {
    const area = z.area
    const cx = (area.x - topology.value.gridWidth / 2 + area.w / 2) * GRID_SIZE
    const cz = (area.y - topology.value.gridHeight / 2 + area.h / 2) * GRID_SIZE

    const geom = new THREE.BoxGeometry(area.w * GRID_SIZE, 0.06, area.h * GRID_SIZE)
    const typeColors = {
      restricted: 0xff8800, workbench: 0x0088cc,
      obstacle: 0x663333, workarea: 0x00aa66,
    }
    const mat = new THREE.MeshStandardMaterial({
      color: typeColors[z.type] || 0x5577aa,
      transparent: true, opacity: 0.15, roughness: 0.9,
    })
    const mesh = new THREE.Mesh(geom, mat)
    mesh.position.set(cx, 0.03, cz)
    mesh.receiveShadow = true
    mesh.userData = { id: z.id, name: z.name, type: z.type }
    zoneGroup.add(mesh)
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
      mat = new THREE.MeshStandardMaterial({ color: 0x5588bb, roughness: 0.3, metalness: 0.6 })
    } else {
      geom = new THREE.SphereGeometry(GRID_SIZE * 0.12, 8, 8)
      mat = new THREE.MeshStandardMaterial({ color: 0x44aa77, roughness: 0.4, metalness: 0.4 })
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
    const mat = new THREE.LineDashedMaterial({ color: 0x88aacc, dashSize: 0.8, gapSize: 1.0 })
    const line = new THREE.Line(geom, mat)
    line.computeLineDistances()
    edgeGroup.add(line)
  }
}

// ==================== 科技感围墙 ====================
function buildPerimeterWall() {
  // 清理旧墙 (放在 zoneGroup 之前的独立逻辑，复用 decorGroup 不合适，直接挂 scene)
  const existingWall = scene.getObjectByName('perimeter-wall')
  if (existingWall) {
    existingWall.traverse(ch => { ch.geometry?.dispose(); ch.material?.dispose() })
    scene.remove(existingWall)
  }

  const w = topology.value.gridWidth
  const h = topology.value.gridHeight
  const U = GRID_SIZE
  const halfW = (w * U) / 2
  const halfH = (h * U) / 2
  const wallH = U * 0.55
  const offset = U * 0.6  // 墙在网格外侧偏移

  const wallGroup = new THREE.Group()
  wallGroup.name = 'perimeter-wall'

  // --- 材质 ---
  const panelMat = new THREE.MeshStandardMaterial({
    color: 0xdde4ee, transparent: true, opacity: 0.35,
    roughness: 0.1, metalness: 0.8, side: THREE.DoubleSide,
  })
  const frameMat = new THREE.MeshStandardMaterial({
    color: 0x6688aa, roughness: 0.25, metalness: 0.8,
  })
  const glowMat = new THREE.MeshStandardMaterial({
    color: 0x3399cc, emissive: 0x1166aa, emissiveIntensity: 0.6,
    roughness: 0.1, metalness: 0.9,
  })
  const cornerMat = new THREE.MeshStandardMaterial({
    color: 0x5577aa, emissive: 0x113355, roughness: 0.2, metalness: 0.7,
  })
  const baseStripMat = new THREE.MeshStandardMaterial({
    color: 0x4499cc, emissive: 0x1166aa, emissiveIntensity: 0.4,
    roughness: 0.15, metalness: 0.85,
  })
  const topStripMat = new THREE.MeshStandardMaterial({
    color: 0x55aadd, emissive: 0x2288bb, emissiveIntensity: 0.5,
    roughness: 0.1, metalness: 0.9,
  })

  // --- 每面墙的生成 ---
  function createWallSegment(length, position, rotationY) {
    const seg = new THREE.Group()

    // 1. 半透明面板
    const panelGeom = new THREE.PlaneGeometry(length, wallH)
    const panel = new THREE.Mesh(panelGeom, panelMat)
    panel.position.y = wallH / 2 + U * 0.02
    seg.add(panel)

    // 2. 边框立柱 — 每隔 2 个单位一根
    const postGeom = new THREE.BoxGeometry(U * 0.06, wallH + U * 0.04, U * 0.06)
    const postCount = Math.max(2, Math.floor(length / (U * 2)) + 1)
    for (let i = 0; i < postCount; i++) {
      const t = postCount === 1 ? 0 : (i / (postCount - 1) - 0.5) * length
      const post = new THREE.Mesh(postGeom, frameMat)
      post.position.set(t, wallH / 2, 0)
      post.castShadow = true
      seg.add(post)
    }

    // 3. 底部发光条
    const baseGeom = new THREE.BoxGeometry(length, U * 0.04, U * 0.03)
    const base = new THREE.Mesh(baseGeom, baseStripMat)
    base.position.y = U * 0.02
    seg.add(base)

    // 4. 顶部发光条
    const topGeom = new THREE.BoxGeometry(length, U * 0.03, U * 0.03)
    const top = new THREE.Mesh(topGeom, topStripMat)
    top.position.y = wallH + U * 0.01
    seg.add(top)

    // 5. 数据节点 — 每段中间小方块（模拟数据接口）
    const nodeGeom = new THREE.BoxGeometry(U * 0.12, U * 0.08, U * 0.05)
    const nodeCount = Math.max(1, Math.floor(length / (U * 4)))
    for (let i = 0; i < nodeCount; i++) {
      const t = nodeCount === 1 ? 0 : ((i + 0.5) / nodeCount - 0.5) * length
      const node = new THREE.Mesh(nodeGeom, glowMat)
      node.position.set(t, wallH * 0.4, U * 0.01)
      seg.add(node)
    }

    seg.position.copy(position)
    seg.rotation.y = rotationY
    wallGroup.add(seg)
  }

  // --- 四面墙 ---
  // 前 (z = +halfH + offset)
  createWallSegment(w * U, new THREE.Vector3(0, 0, halfH + offset), 0)
  // 后 (z = -halfH - offset)
  createWallSegment(w * U, new THREE.Vector3(0, 0, -halfH - offset), 0)
  // 左 (x = -halfW - offset)
  createWallSegment(h * U, new THREE.Vector3(-halfW - offset, 0, 0), Math.PI / 2)
  // 右 (x = +halfW + offset)
  createWallSegment(h * U, new THREE.Vector3(halfW + offset, 0, 0), Math.PI / 2)

  // --- 四角立柱 ---
  const cornerGeom = new THREE.CylinderGeometry(U * 0.1, U * 0.12, wallH + U * 0.2, 8)
  const cornerTopGeom = new THREE.CylinderGeometry(U * 0.13, U * 0.1, U * 0.06, 8)
  const cornerGlowGeom = new THREE.SphereGeometry(U * 0.06, 8, 8)
  const cornerGlowMat = new THREE.MeshBasicMaterial({ color: 0x44bbff })

  const cornerPositions = [
    [-halfW - offset, -halfH - offset],
    [ halfW + offset, -halfH - offset],
    [-halfW - offset,  halfH + offset],
    [ halfW + offset,  halfH + offset],
  ]
  cornerPositions.forEach(([x, z]) => {
    const grp = new THREE.Group()
    const pillar = new THREE.Mesh(cornerGeom, cornerMat)
    pillar.position.y = (wallH + U * 0.2) / 2
    pillar.castShadow = true
    grp.add(pillar)
    const cap = new THREE.Mesh(cornerTopGeom, frameMat)
    cap.position.y = wallH + U * 0.2
    grp.add(cap)
    const glow = new THREE.Mesh(cornerGlowGeom, cornerGlowMat)
    glow.position.y = wallH + U * 0.28
    grp.add(glow)
    grp.position.set(x, 0, z)
    wallGroup.add(grp)
  })

  scene.add(wallGroup)
}

function buildStaticElements() {
  buildGroundAndGrid()
  buildPerimeterWall()
  buildZones()
  buildEdges()
  buildWaypoints()
  buildDecorations()
  buildMachines()
}

// ==================== 装饰物构建 ====================
function buildDecorations() {
  while (decorGroup.children.length) {
    const c = decorGroup.children[0]
    c.traverse(ch => { ch.geometry?.dispose(); ch.material?.dispose() })
    decorGroup.remove(c)
  }

  const w = topology.value.gridWidth
  const h = topology.value.gridHeight
  const U = GRID_SIZE
  const halfW = w / 2
  const halfH = h / 2

  // --- 共享材质 ---
  const yellowMat = new THREE.MeshStandardMaterial({ color: 0xf5c542, roughness: 0.5, metalness: 0.3 })
  const metalGrayMat = new THREE.MeshStandardMaterial({ color: 0x8899aa, roughness: 0.4, metalness: 0.6 })
  const darkGrayMat = new THREE.MeshStandardMaterial({ color: 0x556677, roughness: 0.6, metalness: 0.3 })
  const cabinetMat = new THREE.MeshStandardMaterial({ color: 0x778899, roughness: 0.35, metalness: 0.5 })
  const rackMat = new THREE.MeshStandardMaterial({ color: 0x99aabb, roughness: 0.5, metalness: 0.4 })
  const shelfMat = new THREE.MeshStandardMaterial({ color: 0xdde4ee, roughness: 0.6, metalness: 0.1 })
  const greenBoxMat = new THREE.MeshStandardMaterial({ color: 0x55aa77, roughness: 0.5, metalness: 0.1 })
  const blueBoxMat = new THREE.MeshStandardMaterial({ color: 0x5577bb, roughness: 0.5, metalness: 0.1 })

  // 1. 安全警示柱 — 沿工厂边缘等距放置
  const bollardGeom = new THREE.CylinderGeometry(U * 0.06, U * 0.06, U * 0.5, 8)
  const bollardCapGeom = new THREE.CylinderGeometry(U * 0.08, U * 0.08, U * 0.04, 8)
  const stripeGeom = new THREE.BoxGeometry(U * 0.04, U * 0.08, U * 0.14)
  const blackMat = new THREE.MeshStandardMaterial({ color: 0x222222, roughness: 0.8 })

  const bollardPositions = []
  // 顶边和底边
  for (let gx = 1; gx < w; gx += 3) {
    bollardPositions.push([gx, 0])
    bollardPositions.push([gx, h - 1])
  }
  // 左边和右边
  for (let gy = 1; gy < h; gy += 3) {
    bollardPositions.push([0, gy])
    bollardPositions.push([w - 1, gy])
  }

  bollardPositions.forEach(([gx, gy]) => {
    const pos = gridToWorld(gx, gy)
    const grp = new THREE.Group()
    // 柱体
    const post = new THREE.Mesh(bollardGeom, yellowMat)
    post.position.y = U * 0.25
    post.castShadow = true
    grp.add(post)
    // 顶部
    const cap = new THREE.Mesh(bollardCapGeom, darkGrayMat)
    cap.position.y = U * 0.52
    grp.add(cap)
    // 黑色条纹
    const s1 = new THREE.Mesh(stripeGeom, blackMat)
    s1.position.set(0, U * 0.15, U * 0.07)
    grp.add(s1)
    const s2 = new THREE.Mesh(stripeGeom, blackMat)
    s2.position.set(0, U * 0.35, U * 0.07)
    grp.add(s2)

    grp.position.set(pos.x, 0, pos.z)
    decorGroup.add(grp)
  })

  // 2. 储物架 — 四角放置
  const rackPositions = [
    [1, 1], [w - 2, 1], [1, h - 2], [w - 2, h - 2],
  ]
  const rackGeom = new THREE.BoxGeometry(U * 0.8, U * 1.2, U * 0.4)
  const shelfGeom = new THREE.BoxGeometry(U * 0.75, U * 0.03, U * 0.36)

  rackPositions.forEach(([gx, gy]) => {
    const pos = gridToWorld(gx, gy)
    const grp = new THREE.Group()

    // 框架
    const frame = new THREE.Mesh(rackGeom, rackMat)
    frame.position.y = U * 0.6
    frame.castShadow = true
    grp.add(frame)

    // 三层隔板 + 货箱
    for (let layer = 0; layer < 3; layer++) {
      const shelfY = U * (0.2 + layer * 0.35)
      const shelf = new THREE.Mesh(shelfGeom, shelfMat)
      shelf.position.y = shelfY
      grp.add(shelf)

      // 每层放 1-2 个小货箱
      const boxGeom = new THREE.BoxGeometry(U * 0.25, U * 0.15, U * 0.25)
      const boxMat = layer % 2 === 0 ? greenBoxMat : blueBoxMat
      const box = new THREE.Mesh(boxGeom, boxMat)
      box.position.set(U * (layer % 2 === 0 ? -0.15 : 0.15), shelfY + U * 0.09, 0)
      box.castShadow = true
      grp.add(box)
    }

    grp.position.set(pos.x, 0, pos.z)
    decorGroup.add(grp)
  })

  // 3. 配电箱 — 长边中间位置
  const cabinetGeom = new THREE.BoxGeometry(U * 0.35, U * 0.7, U * 0.25)
  const cabinetDoorGeom = new THREE.BoxGeometry(U * 0.01, U * 0.5, U * 0.18)
  const lightDotGeom = new THREE.CircleGeometry(U * 0.025, 8)
  const redLightMat = new THREE.MeshBasicMaterial({ color: 0xff4444 })
  const greenLightMat = new THREE.MeshBasicMaterial({ color: 0x44ff44 })

  const cabinetPositions = [
    [Math.floor(w / 2), 0],          // 底边中间
    [Math.floor(w / 2), h - 1],      // 顶边中间
    [0, Math.floor(h / 2)],           // 左边中间
    [w - 1, Math.floor(h / 2)],       // 右边中间
  ]

  cabinetPositions.forEach(([gx, gy]) => {
    const pos = gridToWorld(gx, gy)
    const grp = new THREE.Group()

    // 箱体
    const cab = new THREE.Mesh(cabinetGeom, cabinetMat)
    cab.position.y = U * 0.35
    cab.castShadow = true
    grp.add(cab)

    // 门
    const door = new THREE.Mesh(cabinetDoorGeom, darkGrayMat)
    door.position.set(U * 0.18, U * 0.35, 0)
    grp.add(door)

    // 指示灯
    const redLight = new THREE.Mesh(lightDotGeom, redLightMat)
    redLight.position.set(U * 0.18, U * 0.55, U * 0.06)
    redLight.rotation.y = Math.PI / 2
    grp.add(redLight)
    const greenLight = new THREE.Mesh(lightDotGeom, greenLightMat)
    greenLight.position.set(U * 0.18, U * 0.55, -U * 0.06)
    greenLight.rotation.y = Math.PI / 2
    grp.add(greenLight)

    grp.position.set(pos.x, 0, pos.z)
    decorGroup.add(grp)
  })

  // 4. 安全隔离栏 — 两段长围栏
  const railPostGeom = new THREE.CylinderGeometry(U * 0.03, U * 0.03, U * 0.6, 6)
  const railBarGeom = new THREE.BoxGeometry(U * 3, U * 0.04, U * 0.04)

  // 沿底部 y=0 位置放一段围栏
  if (w > 6) {
    const fenceZ = gridToWorld(0, 0).z - U * 1.5
    for (let i = 0; i < 2; i++) {
      const fenceX = gridToWorld(Math.floor(w / 4) + i * Math.floor(w / 2), 0).x
      const grp = new THREE.Group()
      // 两根立柱
      const p1 = new THREE.Mesh(railPostGeom, metalGrayMat)
      p1.position.set(-U * 1.5, U * 0.3, 0)
      p1.castShadow = true
      grp.add(p1)
      const p2 = new THREE.Mesh(railPostGeom, metalGrayMat)
      p2.position.set(U * 1.5, U * 0.3, 0)
      p2.castShadow = true
      grp.add(p2)
      // 两道横杆
      const bar1 = new THREE.Mesh(railBarGeom, yellowMat)
      bar1.position.y = U * 0.45
      grp.add(bar1)
      const bar2 = new THREE.Mesh(railBarGeom, yellowMat)
      bar2.position.y = U * 0.2
      grp.add(bar2)
      grp.position.set(fenceX, 0, fenceZ)
      decorGroup.add(grp)
    }
  }
}

// ==================== AGV 构建（精巧复合体） ====================
function buildAGVGroup(index) {
  const U = GRID_SIZE
  const group = new THREE.Group()

  // 1. 底盘
  const chassisMat = new THREE.MeshStandardMaterial({
    color: AGV_ACTIVE_COLOR, roughness: 0.25, metalness: 0.6,
  })
  const chassis = new THREE.Mesh(agvChassisGeom, chassisMat)
  chassis.position.y = U * 0.07
  chassis.castShadow = true
  chassis.receiveShadow = true
  group.add(chassis)

  // 2. 四个轮子
  const wheelMat = new THREE.MeshStandardMaterial({ color: AGV_WHEEL, roughness: 0.8, metalness: 0.1 })
  const wheelPos = [[-U*0.22, U*0.035, -U*0.18], [U*0.22, U*0.035, -U*0.18], [-U*0.22, U*0.035, U*0.18], [U*0.22, U*0.035, U*0.18]]
  wheelPos.forEach(([x, y, z]) => {
    const wheel = new THREE.Mesh(agvWheelGeom, wheelMat)
    wheel.position.set(x, y, z)
    wheel.rotation.x = Math.PI / 2
    group.add(wheel)
  })

  // 3. 货叉（两根平行臂）
  const forkMat = new THREE.MeshStandardMaterial({ color: AGV_FORK, roughness: 0.4, metalness: 0.6 })
  const fork1 = new THREE.Mesh(agvForkGeom, forkMat)
  fork1.position.set(-U * 0.1, U * 0.1, U * 0.28)
  group.add(fork1)
  const fork2 = new THREE.Mesh(agvForkGeom, forkMat)
  fork2.position.set(U * 0.1, U * 0.1, U * 0.28)
  group.add(fork2)

  // 4. 方向指示（小箭头）
  const arrowMat = new THREE.MeshBasicMaterial({ color: 0x0099dd })
  const arrow = new THREE.Mesh(agvArrowGeom, arrowMat)
  arrow.position.set(0, U * 0.12, U * 0.22)
  arrow.rotation.x = -Math.PI / 2
  group.add(arrow)

  // 5. 状态指示灯
  const lightMat = new THREE.MeshBasicMaterial({ color: 0x00e87b })
  const light = new THREE.Mesh(agvLightGeom, lightMat)
  light.position.set(0, U * 0.12, 0)
  group.add(light)

  group.userData = { id: `AGV-${index + 1}` }
  return { group, chassisMat, lightMat }
}

function rebuildAGVs(count) {
  agvDataList.forEach(d => { d.chassisMat?.dispose(); d.lightMat?.dispose() })
  agvDataList.length = 0
  while (agvGroup.children.length) agvGroup.remove(agvGroup.children[0])

  if (!agvChassisGeom) initGeometries()

  for (let i = 0; i < count; i++) {
    const { group, chassisMat, lightMat } = buildAGVGroup(i)
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
    agvData.lightMat.color.set(active ? 0x00e87b : 0x8899aa)
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
  initGeometries()
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

  // 清理共享几何体
  ;[machineBodyGeom, machinePillarGeom, machineTopGeom, machineScreenGeom, statusLightGeom,
    agvChassisGeom, agvWheelGeom, agvForkGeom, agvArrowGeom, agvLightGeom
  ].forEach(g => g?.dispose())

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
watch(() => props.staticConfig, () => {
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
}, { deep: true })

// ==================== 监听编辑模式切换 ====================
watch(() => props.editMode, (val) => {
  if (val) {
    setupEditModeListeners()
  } else {
    removeEditModeListeners()
  }
})
</script>

<style scoped>
.factory-3d-container {
  width: 100%;
  height: 100%;
  position: relative;
  overflow: hidden;
  background: #f0f2f5;
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
  text-shadow: 0 1px 3px rgba(255, 255, 255, 0.9), 0 0 6px rgba(255, 255, 255, 0.5);
  white-space: nowrap;
  pointer-events: none;
}
</style>
