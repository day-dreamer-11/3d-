# OIP 子项目（AIC 集成）

- 上游：https://github.com/Open-Industry-Project/Open-Industry-Project
- 固定提交：`dcebf53`（2026-10-29）
- 许可证：MIT（见 `LICENSE`）；第三方资源与 LGPL 组件见 `THIRD_PARTY_NOTICES.md`
- 复制时间：2026-10-30；复制时排除 `.git`、`node_modules`、`e2e/shots`，随后按本机依赖安装并用 `npm run build` 生成 `dist/`

## AIC 集成改动（主要几处）

1. `app/index.html`：在编辑器入口脚本前加入 `<script src="/oip-bridge.js?v=..."></script>`。
2. `public/oip-bridge.js`（新增）：父平台桥接脚本。
   - 读取 URL 参数 `api / projectId / sceneId`；
   - 页面就绪后从场景 API 加载已保存的 OIP 工程（`store.loadProject`）；
   - 用户按 Ctrl+S 或点击左下角「同步到 AIC」时，把 `store.snapshotProject()` POST 到
     `/api/oip/scene-save`，由 Python 转换/保存新版本；
   - 平台内嵌（URL 带 `api/projectId/sceneId`）时给 localStorage 增加 IndexedDB 溢出层：
     OIP 工程键与超大值写入 IndexedDB（按项目 + 场景隔离），自动保存不再受 localStorage
     几 MB 配额限制；刷新后若有未同步编辑会恢复并提示同步；
   - 小键值仍走 localStorage；standalone 打开 OIP 时不启用该层；OIP 不做仿真权威；
   - 内嵌模式禁用并注销 OIP 的 Service Worker：OIP 的 SW 会预缓存 `/app/index.html` 与
     `oip-bridge.js`，平台更新后可能一直加载旧缓存。
3. `public/reset.html`（新增）：平台进入编辑器前的同源重置页；先注销旧 Service Worker、
   清理 Cache Storage，再跳转到带参数的 `/app/`，保证总是加载最新桥接脚本。
4. 修改 `public/oip-bridge.js` / `public/reset.html` 后需同步到 `dist/`（或重新 `npm run build`）。

## 运行模式

- 默认 **静态模式**：若 `dist/index.html` 存在，平台用 Python `http.server` 托管 `dist/`
  （仅监听 127.0.0.1），不需要 Node 运行时。
- 开发模式：设置 `AIC_OIP_DEV=1` 且 `node_modules` 存在时，用 Vite 开发服务器运行。
- 静态构建中桥接依赖 OIP 官方 URL 开关 `?hook=1` 暴露 `window.__oip`；平台生成的编辑器
  URL 已自动附带该参数。

## 重新构建

```bash
cd frontend/oip
npm ci          # 需要 Node 24+
npm run build   # 生成 dist/；构建过程会检查模型、压缩资源
```

构建产物与 `node_modules` 已在 `.gitignore` 中排除；部署时需保留 `dist/`（或设置
`AIC_OIP_DEV=1` 并安装 Node 24）。
