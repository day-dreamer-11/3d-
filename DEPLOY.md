# 部署到 Render（单容器 + 密码保护）

本目录已包含 Render 所需文件：

```text
Dockerfile                 单容器：nginx + Streamlit + RFTwin 三维服务
render.yaml                Render Blueprint（Docker Web Service）
deploy/nginx.conf.template 反向代理：/ → Streamlit，/twin/ → RFTwin，/api/、/ws → RFTwin
deploy/supervisord.conf    进程管理
deploy/entrypoint.sh       生成 Basic Auth、注入 $PORT、启动进程
.dockerignore              排除运行数据、依赖与本地构建产物
```

架构（同一个公网端口）：

```text
浏览器 ──https──> Render (nginx :$PORT)
                   ├── /            → Streamlit 工作台 127.0.0.1:8502
                   ├── /twin/       → RFTwin 三维前端 127.0.0.1:8020（构建 base=/twin/）
                   ├── /api/、/ws   → RFTwin REST / WebSocket
                   └── /healthz     → Render 健康检查（免认证）
```

三维服务仍由工作台在容器内按项目参数启动（`TWIN_INTERNAL_PORT=8020`），
所以参数注入、暂停/重置/故障注入等原有能力都保留。

---

## 一、推送到 GitHub

本目录已经是一个本地 Git 仓库。先在 GitHub 新建一个**私有**仓库（不要勾选 README），然后：

```powershell
cd C:\Users\Administrator\Desktop\aic
git remote add origin https://github.com/<你的用户名>/aic-factory-studio.git
git push -u origin main
```

> 如果还没有装 Git 凭据，GitHub 会要求登录/使用 Personal Access Token。

## 二、在 Render 部署

1. 登录 <https://dashboard.render.com>；
2. **New +** → **Blueprint** → 连接刚推送的仓库；
3. Render 会读取根目录 `render.yaml`，创建服务 `aic-factory-studio`；
4. 在环境变量里填写 `BASIC_AUTH_PASSWORD`（必填，建议 16 位以上随机串）；
5. 点击 **Apply / Deploy**，首次构建约 5–10 分钟。

构建完成后访问：

```text
https://<服务名>.onrender.com
用户名：admin（或你在 BASIC_AUTH_USER 中设置的值）
密码：你在 Render 填写的 BASIC_AUTH_PASSWORD
```

进入后先打开 **三维仿真** → **启动项目三维仿真**（首次约 10 秒预热），
三维画面会从同源 `/twin/` 加载。

## 三、免费实例的重要限制

- Render 免费实例 **512MB 内存**，同时运行 Streamlit + RFTwin + nginx **可能内存不足**；
  如果部署日志出现 OOM / 502，升级到 **Starter（2GB）**。
- 免费实例 15 分钟无访问会休眠，下一次访问需要冷启动；三维服务会随进程重启。
- 免费实例**没有持久磁盘**：`render.yaml` 里数据目录是 `/tmp/aic-data`，重新部署/重启后项目与实验记录会丢失。

### 需要持久化时（推荐正式使用）

1. 把 `render.yaml` 中 `plan: free` 改为 `plan: starter`；
2. 取消 `disk` 段注释，挂载 `/var/data`；
3. 把 `AIC_DATA_DIR` 改为 `/var/data`；
4. 重新部署。

## 四、常见问题

| 现象 | 处理 |
| --- | --- |
| 部署成功但打开 502 | 等 30 秒冷启动；看 Render Logs 是否有 OOM |
| `/twin/` 返回 502 | 三维服务尚未启动：进「三维仿真」点击启动；或内部端口被占用 |
| 三维页面空白/WebSocket 报错 | 确认 nginx 的 `/ws` 与 `/twin/` Upgrade 头未被平台改动；`STREAMLIT_SERVER_ENABLE_CORS=false` 已设置；若 Session 报 XSRF 错误，在 Render 增加 `STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=false` 后重启 |
| 登录框反复弹出 | 确认 `BASIC_AUTH_PASSWORD` 不含换行；修改后需要在 Render 手动重启 |
| 数据重启后消失 | 免费实例无持久磁盘，按上文升级并挂载磁盘 |
| 页面很慢 | 免费实例 CPU/内存受限；升级 Starter |

## 四之二、部署到你自己的服务器

服务器规格、实测资源占用、容错与扩展建议见 **`deploy/CAPACITY.md`**。摘要：

- 演示/小团队：**2 vCPU / 4 GB / 40–60 GB SSD**；
- 推荐正式：**4 vCPU / 8 GB / 100 GB SSD**；
- 高可用：2–3 节点 + 负载均衡 + 共享存储/PostgreSQL + 会话粘滞；
- 单次离线实验约 3.5 秒且受 GIL 限制，单实例基本只用 1 核；并发要靠多实例或任务队列。

已提供自有服务器 Compose：

```bash
cd deploy
cp .env.example .env      # 填 DOMAIN 与 BASIC_AUTH_PASSWORD
docker compose -f docker-compose.server.yml up -d --build
```

Caddy 会自动申请 HTTPS 证书；只开放 80/443，不要暴露 8502/8020。

## 五、更新与回滚

- `git push` 会触发 Render 自动重新部署（`autoDeploy: true`）；
- 回滚：Render 控制台 → **Events / Deploys** → 选择历史版本 **Rollback**。

## 六、本地 Docker 自测（可选）

如果本机安装了 Docker：

```powershell
docker build -t aic-factory-studio .
docker run --rm -p 10000:10000 -e BASIC_AUTH_PASSWORD=test1234 aic-factory-studio
# 打开 http://localhost:10000 ，用户名 admin / 密码 test1234
```

## 七、安全说明

- 除 `/healthz` 外，全站由 nginx Basic Auth 保护；三维 iframe 与主站同源，登录一次即可；
- 仓库中**不包含**密码，`BASIC_AUTH_PASSWORD` 只在 Render 控制台填写；
- 平台返回的全部是仿真数据，不包含真实工厂或真实网络测量；
- 如需给 ChatGPT 读取，请单独部署只读接口（`run_public_api.py` + 令牌），不要把工作台直接公开。
