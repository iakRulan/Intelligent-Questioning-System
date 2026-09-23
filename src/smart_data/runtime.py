"""运行期解析后的查询后端：mock / mysql / influx。"""

query_backend = "mock"


def set_query_backend(name: str) -> None:
    global query_backend
    query_backend = name
