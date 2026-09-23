from __future__ import annotations

import json
import logging
import math
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from src.smart_data.infrastructure.database.glossary_repo import default_glossary
from src.smart_data.infrastructure.mysql.engine import ensure_database, get_engine

logger = logging.getLogger(__name__)

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS metric_dictionary_version (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        version_code VARCHAR(64) NOT NULL UNIQUE,
        status VARCHAR(16) NOT NULL DEFAULT 'active',
        effective_at DATETIME NOT NULL,
        created_by VARCHAR(64) NOT NULL DEFAULT 'system',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS metric_dictionary_entry (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        version_id BIGINT NOT NULL,
        asset_type VARCHAR(64) NOT NULL DEFAULT 'GENERIC',
        business_name VARCHAR(128) NOT NULL,
        point_code VARCHAR(64) NOT NULL,
        measurement VARCHAR(128) NOT NULL,
        field_name VARCHAR(128) NOT NULL,
        unit VARCHAR(32) NULL,
        data_type VARCHAR(32) NOT NULL DEFAULT 'measured',
        allowed_aggregations VARCHAR(255) NOT NULL DEFAULT '["avg","max","min","count","sum"]',
        normal_range VARCHAR(64) NULL,
        enabled TINYINT NOT NULL DEFAULT 1,
        KEY idx_version_code (version_id, point_code),
        CONSTRAINT fk_metric_entry_version FOREIGN KEY (version_id)
            REFERENCES metric_dictionary_version(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS metric_alias (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        entry_id BIGINT NOT NULL,
        alias VARCHAR(128) NOT NULL,
        locale VARCHAR(16) NOT NULL DEFAULT 'zh-CN',
        priority INT NOT NULL DEFAULT 0,
        KEY idx_alias (alias, locale),
        CONSTRAINT fk_metric_alias_entry FOREIGN KEY (entry_id)
            REFERENCES metric_dictionary_entry(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS gt_telemetry (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        time DATETIME(3) NOT NULL,
        asset_id VARCHAR(32) NOT NULL,
        temperature DOUBLE NULL,
        speed DOUBLE NULL,
        power DOUBLE NULL,
        flow_rate DOUBLE NULL,
        vibration DOUBLE NULL,
        generator_efficiency DOUBLE NULL,
        thermal_efficiency DOUBLE NULL,
        KEY idx_asset_time (asset_id, time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS smart_query (
        query_id VARCHAR(64) PRIMARY KEY,
        trace_id VARCHAR(64) NOT NULL,
        conversation_id VARCHAR(64) NULL,
        user_id VARCHAR(64) NOT NULL,
        question TEXT NOT NULL,
        status VARCHAR(32) NOT NULL,
        dictionary_version VARCHAR(64) NULL,
        model_version VARCHAR(64) NULL,
        prompt_versions JSON NULL,
        generated_sql TEXT NULL,
        safe_sql TEXT NULL,
        result_summary JSON NULL,
        error_code VARCHAR(64) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at DATETIME NULL,
        KEY idx_user_created (user_id, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS smart_query_event (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        query_id VARCHAR(64) NOT NULL,
        seq INT NOT NULL,
        stage VARCHAR(64) NULL,
        event_type VARCHAR(64) NOT NULL,
        public_payload JSON NULL,
        audit_payload JSON NULL,
        elapsed_ms INT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_query_seq (query_id, seq)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]

SEED_METRICS = [
    {
        "point_code": "T48_AVG",
        "business_name": "平均排气温度",
        "measurement": "gt_telemetry",
        "field_name": "temperature",
        "unit": "℃",
        "aliases": ["排温", "排气温度", "透平排温", "t48", "t4_avg"],
    },
    {
        "point_code": "N2_SPEED",
        "business_name": "燃机转子转速",
        "measurement": "gt_telemetry",
        "field_name": "speed",
        "unit": "rpm",
        "aliases": ["转速", "燃机转速", "n2", "转子转速"],
    },
    {
        "point_code": "GEN_POWER",
        "business_name": "发电机有功功率",
        "measurement": "gt_telemetry",
        "field_name": "power",
        "unit": "MW",
        "aliases": ["发电功率", "功率", "有功功率", "输出功率", "负荷"],
    },
    {
        "point_code": "FUEL_FLOW",
        "business_name": "燃油质量流量",
        "measurement": "gt_telemetry",
        "field_name": "flow_rate",
        "unit": "kg/s",
        "aliases": ["燃油流量", "燃耗", "耗气量", "燃料流量"],
    },
    {
        "point_code": "VIB_MAX",
        "business_name": "轴承振动有效值",
        "measurement": "gt_telemetry",
        "field_name": "vibration",
        "unit": "μm",
        "aliases": ["振动", "轴振", "瓦振", "最大振动值"],
    },
    {
        "point_code": "GEN_EFF",
        "business_name": "发电效率",
        "measurement": "gt_telemetry",
        "field_name": "generator_efficiency",
        "unit": "%",
        "aliases": ["发电效率", "效率"],
    },
    {
        "point_code": "THERMAL_EFF",
        "business_name": "热效率",
        "measurement": "gt_telemetry",
        "field_name": "thermal_efficiency",
        "unit": "%",
        "aliases": ["热效率", "循环热效率", "效率"],
    },
]


def bootstrap_mysql() -> bool:
    """创建库表、写入字典与近 14 天样例时序，并刷新内存字典。"""
    try:
        ensure_database()
        engine = get_engine()
        with engine.begin() as conn:
            for stmt in SCHEMA_STATEMENTS:
                conn.execute(text(stmt))
            _seed_dictionary(conn)
            _seed_telemetry(conn)
        default_glossary.reload_from_mysql(engine)
        logger.info("MySQL gt_health 初始化完成，字典版本 %s", default_glossary.version)
        return True
    except Exception:
        logger.exception("MySQL 初始化失败，将回退 mock")
        return False


def _seed_dictionary(conn) -> None:
    count = conn.execute(text("SELECT COUNT(*) FROM metric_dictionary_entry")).scalar() or 0
    if count:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conn.execute(
        text(
            """
            INSERT INTO metric_dictionary_version (version_code, status, effective_at, created_by)
            VALUES (:code, 'active', :effective_at, 'system')
            """
        ),
        {"code": "dict-v20260901", "effective_at": now},
    )
    version_id = conn.execute(text("SELECT id FROM metric_dictionary_version WHERE version_code='dict-v20260901'")).scalar()
    for item in SEED_METRICS:
        conn.execute(
            text(
                """
                INSERT INTO metric_dictionary_entry
                    (version_id, asset_type, business_name, point_code, measurement, field_name, unit, data_type, allowed_aggregations, enabled)
                VALUES
                    (:version_id, 'GENERIC', :business_name, :point_code, :measurement, :field_name, :unit, 'measured', :aggs, 1)
                """
            ),
            {
                "version_id": version_id,
                "business_name": item["business_name"],
                "point_code": item["point_code"],
                "measurement": item["measurement"],
                "field_name": item["field_name"],
                "unit": item["unit"],
                "aggs": json.dumps(["avg", "max", "min", "count", "sum"]),
            },
        )
        entry_id = conn.execute(
            text("SELECT id FROM metric_dictionary_entry WHERE point_code=:code ORDER BY id DESC LIMIT 1"),
            {"code": item["point_code"]},
        ).scalar()
        aliases = [item["business_name"], item["point_code"], *item["aliases"]]
        for alias in aliases:
            conn.execute(
                text(
                    """
                    INSERT INTO metric_alias (entry_id, alias, locale, priority)
                    VALUES (:entry_id, :alias, 'zh-CN', 0)
                    """
                ),
                {"entry_id": entry_id, "alias": alias},
            )


def _seed_telemetry(conn) -> None:
    latest = conn.execute(text("SELECT MAX(time) FROM gt_telemetry")).scalar()
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    if latest and latest >= now - timedelta(hours=2):
        return

    start = now - timedelta(days=14)
    rng = random.Random(703001)
    rows = []
    current = start.replace(minute=0, second=0, microsecond=0)
    idx = 0
    while current <= now:
        for asset_id, phase in (("GT-001", 0.0), ("GT-002", 0.4)):
            rows.append(
                {
                    "time": current,
                    "asset_id": asset_id,
                    "temperature": round(520 + 15 * math.sin(idx * 0.2 + phase) + rng.uniform(-1, 1), 2),
                    "speed": round(9400 + 150 * math.sin(idx * 0.12 + phase) + rng.uniform(-8, 8), 2),
                    "power": round(26.5 + 2 * math.sin(idx * 0.15 + phase) + rng.uniform(-0.2, 0.2), 2),
                    "flow_rate": round(1.8 + 0.15 * math.sin(idx * 0.18 + phase) + rng.uniform(-0.02, 0.02), 3),
                    "vibration": round(18 + 3 * math.sin(idx * 0.3 + phase) + rng.uniform(-0.4, 0.4), 2),
                    "generator_efficiency": round(34 + 1.2 * math.sin(idx * 0.1 + phase) + rng.uniform(-0.1, 0.1), 2),
                    "thermal_efficiency": round(37 + 1.0 * math.sin(idx * 0.11 + phase) + rng.uniform(-0.1, 0.1), 2),
                }
            )
        current += timedelta(hours=1)
        idx += 1

    conn.execute(
        text(
            """
            INSERT INTO gt_telemetry
                (time, asset_id, temperature, speed, power, flow_rate, vibration, generator_efficiency, thermal_efficiency)
            VALUES
                (:time, :asset_id, :temperature, :speed, :power, :flow_rate, :vibration, :generator_efficiency, :thermal_efficiency)
            """
        ),
        rows,
    )
    logger.info("已写入 gt_telemetry 样例 %s 行", len(rows))
