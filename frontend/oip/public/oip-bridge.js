// AIC Factory Studio integration bridge (added by the AIC integration, OIP upstream commit dcebf53).
// The editor page is opened as: /app/?api=http://127.0.0.1:8701&projectId=PRJ-...&sceneId=SCN-...
// - loads the saved OIP project for that unified scene on ready;
// - Ctrl+S (or the "保存" pill) posts the full project to the Python scene API;
// - never changes OIP's own save behaviour.
(function () {
  'use strict'
  var params = new URLSearchParams(location.search)
  var api = (params.get('api') || '').replace(/\/+$/, '')
  var projectId = params.get('projectId') || ''
  var sceneId = params.get('sceneId') || ''
  if (!api || !projectId || !sceneId) return

  // 平台内嵌时持久化由 AIC 后端负责（Ctrl+S / 「保存」）；OIP 自己的 localStorage
  // 自动保存只有几 MB 配额，含装饰模型的演示工程约 20MB，会超出并弹出「Not saving」。
  // 这里给 localStorage 做一个 IndexedDB 溢出层（IndexedDB 配额按磁盘余量动态分配）：
  // - 小键值仍写 localStorage；
  // - OIP 工程键与超大值写入 IndexedDB，并按「项目 + 场景」隔离；
  // - 编辑器内自动保存不再因配额失败；刷新后若存在未同步编辑，可从 IndexedDB 恢复。
  var PROJECT_KEY = 'oip-web/project'
  var IDB_NAME = 'aic-oip-autosave-v1'
  var IDB_STORE = 'kv'
  var LARGE_CHARS = 2000000
  var mem = Object.create(null)
  var idbTimers = {}
  var idbWritesEnabled = false
  var idbDb = null
  var idbReady = null
  var storage = null
  try { storage = window.localStorage } catch (e) {}
  var nativeGet = Storage.prototype.getItem
  var nativeSet = Storage.prototype.setItem
  var nativeRemove = Storage.prototype.removeItem
  var syncMarkKey = 'aic-oip/last-sync/' + projectId + '/' + sceneId

  function idbKey(key) { return 'p:' + projectId + ':s:' + sceneId + ':' + key }

  function openIdb() {
    if (!window.indexedDB) return Promise.resolve(null)
    if (idbDb) return Promise.resolve(idbDb)
    if (idbReady) return idbReady
    idbReady = new Promise(function (resolve) {
      var req
      try { req = window.indexedDB.open(IDB_NAME, 1) } catch (e) { resolve(null); return }
      req.onupgradeneeded = function () {
        try { if (!req.result.objectStoreNames.contains(IDB_STORE)) req.result.createObjectStore(IDB_STORE) } catch (e) {}
      }
      req.onsuccess = function () { idbDb = req.result; resolve(idbDb) }
      req.onerror = function () { resolve(null) }
      req.onblocked = function () { resolve(null) }
    })
    return idbReady
  }

  function idbGet(key) {
    return openIdb().then(function (db) {
      if (!db) return null
      return new Promise(function (resolve) {
        try {
          var req = db.transaction(IDB_STORE, 'readonly').objectStore(IDB_STORE).get(idbKey(key))
          req.onsuccess = function () { resolve(req.result || null) }
          req.onerror = function () { resolve(null) }
        } catch (e) { resolve(null) }
      })
    })
  }

  function idbPut(key, value) {
    return openIdb().then(function (db) {
      if (!db) return false
      return new Promise(function (resolve) {
        try {
          var tx = db.transaction(IDB_STORE, 'readwrite')
          tx.objectStore(IDB_STORE).put({ value: value, savedAt: Date.now() }, idbKey(key))
          tx.oncomplete = function () { resolve(true) }
          tx.onerror = tx.onabort = function () { resolve(false) }
        } catch (e) { resolve(false) }
      })
    }).catch(function () { return false })
  }

  function idbDelete(key) {
    return openIdb().then(function (db) {
      if (!db) return
      try { db.transaction(IDB_STORE, 'readwrite').objectStore(IDB_STORE).delete(idbKey(key)) } catch (e) {}
    })
  }

  function scheduleIdb(key, value) {
    if (!idbWritesEnabled) return
    clearTimeout(idbTimers[key])
    idbTimers[key] = setTimeout(function () { delete idbTimers[key]; void idbPut(key, value) }, 600)
  }

  function lastSyncedAt() {
    try { return Number(nativeGet.call(storage, syncMarkKey) || 0) } catch (e) { return 0 }
  }

  function markSynced() {
    try { nativeSet.call(storage, syncMarkKey, String(Date.now())) } catch (e) {}
  }

  if (window.indexedDB && storage) {
    Storage.prototype.getItem = function (key) {
      if (this === storage) {
        // 内嵌模式下工程只认 AIC 后端 / IndexedDB，忽略浏览器里遗留的原生工程，
        // 避免旧会话的本地副本触发 OIP 的「另一个标签页已修改」保护而停止保存。
        if (key === PROJECT_KEY) {
          return Object.prototype.hasOwnProperty.call(mem, key) ? mem[key] : null
        }
        if (Object.prototype.hasOwnProperty.call(mem, key)) return mem[key]
      }
      return nativeGet.call(this, key)
    }
    Storage.prototype.setItem = function (key, value) {
      if (this !== storage) return nativeSet.call(this, key, value)
      var text = String(value)
      if (key === PROJECT_KEY || text.length > LARGE_CHARS) {
        mem[key] = text
        scheduleIdb(key, text)
        return
      }
      try {
        nativeSet.call(this, key, text)
      } catch (e) {
        // 小键值也遇到配额时回退到 IndexedDB，避免 OIP 弹出「Not saving」。
        mem[key] = text
        scheduleIdb(key, text)
      }
    }
    Storage.prototype.removeItem = function (key) {
      if (this === storage) {
        delete mem[key]
        clearTimeout(idbTimers[key])
        void idbDelete(key)
      }
      return nativeRemove.call(this, key)
    }
  }

  // OIP 的 Service Worker 会预缓存 /app/index.html 与 /oip-bridge.js；平台更新后浏览器
  // 仍可能拿旧缓存（表现为刷新后继续报 localStorage 配额）。内嵌模式直接禁用并注销 SW，
  // 由 AIC 重置页先清理，再加载最新静态文件。
  if ('serviceWorker' in navigator) {
    try {
      navigator.serviceWorker.register = function () {
        return Promise.reject(new Error('AIC embedded mode: service worker disabled'))
      }
    } catch (e) {}
    try {
      navigator.serviceWorker.getRegistrations().then(function (regs) {
        regs.forEach(function (r) { void r.unregister() })
      }).catch(function () {})
    } catch (e) {}
  }

  function store() { return window.__oip && window.__oip.store }
  function snapshot() {
    var s = store()
    if (!s || typeof s.snapshotProject !== 'function') return null
    try { return s.snapshotProject() } catch (e) { return null }
  }
  function post(type, extra) {
    try { window.parent && window.parent.postMessage(Object.assign({ source: 'oip-aic', type: type }, extra || {}), '*') } catch (e) {}
  }

  var pill = document.createElement('div')
  pill.id = 'aic-sync-pill'
  pill.style.cssText = [
    'position:fixed', 'left:14px', 'bottom:14px', 'z-index:2147483000',
    'display:flex', 'align-items:center', 'gap:8px', 'padding:7px 10px',
    'border-radius:999px', 'background:rgba(17,32,51,.92)', 'color:#dbe9f7',
    'font:12px/1.4 system-ui,"Microsoft YaHei",sans-serif', 'box-shadow:0 4px 18px rgba(0,0,0,.25)',
    'border:1px solid rgba(120,180,240,.35)'
  ].join(';')
  var dot = document.createElement('span')
  dot.style.cssText = 'width:8px;height:8px;border-radius:50%;background:#e0b341;display:inline-block'
  var label = document.createElement('span')
  label.textContent = '正在加载场景…'
  var button = document.createElement('button')
  button.type = 'button'
  button.textContent = '保存'
  button.style.cssText = 'border:0;border-radius:999px;padding:3px 9px;background:#2f6fed;color:white;cursor:pointer;font-size:11px'
  var copyButton = button.cloneNode()
  copyButton.textContent = '另存为'
  var copyForm = document.createElement('form')
  copyForm.hidden = true
  var copyName = document.createElement('input')
  copyName.placeholder = '输入新场景名称'
  copyName.setAttribute('aria-label', '新场景名称')
  copyName.maxLength = 80
  copyName.required = true
  var submitCopy = button.cloneNode()
  submitCopy.type = 'submit'
  submitCopy.textContent = '保存副本'
  copyForm.append(copyName, submitCopy)
  pill.append(dot, label, button, copyButton, copyForm)
  var setState = function (text, color) { label.textContent = text; dot.style.background = color }
  var lastSync = 0
  var busy = false

  async function sync(reason, copyNameValue) {
    if (busy) return
    var project = snapshot()
    if (!project) { setState('AIC: 编辑器尚未就绪', '#e0b341'); return }
    busy = true
    setState(copyNameValue ? '正在保存副本…' : '正在保存…', '#e0b341')
    try {
      var res = await fetch(api + '/api/oip/scene-save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: projectId, scene_id: sceneId, project: project, save_as_name: copyNameValue })
      })
      var data = await res.json().catch(function () { return {} })
      if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status))
      if (copyNameValue) {
        copyForm.hidden = true
        setState('已另存为「' + data.name + '」。点击页面上的「刷新场景列表」后可打开副本。', '#38b26f')
        return
      }
      lastSync = Date.now()
      markSynced()
      setState('已保存 v' + data.scene_version + ' · ' + data.devices + ' 设备 · ' + data.connections + ' 连接', '#38b26f')
      post('saved', { scene_version: data.scene_version, devices: data.devices, connections: data.connections, errors: data.errors, warnings: data.warnings, reason: reason })
    } catch (e) {
      setState('保存失败 · ' + String(e.message || e).slice(0, 60), '#e05a4f')
      post('sync-error', { message: String(e.message || e) })
    } finally {
      busy = false
    }
  }

  // AIC: ?live=1 shows the platform's live Python simulation states inside the editor: a status
  // panel polled from the scene API, and a gentle highlight of a failed/blocked device when idle.
  var LIVE_STATE = { idle: '空闲', running: '运行', processing: '加工中', blocked: '阻塞', failed: '故障', maintenance: '维护' }
  var LIVE_COLOR = { idle: '#8a949e', running: '#38b26f', processing: '#2f6fed', blocked: '#e0b341', failed: '#e05a4f', maintenance: '#7a6ff0' }
  var livePanel = null
  var liveRows = null
  var liveTitle = null
  var liveSummary = null
  var liveHighlight = null

  function ensureLivePanel() {
    if (livePanel) return livePanel
    livePanel = document.createElement('div')
    livePanel.id = 'aic-live-panel'
    livePanel.style.cssText = [
      'position:fixed', 'right:14px', 'top:74px', 'z-index:2147482999', 'width:248px',
      'padding:10px 12px', 'border-radius:12px', 'background:rgba(255,255,255,.96)',
      'color:#16324f', 'font:12px/1.5 system-ui,"Microsoft YaHei",sans-serif',
      'box-shadow:0 6px 24px rgba(16,50,79,.18)', 'border:1px solid rgba(42,127,143,.28)'
    ].join(';')
    liveTitle = document.createElement('div')
    liveTitle.style.cssText = 'font-weight:700;color:#1f5b6b;margin-bottom:2px'
    liveTitle.textContent = '实时仿真联动'
    liveSummary = document.createElement('div')
    liveSummary.style.cssText = 'color:#55707f;font-size:11px;margin-bottom:6px'
    liveRows = document.createElement('div')
    livePanel.appendChild(liveTitle)
    livePanel.appendChild(liveSummary)
    livePanel.appendChild(liveRows)
    document.body.appendChild(livePanel)
    return livePanel
  }

  function hideLivePanel() {
    if (livePanel) livePanel.style.display = 'none'
  }

  function renderLive(sim) {
    ensureLivePanel().style.display = 'block'
    var kpi = sim.kpis || {}
    liveSummary.textContent = '仿真 ' + Number(sim.sim_time_sec || 0).toFixed(1) + ' s · 良品 ' + (kpi.good_units != null ? kpi.good_units : '—')
    var states = sim.device_states || []
    var html = ''
    for (var i = 0; i < Math.min(states.length, 8); i++) {
      var d = states[i]
      var color = LIVE_COLOR[d.state] || '#8a949e'
      var label = LIVE_STATE[d.state] || d.state || ''
      var name = String(d.name || d.device_id || '')
      var dot = '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:' + color + ';margin-right:5px"></span>'
      html += '<div style="display:flex;justify-content:space-between;gap:8px;padding:2px 0;border-top:1px solid rgba(42,127,143,.08)">' +
        '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:150px">' + dot + name.replace(/[<>&]/g, '') + '</span>' +
        '<span style="color:' + color + ';white-space:nowrap">' + label + '</span></div>'
    }
    liveRows.innerHTML = html ||
      '<div style="color:#8a949e">实时仿真尚未产生设备状态</div>'
  }

  async function pollLive() {
    try {
      var res = await fetch(api + '/api/simulations', { cache: 'no-store' })
      if (!res.ok) { hideLivePanel(); return }
      var sims = await res.json()
      var sim = null
      for (var i = 0; i < (sims || []).length; i++) {
        var s = sims[i]
        if (s && s.scene_id === sceneId && (s.status === 'running' || s.status === 'paused')) { sim = s; break }
      }
      if (!sim) { hideLivePanel(); return }
      renderLive(sim)
      var hook = window.__oip
      var st = hook && hook.store && hook.store.getState && hook.store.getState()
      if (st && st.mode === 'idle' && (!st.selectedIds || st.selectedIds.length === 0)) {
        var states = sim.device_states || []
        var target = null
        for (var j = 0; j < states.length; j++) if (states[j].state === 'failed') { target = states[j]; break }
        if (!target) for (var k = 0; k < states.length; k++) if (states[k].state === 'blocked') { target = states[k]; break }
        // 选中会在仿真启动时被 OIP 清空，因此只要当前没有选中就重新高亮受限设备
        if (target && target.device_id) {
          liveHighlight = target.device_id
          try { hook.store.select(target.device_id) } catch (e) {}
        }
      }
    } catch (e) {
      /* the scene API being down keeps the last panel; next poll retries */
    }
  }

  // AIC: ?autoplay=1 starts OIP's own simulation once the saved scene is loaded, so the line
  // animates as soon as the editor opens. 视觉动画；生产 KPI 仍来自 Python 仿真。
  function scheduleAutoplay() {
    var tries = 0
    var timer = setInterval(function () {
      var hook = window.__oip
      var state = hook && hook.store && hook.store.getState && hook.store.getState()
      var parts = state && state.scene && state.scene.parts
      if (state && state.running) { clearInterval(timer); return }
      if (parts && parts.length > 0 && hook.runCommand) {
        try { hook.runCommand('sim.run') } catch (e) { /* keep trying */ }
      }
      if (++tries > 24) clearInterval(timer)
    }, 1200)
  }

  async function loadSavedScene() {
    var cached = await idbGet(PROJECT_KEY)
    try {
      var res = await fetch(api + '/api/projects/' + encodeURIComponent(projectId) + '/scenes/' + encodeURIComponent(sceneId) + '/oip-project')
      if (!res.ok) throw new Error('HTTP ' + res.status)
      var project = await res.json()
      var s = store()
      if (!s || typeof s.loadProject !== 'function') throw new Error('编辑器尚未就绪')
      s.loadProject(project)
      var restored = false
      // 上次同步之后又有本地编辑 → 优先恢复本地自动保存，提示用户再同步。
      if (cached && cached.value && Number(cached.savedAt) > lastSyncedAt() + 3000) {
        try {
          s.loadProject(JSON.parse(cached.value))
          restored = true
        } catch (e) { restored = false }
      }
      setState(
        restored ? 'AIC: 已恢复本地未同步编辑' : ('AIC: 已载入场景 v' + (project && project.version ? project.version : '?')),
        restored ? '#e0b341' : '#38b26f'
      )
      post('loaded', { sceneId: sceneId, restored: restored })
    } catch (e) {
      if (cached && cached.value) {
        try {
          var cachedProject = JSON.parse(cached.value)
          var cachedStore = store()
          if (cachedStore && typeof cachedStore.loadProject === 'function') {
            cachedStore.loadProject(cachedProject)
            setState('AIC: 后端不可用，已用本地缓存', '#e0b341')
            post('loaded', { sceneId: sceneId, cached: true })
            return
          }
        } catch (cachedError) {}
      }
      setState('AIC: 载入场景失败', '#e05a4f')
    } finally {
      // 后端场景已载入（或已尝试），此后的自动保存才允许写 IndexedDB，
      // 避免启动瞬间的空工程覆盖上一轮的本地编辑。
      idbWritesEnabled = true
    }
  }

  var boot = setInterval(function () {
    if (!store()) return
    clearInterval(boot)
    document.body.appendChild(pill)
    pill.addEventListener('mousedown', function (e) { e.stopPropagation() })
    copyButton.addEventListener('click', function () {
      copyForm.hidden = !copyForm.hidden
      if (!copyForm.hidden) {
        var project = snapshot()
        copyName.value = (project && project.name ? project.name : '新场景').slice(0, 77) + ' 副本'
        copyName.focus()
        copyName.select()
      }
    })
    copyForm.addEventListener('keydown', function (e) { e.stopPropagation() })
    copyForm.addEventListener('submit', function (e) {
      e.preventDefault()
      var name = copyName.value.trim()
      if (name) void sync('save-as', name)
    })
    button.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); sync('button') })
    window.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && String(e.key).toLowerCase() === 's') {
        // OIP's own save runs as usual; sync shortly after it commits.
        setTimeout(function () { sync('ctrl+s') }, 500)
      }
    }, true)
    loadSavedScene()
    if (params.get('autoplay') === '1') setTimeout(scheduleAutoplay, 1500)
    if (params.get('live') === '1') { void pollLive(); setInterval(pollLive, 1500) }
    post('ready', { sceneId: sceneId })
  }, 200)
})()
