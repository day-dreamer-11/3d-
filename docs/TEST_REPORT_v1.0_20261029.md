# 测试与交付验证报告

> 环境：Windows 11（10.0.26200）、Python 3.12.10、便携运行时 CPython 3.12.16、
> Inno Setup 6.7.3、Node 24（仅构建三维前端）。
> 本机**没有 Docker**，因此容器镜像未在本机构建/运行；Web 部署配置已完成但未实测容器。

## 1. 自动化测试

| 测试集 | 结果 |
| --- | --- |
| 根项目 pytest（EdgeCoder + 集成层 + UI + SQLite + 启动器 + 公开 API） | **60 passed**，89.75 s |
| RFTwin 原测试 | 157 passed（此前完整运行；本次仅改前端 i18n 词库，后端未改） |
| 测试覆盖 | 仿真确定性/一致性、边缘三策略公平性、项目校验、JSON/SQLite 存储与迁移、OEE/对比/策略排名、规则优化重放、Streamlit 11 页 AppTest、SQLite 迁移、启动器端口探测、只读公开 API |

测试命令：

```powershell
python -m pytest -q
```

## 2. Windows 安装包构建

- 安装包：`dist/AIIndustrialFactory-Setup-1.0.0.exe`（约 **74 MB**）
- SHA-256：`1f91ca038d53563f7178814e92fd4b4c4dd07c9c303faf3646459918cccc7f68`
- 分发压缩包：`release/AI-Industry-Factory-1.0.0-Windows-x64.zip`（76.8 MB，含安装包/校验值/说明）
  SHA-256：`545a665fc464b6272b33970a1599aae6a098947f4b71bc970b4ddf939a614827`
- 安装后占用：约 **410 MB**（含内置 Python 运行时与依赖）
- 构建命令：

```powershell
powershell -ExecutionPolicy Bypass -File build_runtime.ps1   # 首次：组装便携运行时
powershell -ExecutionPolicy Bypass -File build_installer.ps1 # 构建安装包
```

- 安装器：Inno Setup 6，按用户安装（`%LOCALAPPDATA%\Programs\AIIndustrialFactory`），无需管理员权限；
- 创建桌面/开始菜单快捷方式、诊断控制台、停止入口、使用说明、卸载入口；
- 部署层参数：`PrivilegesRequired=lowest`、`PrivilegesRequiredOverridesAllowed=commandline`（静默安装无对话框）。

## 3. 安装/启动/卸载实测（本机）

| 验证项 | 结果 |
| --- | --- |
| 静默安装到独立目录 | 成功，退出码 0，410 MB |
| 安装目录包含内置运行时 | 是（`runtime\python\pythonw.exe`） |
| 使用安装目录内置 Python 启动 | 是；启动器日志记录 `sys.executable` 指向安装目录运行时 |
| 干净用户数据目录（临时 LOCALAPPDATA） | 首次启动自动创建 `AIIndustrialFactory/aic_factory.db` 与“演示工厂”项目 |
| 自动启动 Streamlit + RFTwin | 是，健康检查通过；最终验证使用 8502/8020（端口被占用时自动切换） |
| 浏览器访问 | 打开工作台成功；11 页可切换 |
| 工厂仿真实验 | 通过 UI 运行并写入 SQLite（FEXP…） |
| 调度策略实验 | 通过 UI 运行并写入 SQLite（EEXP…） |
| 三维场景 | iframe 加载成功，RFTwin 中文界面、LIVE 状态正常 |
| 重复启动 | 检测到已运行实例，0 秒内退出并打开现有地址 |
| 网页内“停止本地平台服务” | 4–5 秒内停止 Streamlit/三维服务，状态变为 running=false |
| 停止后再次启动 | 同数据目录恢复，项目与实验记录保留（1 项目 / 2 实验） |
| 卸载 | 桌面/开始菜单快捷方式移除，安装目录完全删除，用户数据目录保留（SQLite 文件仍在） |
| 不误杀其他 Python | 仅终止启动器记录的 Streamlit PID 树与三维服务 PID |

## 4. 未能实测/已知限制

1. **未在另一台“干净 Windows”物理机/虚拟机上测试**：本机没有可用的干净 VM。
   已通过“内置运行时 + 临时 LOCALAPPDATA + 日志确认使用安装目录 Python”模拟；
   建议正式提交前在一台未安装 Python/Node 的 Windows 10/11 x64 上再跑一次安装包。
2. **Docker/服务器镜像未在本机构建**（无 Docker）。`Dockerfile`、`render.yaml`、
   `deploy/docker-compose.server.yml`、Caddy 配置齐全，但容器内运行未实测。
3. **安装包未做代码签名**：Windows SmartScreen 可能提示；安装前必读与压缩包 README 已说明“更多信息 → 仍要运行”。
4. **工厂自由编辑未实现**：当前支持项目/产线/设备参数（节拍、缓冲、故障率、AMR 数量）编辑，
   不支持任意新增/删除设备、拖拽布局与 GLB/GLTF 导入；RFTwin 三维场景为标准 4/4/2/2 布局。
   这是后续扩展项，未用假交互冒充。
5. **Web 多用户隔离未实现**：服务器单实例已切换为 SQLite（WAL），但仍共享 Basic Auth、无账号/租户隔离；
   多实例部署需迁 PostgreSQL。桌面版为单用户 SQLite。
6. **AI 为规则优化**（瓶颈规则 + 引擎重放验证），不是 LLM/ML 训练模型。
7. 边缘网络、任务映射、成本为仿真假设，界面已标注，不是真实 6G 实测。
8. 3D 前端为 RFTwin 第三方组件（MIT），我们做了默认中文/简体词库与部署路径适配。

## 5. 交付物

```text
release/AI-Industry-Factory-1.0.0-Windows-x64.zip  推荐分发（含安装包+说明+校验）
dist/AIIndustrialFactory-Setup-1.0.0.exe   安装包
launcher.py                                本地启动器
build_runtime.ps1 / build_installer.ps1    可复现构建脚本
installer/AIIndustrialFactory.iss          Inno Setup 脚本
docs/评委使用说明.md / docs/演示流程.md      使用与演示
Dockerfile / render.yaml / deploy/*        服务器部署
```

**结论**：安装包已真实生成，并完成本机安装、启动、健康检查、工厂/调度实验、三维页面、
停止、重启持久化、卸载保留数据的验证；未验证项已在上方如实列出。
