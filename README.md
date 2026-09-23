# 智能提问系统 (Intelligent-Questioning-System)

面向燃气轮机健康管理系统的时序数据智能问数（Text2SQL / NL2SQL）后端微服务。

本服务基于 **FastAPI + 确定性六步管道** 构建。默认 `pipeline.mode=auto`：启动时自动接入 MySQL（指标字典 + `gt_telemetry` 时序）和 InfluxDB v3；都不可用时回退 mock。自动化测试固定走 mock。

---

## 核心特性

1. **确定性六步流水线**：
   - `intent_parsing`：抽取操作类型、机组、指标与时间窗口；
   - `semantic_mapping`：版本化指标字典映射，核验机组权限；
   - `sql_generation`：生成 InfluxDB v3 只读 SQL；
   - `security_validation`：sqlglot AST 拦截写操作、注释、`SELECT *`、未知函数，并强制时间窗 / LIMIT / 授权机组；
   - `data_query`：开发阶段输出确定性模拟时序数据；
   - `trend_analysis`：统计量、图表 DSL 与区分事实/说明的结论。
2. **先澄清后执行**：机组、指标或时间不完整时下发 `clarification.required` 并结束本轮 SSE；客户端携带 `clarification_id` 与答案重新提交。
3. **数据库接入**：MySQL 库 `gt_health` 存放指标字典、审计与样例时序；InfluxDB v3 通过 HTTP SQL（`/api/v3/query_sql`）只读查询。
4. **DeepSeek Flash**：意图解析、SQL 生成、趋势结论默认调用 `https://api.deepseek.com/v1` 的 `deepseek-flash`；失败时回退确定性规则，SQL 仍必须经过安全网关。

---

## 快速上手

```bash
uv venv .venv
uv pip install -e ".[dev]"

.venv/Scripts/python.exe -m uvicorn src.smart_data.main:app --host 0.0.0.0 --port 8080 --reload
```

- Swagger：`http://localhost:8080/docs`
- 存活探针：`GET /health/live` 或 `GET /api/v1/health/live`
- 就绪探针：`GET /health/ready` 或 `GET /api/v1/health/ready`

```bash
curl -N -X POST "http://localhost:8080/api/v1/nl2sql/query" \
     -H "Content-Type: application/json" \
     -H "Accept: text/event-stream" \
     -d '{
       "question": "帮我查询 GT-001 今天的平均排气温度",
       "timezone": "Asia/Shanghai"
     }'
```

首次启动会自动创建 `gt_health` 库表并写入近 14 天 GT-001 / GT-002 样例时序。也可手动初始化：

```bash
.venv/Scripts/python.exe scripts/init_db.py
```

开发环境可用 `Authorization: Bearer dev:user_default:GT-001,GT-002` 指定用户与机组权限域。未传令牌时默认拥有 GT-001、GT-002。

---

## 运行测试

```bash
.venv/Scripts/python.exe -m pytest -v
```

覆盖 SQL 安全拦截、自然语言解析、澄清闭环、空结果终态与 SSE 契约。
