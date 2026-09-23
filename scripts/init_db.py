"""初始化 gt_health 库表、指标字典与样例时序。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.smart_data.startup import startup_datasources


if __name__ == "__main__":
    backend = startup_datasources()
    print(f"query_backend={backend}")
