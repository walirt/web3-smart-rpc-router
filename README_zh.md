# Web3 Smart RPC Router

[English](README.md) | 中文

> _由 [CyOps](https://docs.cysic.xyz/cysic-ai/cysic-automation/cyops/) 构建_

一个面向 Ethereum 风格 JSON-RPC 流量的本地智能网关。它把多个公共上游 RPC 节点聚合成一个稳定的本地入口，在上游出现临时故障时自动故障转移，并通过终端 TUI 展示实时健康、流量和自愈日志。

## 解决的问题

免费公共 RPC 节点很方便，但也很脆弱。单个节点可能返回 `429` 限流、`5xx`
故障、连接超时，或者短暂不可用。很多本地脚本、钱包和开发工具只接受一个
稳定的 JSON-RPC URL，因此任何上游波动都会直接变成应用失败。

本项目提供一个稳定的本地入口：

```text
client -> http://127.0.0.1:<listen_port>/ -> best available upstream RPC node
```

当上游返回临时失败时，路由器会按策略尝试其他节点，使用有上限的指数退避，
并把成功上游的响应返回给调用方。客户端不会看到中间的 `429` 或 `5xx`。

## 功能清单

| 功能 | 状态 | 证据 |
|---|---:|---|
| 严格 YAML 配置契约 | 完成 | `core/models.py`, `core/config.py`, `config.yaml` |
| 拒绝未知配置字段 | 完成 | Pydantic `extra="forbid"` |
| 本地 JSON-RPC 代理 | 完成 | `core/router.py`, `POST /` |
| 存活检查接口 | 完成 | `GET /healthz -> {"ok": true}` |
| 针对 `429` 和 `5xx` 的故障转移 | 完成 | `forward_with_failover()` 与路由测试 |
| 网络错误、超时、异常 JSON 的故障转移 | 完成 | 使用 `aioresponses` 的上游模拟测试 |
| 有上限的指数退避 | 完成 | `request_timeout_seconds / 4` 起始，`* 4` 封顶 |
| 多种路由策略 | 完成 | `priority`, `round_robin`, `lowest_latency`, `failover` |
| 后台健康探测 | 完成 | `core/prober.py`, `eth_blockNumber` 探测 |
| 内存状态与快照 | 完成 | `core/state.py`, `RouterState.snapshot()` |
| 只读 Rich TUI 大盘 | 完成 | `ui/dashboard.py`, `--with-tui` |
| 100% 行覆盖和分支覆盖 | 完成 | `pytest --cov-branch --cov-fail-under=100` |
| 严格类型检查和 lint | 完成 | `mypy --strict core ui`, `ruff check core ui tests` |

## 快速开始

安装依赖：

```bash
pip install -r requirements.txt
```

验证示例配置：

```bash
python -m core.config config.yaml
```

启动路由器和终端大盘：

```bash
python -m core.router config.yaml --with-tui
```

发送 JSON-RPC 请求到：

```text
http://127.0.0.1:8545/
```

检查存活状态：

```bash
curl http://127.0.0.1:8545/healthz
```

预期响应：

```json
{"ok": true}
```

## 配置

`config.yaml` 是运行时行为的单一配置来源：

```yaml
global:
  listen_host: 0.0.0.0
  listen_port: 8545
  probe_interval_seconds: 5.0
  request_timeout_seconds: 10.0
  routing_strategy: priority

method_routes:
  eth_getLogs:
    providers: [cloudflare-eth]
    routing_strategy: priority

rpc_nodes:
  - provider: cloudflare-eth
    url: https://cloudflare-ethereum.com
    priority: 1
    headers: {}
```

### 如何使用 `config.yaml`

1. 复制或直接编辑仓库根目录下的 `config.yaml`。
2. 设置 `global.listen_host`：`127.0.0.1` 表示仅本机访问，`0.0.0.0`
   表示监听所有本机网络接口。
3. 设置 `global.listen_port`，它决定本地路由器监听的端口。
4. 设置 `global.routing_strategy`，它决定路由器如何选择上游节点。
5. 在 `rpc_nodes` 中为每个上游 RPC 服务商添加一条节点配置。
6. 每个节点都需要唯一的 `provider` 名称和唯一的 `priority` 数值。
7. 如有需要，在 `method_routes` 中为特定 JSON-RPC 方法指定 provider 子集
   或策略覆盖。
8. 启动前先验证配置：

```bash
python -m core.config config.yaml
```

9. 使用该配置启动路由器：

```bash
python -m core.router config.yaml --with-tui
```

字段说明：

| 字段 | 位置 | 含义 |
|---|---|---|
| `listen_host` | `global` | 本地绑定地址；`127.0.0.1` 仅本机访问，`0.0.0.0` 监听所有本机网络接口 |
| `listen_port` | `global` | 路由器暴露的本地 HTTP 端口 |
| `probe_interval_seconds` | `global` | 后台健康探测的间隔 |
| `request_timeout_seconds` | `global` | 上游请求超时，也是退避计算的基础 |
| `routing_strategy` | `global` | 可选 `priority`、`round_robin`、`lowest_latency`、`failover` |
| `method_routes.<method>.providers` | `method_routes` | 某个 JSON-RPC 方法允许使用的 provider 名称 |
| `method_routes.<method>.routing_strategy` | `method_routes` | 该方法可选的策略覆盖 |
| `provider` | `rpc_nodes[]` | 上游节点的唯一名称，也用于状态和 TUI 展示 |
| `url` | `rpc_nodes[]` | 上游 JSON-RPC HTTP(S) 地址 |
| `priority` | `rpc_nodes[]` | 数值越小优先级越高 |
| `headers` | `rpc_nodes[]` | 发往该上游节点的可选 HTTP 请求头 |

两个上游节点示例：

```yaml
global:
  listen_port: 8545
  probe_interval_seconds: 5.0
  request_timeout_seconds: 10.0
  routing_strategy: priority

method_routes:
  eth_getLogs:
    providers: [archive]
    routing_strategy: priority

  eth_sendRawTransaction:
    providers: [tx-broadcast, fallback]
    routing_strategy: failover

rpc_nodes:
  - provider: archive
    url: https://primary.example/rpc
    priority: 1
    headers: {}

  - provider: tx-broadcast
    url: https://tx.example/rpc
    priority: 2
    headers: {}

  - provider: fallback
    url: https://fallback.example/rpc
    priority: 3
    headers:
      X-Demo: local-router
```

配置校验规则：

- 顶层只允许 `global`、`method_routes` 和 `rpc_nodes`。
- 节点和全局配置中的未知字段都会被拒绝。
- provider 名称和 priority 必须唯一。
- `method_routes` 只能引用 `rpc_nodes` 中已经声明的 provider。
- URL 必须使用 `http` 或 `https`。
- `routing_strategy` 属于 `global`，不要放到单个节点下面。
- timeout 和 probe interval 必须为正数。
- 错误 YAML 和非法 schema 会原样抛出，不会被吞掉。

## 运行行为

### 接口

| 方法 | 路径 | 行为 |
|---|---|---|
| `POST` | `/` | JSON-RPC 请求透传到选中的上游节点 |
| `GET` | `/healthz` | 本地存活检查，进程运行时返回 HTTP 200 |

### 故障转移契约

以下上游结果会触发重试：

- HTTP `429`
- HTTP `5xx`
- `aiohttp.ClientError`
- `asyncio.TimeoutError`
- JSON 响应不是对象
- JSON 解析或内容类型错误

退避策略为有上限的指数退避：

```text
base  = request_timeout_seconds / 4
cap   = request_timeout_seconds * 4
delay = min(base * 2 ** (attempt - 1), cap)
```

当所有节点都失败时，本地代理返回：

```json
{"error": "no healthy upstream"}
```

HTTP 状态码为 `503`。

### 路由策略

| 策略 | 选择规则 |
|---|---|
| `priority` | 选择健康节点中 priority 数值最小的节点 |
| `round_robin` | 按 priority 顺序在健康节点中轮转 |
| `lowest_latency` | 选择健康节点中探测延迟最低的节点 |
| `failover` | 始终从 priority 顺序中的第一个健康节点开始 |

如果所有节点都被标记为不健康，转发路径仍会按 priority 顺序尝试完整配置链。
这样当全局故障恢复后，服务仍然可以自愈。

### 按方法分流

`method_routes` 可以让特定 JSON-RPC 方法使用指定 provider 子集，并可选择覆盖
全局策略。这适合不同 RPC 方法对基础设施要求不同的场景：

- `eth_getLogs` 可以路由到 archive 能力更强的节点。
- `eth_sendRawTransaction` 可以路由到交易广播更稳定的节点。
- 未命中的方法继续使用全局 `routing_strategy` 和完整节点列表。

当前只对单个 JSON-RPC 请求对象做方法分流。Batch 请求的逐项分流可以后续再加。

## 终端大盘

使用 `--with-tui` 可以在同一个事件循环中启动 Rich 终端大盘。TUI 是只读的：
它只读取 `RouterState.snapshot()`，不会修改请求流。

大盘包含：

- Header：运行状态、uptime、全局 routing strategy 和监听端口。
- Node Health：provider、status、ping、pressure bar、success-rate estimate。
- Method Routing：按方法配置的 provider 子集和可选策略覆盖。
- Traffic & Performance：当前 TPS、故障转移次数、总请求数和流量迁移提示。
- Live Self-Healing Logs：探测失败、故障转移和请求事件日志。

实现位置：`ui/dashboard.py`，主要使用 `rich.layout.Layout`、`rich.panel.Panel`
和 `rich.table.Table`。

## 架构

```text
config.yaml
    |
    v
core.config.load_config()
    |
    v
core.models.RouterConfig
    |
    +--> core.state.RouterState
    |       - 每个 provider 的 NodeStats
    |       - 请求计数器
    |       - 有上限的事件日志
    |       - 给只读消费者使用的 snapshot()
    |
    +--> core.router.make_app()
    |       - POST / JSON-RPC proxy
    |       - GET /healthz
    |       - 按策略选择上游
    |       - 有上限的故障转移和退避
    |
    +--> core.prober.prober_loop()
    |       - 周期性 eth_blockNumber 探测
    |       - 延迟和健康状态更新
    |
    +--> ui.dashboard.dashboard_loop()
            - 只读终端大盘
```

模块边界：

- `core/models.py`：只负责 schema。
- `core/config.py`：只负责 YAML 加载和校验。
- `core/state.py`：内存运行状态、锁和快照。
- `core/router.py`：请求路由、故障转移和 aiohttp 应用装配。
- `core/prober.py`：后台健康检查。
- `ui/dashboard.py`：终端渲染和刷新循环。

## 工程质量

实现重点是可复现、防御式行为和安全边界：

- Pydantic v2 模型拒绝未知配置字段。
- 校验错误不会被吞掉或替换成模糊错误。
- 运行时状态写入通过 `RouterState.transaction()` 和 `asyncio.Lock` 保护。
- TUI 使用深拷贝快照，不直接读写 live state。
- 上游请求集中在一个 `aiohttp.ClientSession`。
- 如果调用方没有注入 upstream client，应用会通过 aiohttp cleanup context 自己创建和关闭。
- prober 对单次 tick 的异常做隔离，一个坏节点不会停止健康检查。
- 测试使用 mocked upstream，不依赖真实公共 RPC 节点。
- 不写入持久化秘密、不引入数据库状态。

## 验证

所有命令都从仓库根目录运行：

```bash
python -m pytest -q --cov=core --cov=ui --cov-branch --cov-fail-under=100
ruff check core ui tests
mypy --strict core ui
```

当前验证结果：

```text
108 passed
Required test coverage of 100% reached. Total coverage: 100.00%
ruff: All checks passed
mypy: Success: no issues found
```

如果 Windows 沙箱环境无法写入默认用户临时目录，可以使用仓库内临时目录：

```bash
python -m pytest -q --basetemp=.pytest_tmp -o cache_dir=.pytest_cache_local \
  --cov=core --cov=ui --cov-branch --cov-fail-under=100
```

## 测试覆盖地图

| 测试文件 | 验证内容 |
|---|---|
| `tests/test_models.py` | schema 约束、枚举、URL 校验、重复值检查 |
| `tests/test_config.py` | YAML 加载、错误 YAML 传播、CLI 校验路径 |
| `tests/test_state.py` | 锁、快照、事件日志上限、TPS 计数、状态初始化 |
| `tests/test_router.py` | 路由策略、故障转移、退避、handler 错误、app 生命周期 |
| `tests/test_prober.py` | 探测成功/失败、loop 取消、异常隔离 |
| `tests/test_dashboard.py` | 渲染布局、大盘标签、日志、demo state、loop 退出 |
| `tests/test_integration.py` | 进程内启动路由器和真实本地 HTTP 请求 |

## 项目结构

```text
.
|-- core/
|   |-- __init__.py
|   |-- config.py       # YAML loader and config validation CLI
|   |-- models.py       # Pydantic configuration schema
|   |-- prober.py       # Background upstream health checks
|   |-- router.py       # aiohttp proxy, routing, failover, app entry point
|   `-- state.py        # In-memory state, locking, snapshots
|-- ui/
|   |-- __init__.py
|   `-- dashboard.py    # Rich terminal dashboard
|-- tests/
|   |-- conftest.py
|   |-- test_config.py
|   |-- test_dashboard.py
|   |-- test_integration.py
|   |-- test_models.py
|   |-- test_prober.py
|   |-- test_router.py
|   `-- test_state.py
|-- config.yaml
|-- pytest.ini
|-- requirements.txt
|-- README.md
`-- README_zh.md
```

## 依赖

运行时：

- `pydantic`
- `PyYAML`
- `aiohttp`
- `rich`

测试和质量工具：

- `pytest`
- `pytest-asyncio`
- `pytest-aiohttp`
- `pytest-cov`
- `aioresponses`
- `pytest-timeout`
- `ruff`
- `mypy`
- `types-PyYAML`

## 范围边界

包含：

- 本地 JSON-RPC 网关。
- mocked upstream 测试。
- 内存健康和流量状态。
- 终端大盘。
- 严格 schema 校验。

不包含：

- 公网部署。
- 认证或 API Key 管理。
- 持久化状态、SQLite、Redis 或外部数据库。
- 真实公网 RPC benchmark。
- WebSocket transport（`ws://` / `wss://`）和 `eth_subscribe` 代理。
- FastAPI、uvicorn、httpx、React、Vue 或浏览器 UI。

TODO:

- 增加完整 WebSocket 代理能力，支持 `ws://` 和 `wss://` 上游。
- 增加加权路由能力，并在真正使用时重新引入 `rpc_nodes[].weight`。
- 增加重试预算配置，并在真正生效时重新引入 `global.max_retries`。

## License

MIT
