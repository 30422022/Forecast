# Public Release Manifest

本目录的发布白名单由 `scripts/release_guard.py` 固定维护。守卫检查目录内全部文件（含隐藏文件和 Git 忽略文件），任何白名单外文件、链接或重解析点都会失败。发布前仍应人工检查内容及许可证适用范围。

## 包含内容

- `src/gridcast_public/`：API 契约、Provider 接口、示例工作流、Mock Adapter、内存收据、加密持久化与协调器。
- `src/gridcast_public/backtest.py`：通用滚动回测框架；公开 API 只调用 Mock，按时间截点隔离训练与验证片段，仅返回误差指标，不打包或持久化观测序列。
- `src/gridcast_public/reference_models/`：独立编写、未训练的 PatchTST 与 FTMixer 思路参考实现。仅用于离线架构实验，不进入 API。
- `frontend/`：与公开 API 对接的 React/TypeScript 控制台，包含任务提交、Mock 曲线、滚动回测、协作轨迹、收据、状态与显式恢复页面。不包含原完整业务前端或私有 API 调用。回测示例由浏览器即时生成，不是仓库内数据集。
- `scripts/prune_runtime.py`：手动清理超期或超量的终态持久任务。清理按命令参数触发，不是自动 TTL 服务。
- `tests/`、`docs/`、CI 与 Docker 配置：验证和解释公开示例及模型边界。
- 根目录文档、许可证、依赖配置和安全说明。

## 明确排除

数据集与运行数据库、数据库备份、日志、缓存、虚拟环境、前端依赖目录与构建产物、连接凭证与密钥、已训练权重、MPWNet 和其他私有模型源码、上游原始模型代码、训练实现、市场规则库、私有优化逻辑、内部服务地址、原业务前端及过程计划文档均不属于发布内容。README 中 MPWNet 的说明仅为高层原理介绍，不表示模型已在公开版实现。指定目录中的 GridFormer 是图像复原模型，不属于电力负荷预测发布范围。

## 数据与恢复说明

内存模式使用有界的进程内收据，进程重启后清空。持久模式必须显式设置 `durable=true`，并在外部配置 `GRIDCAST_DATABASE_URL` 与 `GRIDCAST_CHECKPOINT_KEY`；持久检查点采用 AES-GCM 加密并提交到 PostgreSQL。调用 `/resume` 才会请求恢复。数据库中的有限任务元数据仍可观察。当前没有自动 TTL 清理；可人工调用 `scripts/prune_runtime.py` 清理终态任务。密钥丢失或不匹配时检查点不可恢复。

SHA-256 执行指纹只用于关联有限的执行元数据，不是数字签名，也不证明输入或结果未被修改。
