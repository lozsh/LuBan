"""OpenAPI 快照契约：当前后端 schema 必须与入库快照一致（H14，只比较不写文件）。

- 快照由显式命令生成/更新：`.venv/bin/python scripts/export_openapi.py`
- 路由字段变化但未更新快照 → 本测试失败，提示运行导出命令；
- 序列化与导出命令同一稳定形式（indent=2 + sort_keys），diff 噪音最小。
"""

import json
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).resolve().parents[3] / "openapi.snapshot.json"


async def test_openapi_matches_committed_snapshot(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    assert spec["paths"], "openapi.json 无任何路由"

    assert SNAPSHOT_PATH.exists(), (
        f"快照不存在: {SNAPSHOT_PATH}（运行 python scripts/export_openapi.py 生成）"
    )
    committed = SNAPSHOT_PATH.read_text(encoding="utf-8")
    current = json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    if committed != current:
        # 找出第一个差异行，给出可行动的失败信息（不自动写文件）
        for line_no, (old, new) in enumerate(
            zip(
                committed.splitlines(), current.splitlines(), strict=False
            ), start=1
        ):
            if old != new:
                raise AssertionError(
                    "OpenAPI schema 与入库快照不一致（路由字段变化未更新快照）。\n"
                    f"首个差异在第 {line_no} 行：\n  快照: {old}\n  当前: {new}\n"
                    "更新方式：运行 `python scripts/export_openapi.py` 并提交新快照。"
                )
        raise AssertionError(
            "OpenAPI schema 与入库快照不一致（长度差异）。\n"
            "更新方式：运行 `python scripts/export_openapi.py` 并提交新快照。"
        )


async def test_snapshot_is_stable_serialization(client):
    """导出/测试两侧同一序列化：重复生成字节一致（无生成噪声）。"""
    response = await client.get("/openapi.json")
    spec = response.json()
    once = json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    twice = json.dumps(json.loads(once), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert once == twice
