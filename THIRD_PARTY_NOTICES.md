# 第三方组件说明

## RFTwin

- 项目：RFTwin — Robot Factory Digital Twin
- 来源：https://github.com/WayneChou-bot/RFTwin
- 版权：MIT License © 2026 Wayne Chou
- 本地目录：`rftwin/`（从官方仓库 codeload 源码包解压，默认分支 `main`）
- 许可证全文：`rftwin/LICENSE`

本项目以独立进程/模块方式复用 RFTwin 的确定性 Python 仿真引擎与 React Three Fiber 三维前端，
未复制粘贴其核心实现。为让统一平台能够按项目配置启动 Live 服务，在
`rftwin/apps/factory_backend/main.py` 中增加了两个向后兼容的环境变量钩子：

- `TWIN_PARAMS_PATH`：加载项目参数 YAML；
- `TWIN_SEED`：覆盖引擎随机种子。

未设置这两个变量时，RFTwin 的行为与原版完全一致（默认参数集、seed 42）。

为统一平台的中文界面，另做了两处仅影响本地化、不影响仿真逻辑的前端改动：

- `rftwin/frontend/index.html`：页面标题改为中文；在应用脚本之前写入默认语言
  `twin.lang=zh`（仅当用户未手动选择过语言时），使嵌入式三维界面默认中文；
- `rftwin/frontend/src/i18n/index.ts`：将内置 `zh` 词库由繁体转为简体（OpenCC t2s，
  构建期一次性转换），并把 `document.lang` 设为 `zh-CN`。

RFTwin 前端依赖（React、Three.js、Vite 等）遵循其各自的开源许可证；构建产物
`rftwin/frontend/dist/` 不纳入本项目源码许可范围。

## EdgeCoder

EdgeCoder（项目根目录下的 `app.py`、`models/`、`scheduler/`、`network_sim/`、
`experiments/`、`generator/`、`tester/`）为本项目原有代码，在统一平台中被复用，
其独立入口与测试保持不变。

---

## Open Industry Project (frontend/oip)

`frontend/oip/` vendors the Open Industry Project editor at upstream commit
`dcebf53` (MIT, Copyright (c) 2024-present Automation Standard LLC and
Contributors). Its own license is in `frontend/oip/LICENSE`, and its
third-party notices (Godot-derived icons/design language, fonts, Monaco,
Codicons, DOMPurify, OCCT/STEP import under LGPL-2.1, USD viewer, etc.) are in
`frontend/oip/THIRD_PARTY_NOTICES.md`. The AIC integration adds
`frontend/oip/public/oip-bridge.js` and one script tag in
`frontend/oip/app/index.html`; those additions are provided under this
project's MIT license. The production build in `frontend/oip/dist/` carries the
same notices and the generated third-party license file.
