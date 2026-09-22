# 智能提问系统 (Intelligent-Questioning-System)

面向燃气轮机健康管理系统的时序数据智能问数（Text2SQL / NL2SQL）后端微服务。

本服务基于 **FastAPI + 确定性管道调度架构** 构建，连接本地大模型推理服务、MySQL 业务指标字典与 InfluxDB v3 时序数据库，严格实现系统设计方案 3.4.3 节规定的**六步处理链路**，并通过 Server-Sent Events (SSE) 向前端提供实时、透明的流式事件状态与结构化分析结果。

---

## 核心特性

1. **确定性六步流水线**：
   - `intent_parsing`（意图解析）：提取操作类型、资产对象（机组编号）、业务指标词与时间窗口；
   - `semantic_mapping`（语义映射）：基于版本化指标字典将口语化别名映射为测点编码，并严格核验机组数据权限；
   - `sql_generation`（SQL 生成）：面向 InfluxDB v3（Apache Arrow DataFusion SQL 方言）生成结构化只读查询；
   - `security_validation`（安全网关）：基于 `sqlglot` 进行静态 AST 语法树分析，100% 阻断 DDL/DML 写操作，强制注入时间窗口与行数保护（LIMIT 1000）；
   - `data_query`（数据查询）：只读查询时序数据，支持自适应离线热力学物理特征仿真；
   - `trend_analysis`（趋势分析）：计算样本数、均值、极值、标准差、变异系数等统计量，输出严格图表 DSL 与自然语言解读。
2. **先澄清后执行**：
   - 当机组缺失、指标存在多重候选（如“效率”对应发电效率/热效率）或时间模糊（如“最近”）时，主动中断推流并下发澄清提问，坚决不进行无依据猜测。
3. **安全与开源合规**：
   - 依赖项采用 MIT/Apache-2.0 友好协议，避免 AGPL 传染风险；
   - 彻底关闭未经批准的外网依赖与遥测，支持在银河麒麟 V10 + 华为鲲鹏 ARM64 环境下全离线部署。

---

## 目录结构

```text
├── configs/                       # 配置文件目录
│   ├── application.yaml           # 服务基础配置 (端口、数据库连接、超时)
│   └── policies.yaml              # 安全与查询限额策略
├── prompts/                       # 提示词版本化管理
│   ├── intent/v1.md               # 意图抽取 Prompt
│   ├── sql_generation/v1.md       # InfluxDB v3 SQL 生成 Prompt
│   └── conclusion/v1.md           # 趋势解读 Prompt
├── src/smart_data/                # 核心源代码
│   ├── main.py                    # FastAPI 启动入口与路由挂载
│   ├── config.py                  # Pydantic Settings 配置映射
│   ├── domain/                    # 核心领域实体
│   │   ├── intent.py              # QueryIntent, TimeRange
│   │   ├── query.py               # QueryState, MetricRef
│   │   └── result.py              # ChartDSL, MetricStatistics
│   ├── pipeline/                  # 管道与事件推流
│   │   ├── reporter.py            # StageReporter 异步有界队列
│   │   ├── sse.py                 # SSE 协议事件帧序列化器
│   │   ├── runner.py              # 六步状态机流水线调度器
│   │   └── components/            # 六步核心算子组件
│   │       ├── intent_parser.py   # 阶段 1：确定性时间与实体抽取
│   │       ├── semantic_mapper.py # 阶段 2：指标字典映射与权限校验
│   │       ├── sql_generator.py   # 阶段 3：时序 SQL 结构化生成
│   │       ├── sql_guard.py       # 阶段 4：sqlglot AST 静态安全解析
│   │       ├── security_validator.py
│   │       ├── influx_query.py    # 阶段 5：时序查询驱动
│   │       └── trend_analyzer.py  # 阶段 6：统计量计算与结论归纳
│   ├── infrastructure/            # 基础设施适配层
│   │   └── database/glossary_repo.py # 燃气轮机业务指标字典仓储
│   └── api/                       # API 接入层
│       └── v1/
│           ├── query.py           # POST /api/v1/nl2sql/query (SSE)
│           └── health.py          # GET /health/live, /health/ready
├── tests/                         # 自动化测试套件
│   ├── security/test_sql_guard.py # SQL 注入与安全防护测试
│   ├── unit/                      # 核心组件单元测试
│   └── contract/test_api_sse.py   # OpenAPI 与 SSE 契约流式测试
├── pyproject.toml                 # 项目元数据与依赖定义
├── requirements.lock              # 依赖精确锁定文件
└── README.md                      # 项目说明文档
```

---

## 快速上手与运行指引

### 1. 环境准备 (基于 uv)

本项目使用 `uv` 进行快速依赖安装与虚拟环境管理：

```bash
# 1. 创建本地专属虚拟环境
uv venv .venv

# 2. 安装核心依赖
uv pip install -e ".[dev]"
```

### 2. 启动服务

```bash
# 激活环境并启动 FastAPI 服务
.venv/Scripts/python.exe -m uvicorn src.smart_data.main:app --host 0.0.0.0 --port 8080 --reload
```

服务启动后访问：
- **Swagger API 文档**：`http://localhost:8080/docs`
- **ReDoc 文档**：`http://localhost:8080/redoc`
- **存活探针**：`GET http://localhost:8080/api/v1/health/live`
- **就绪探针**：`GET http://localhost:8080/api/v1/health/ready`

### 3. 测试查询接口 (SSE 流式输出)

```bash
curl -N -X POST "http://localhost:8080/api/v1/nl2sql/query" \
     -H "Content-Type: application/json" \
     -H "Accept: text/event-stream" \
     -d '{
       "question": "帮我查询 GT-001 今天的平均排气温度",
       "timezone": "Asia/Shanghai"
     }'
```

返回事件流将依次推送：
1. `id: 1, event: query.accepted`
2. `id: 2, event: stage.started, stage: intent_parsing`
3. `id: 3, event: stage.completed, stage: intent_parsing`
4. `...`
5. `id: 14, event: result.completed, stage: trend_analysis`（包含 SQL、统计量、图表 DSL 与自然语言结论）
6. `id: 15, event: stream.end`

---

## 运行自动化测试

```bash
.venv/Scripts/python.exe -m pytest -v
```

测试覆盖范围：
- **SQL 安全防护**：100% 阻断 `DROP`、`DELETE`、`INSERT`、`UPDATE`、多语句堆叠、`SELECT *` 通配符，自动收敛大 LIMIT；
- **自然语言解析**：机组抽取、指标提取、自然相对时间（“今天”、“上周”、“过去24小时”）转精确 UTC 起止时间戳；
- **先澄清后执行**：信息不全或指标歧义时返回标准化澄清选择题；
- **SSE 契约完整性**：验证阶段单调递增性与流式返回完整性。
