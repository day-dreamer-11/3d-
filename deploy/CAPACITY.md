# 服务器配置与容量参考

以下数字来自本项目在本机的实测（Windows 11、Ryzen 9 8945HX、32GB），用于估算服务器规格；
实际以你的并发、实验时长与三维访问量为准。

## 一、实测资源基线

| 项目 | 实测 |
| --- | --- |
| Streamlit 工作台进程 | 约 **220–230 MB** RSS（含 pandas/plotly 基线） |
| RFTwin 三维服务进程 | 约 **100–110 MB** RSS |
| nginx / 代理 | 约 **10–30 MB** |
| 单容器空闲总计 | 约 **350–400 MB**，加每会话少量增量 |
| 离线实验（10–30 分钟仿真） | 约 **3.5 秒**；峰值额外约 45 MB |
| 并发实验（4 个线程） | 12.6 秒（顺序）vs 13.2 秒（并发）→ **GIL 限制，基本不加速** |
| 三维实时引擎 CPU | 1× 约 2% 单核；30× 约 2%（事件率 1/s → 15/s），RSS 约 105 MB |
| 三维前端构建产物 | 11 MB（gzip 后首屏约 2–5 MB） |
| 单次实验记录 | 0.25–2.7 MB（30 分钟工厂约 1.4 MB；实时采集约 2.7 MB） |
| 数据增长 | 100 次实验约 0.2–0.3 GB；1000 次约 2–3 GB |

**关键结论：**

1. 一个工作台实例（Streamlit 进程）执行仿真时基本只用 **1 个核**；
   增加 CPU 核数不会让单实例的离线实验更快，要提高并发需**多实例 + 负载均衡**。
2. 内存基线不高，但 Streamlit 每个会话的图表/数据会叠加，需留 1–2 GB 余量。
3. 三维服务本身很轻；主要压力来自浏览器渲染，不影响服务器 CPU。

## 二、推荐配置

### A. 最小可用（演示 / 1–3 人 & 偶尔实验）

- **2 vCPU / 4 GB RAM / 40–60 GB SSD / 5–10 Mbps**
- 单节点 Docker + Caddy（自动 HTTPS）+ 每日备份
- 适合内部演示、培训；免费/低配云主机需注意 2GB 内存会偏紧，构建镜像时更明显

### B. 推荐正式（5–10 并发查看，1–2 个实验同时跑）

- **4 vCPU / 8 GB RAM / 100 GB SSD / 20–50 Mbps**
- 单节点或 2 节点；Docker Compose；数据卷 + 每日异地备份
- 8 GB 可容纳 2–3 个工作台实例（每个约 1–1.5 GB 峰值），为发布/构建留余量
- 这是最推荐的起步配置，性价比较高

### C. 高可用 / 多项目（20–50 并发，未来接真实设备）

- **2–3 个节点 × 4–8 vCPU / 16 GB RAM**，或 Kubernetes
- 前置负载均衡（云 LB / Caddy / HAProxy），**Streamlit 需会话粘滞（sticky）**
- 共享存储（NFS/云盘）或迁移到 PostgreSQL；对象存储保存大快照
- 实验任务改为独立 worker + 队列，避免 GIL 串行
- 监控（Prometheus + Grafana / 云监控）+ 日志聚合 + 告警

### 磁盘与带宽

- 系统 + 镜像约 8–12 GB；建议系统盘 40 GB 起；
- 数据盘按“每年 1000 次实验 ≈ 2–3 GB”估算，100 GB 可用多年；
- 备份建议保留 7–30 天，容量按数据盘 2–3 倍预留；
- 每人首屏约 2–5 MB，实时更新流量很小；20–50 Mbps 足够 10–20 人同时使用。

## 三、容错设计

### 单节点（当前部署形态）

- Docker `restart: unless-stopped` + 健康检查（`/healthz`）；
- Caddy 自动 HTTPS，只开放 80/443，**不要暴露 8502/8020**；
- 数据卷持久化 + **每日备份**（restic/rclone 到异地对象存储）；
- 服务器层面：SSD RAID1、UPS、监控磁盘/内存/5xx；
- 发布前先备份，保留上一个镜像标签，可快速回滚。

### 双节点 / 高可用

- 前置负载均衡，健康检查 `/healthz`，失败摘除；
- Streamlit 使用 WebSocket，必须开启**会话粘滞**；
- 每个实例自带一个 RFTwin 服务（同容器），通过 `/twin/` 同源访问，避免跨节点端口问题；
- **数据一致性**：当前项目/实验是本地 JSON 原子写，多实例共享会导致“最后写入者覆盖”。
  高可用前必须二选一：
  1. 迁移到 PostgreSQL（推荐，JSONB 存项目/实验，对象存储存大快照）；
  2. 或按项目/用户分片到固定实例，并配合共享存储与单写者。
- 蓝绿/滚动发布；实验期间避免重启（会中断三维服务）。

### 备份与恢复

```bash
# 每日备份数据卷（示例）
docker run --rm -v aic-data:/data -v /backup:/backup alpine \
  tar czf /backup/aic-$(date +%F).tgz -C /data .
# 异地同步（示例）
rclone sync /backup remote:aic-backup --delete-after
```

每季度做一次恢复演练；备份文件加密保存。

## 四、后续扩展空间

1. **并发实验**：把 `run_factory_experiment` 移到独立 worker + Redis/RQ/Celery，
   工作台只提交任务与轮询结果；这样 4–8 核才能真正被利用。
2. **数据层**：项目/实验迁移 PostgreSQL，事件与大快照放对象存储（S3/MinIO）；
   多实例共享、审计与查询能力更强。
3. **真实设备接入**：新增 OPC UA / MQTT 采集服务，时序库（TimescaleDB/InfluxDB），
   消息代理（MQTT/Redis），并与仿真数据明确区分 `source=real/simulated`。
4. **多租户与权限**：OIDC/SSO 登录、项目级 ACL、配额与审计。
5. **AI 扩展**：当前规则优化/ONNX 推理为 CPU 即可；只有本地大模型/视觉训练才需要 GPU。
6. **可观测性**：node_exporter + cAdvisor + Prometheus + Grafana；日志 Loki/ELK；
   告警：/healthz、5xx 比例、内存、磁盘、三维服务存活。

## 五、Ubuntu 服务器部署（摘要）

```bash
# 1. 安装 Docker 与 Compose 插件（官方脚本或 apt）
# 2. 上传/克隆仓库，进入 deploy
cd aic/deploy
cp .env.example .env
# 编辑 .env：DOMAIN、BASIC_AUTH_PASSWORD
docker compose -f docker-compose.server.yml up -d --build

# 3. 防火墙
sudo ufw allow 22,80,443/tcp
sudo ufw enable

# 4. 检查
docker compose -f docker-compose.server.yml ps
curl -fsS https://$DOMAIN/healthz
```

首次打开 `https://你的域名`，用 `admin` + 密码登录；
进入「三维仿真」点启动，之后三维画面走同源 `/twin/`。

> 构建镜像需要约 2 GB 可用内存和 2 GB 磁盘；低配机建议在 CI 构建后推送到镜像仓库，服务器只拉取运行。
