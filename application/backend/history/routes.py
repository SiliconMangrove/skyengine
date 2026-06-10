"""
历史运行记录 API 路由
"""

from fastapi import APIRouter, Query, Body
from .manager import HistoryManager

router = APIRouter(prefix="/history", tags=["history"])
manager = HistoryManager()


@router.get("/runs")
async def list_runs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    factory_type: str = Query(None),
):
    """获取运行记录列表"""
    runs = manager.list_runs(limit=limit, offset=offset, factory_type=factory_type)
    return {"status": "ok", "runs": runs, "count": len(runs)}


@router.get("/run/{run_id}")
async def get_run(run_id: str):
    """获取单次运行详情"""
    run = manager.get_run(run_id)
    if not run:
        return {"status": "error", "message": f"运行记录不存在: {run_id}"}
    return {"status": "ok", "data": run}


@router.get("/run/{run_id}/logs")
async def get_run_logs(
    run_id: str,
    event_type: str = Query(None),
    limit: int = Query(100, ge=1, le=1000),
):
    """获取运行日志"""
    logs = manager.get_run_logs(run_id, event_type=event_type, limit=limit)
    return {"status": "ok", "logs": logs, "count": len(logs)}


@router.post("/compare")
async def compare_runs(body: dict = Body(...)):
    """对比多次运行"""
    run_ids = body.get("run_ids", [])
    if len(run_ids) < 2:
        return {"status": "error", "message": "至少需要 2 个运行 ID"}
    result = manager.compare_runs(run_ids)
    return {"status": "ok", "data": result}


@router.post("/run/{run_id}/complete")
async def complete_run(run_id: str, body: dict = Body(default={})):
    """手动完成一次运行记录"""
    manager.complete_run(run_id, metrics_summary=body.get("metrics_summary"))
    return {"status": "ok", "message": f"运行 {run_id} 已完成"}


@router.delete("/run/{run_id}")
async def delete_run(run_id: str):
    """删除运行记录"""
    manager.delete_run(run_id)
    return {"status": "ok", "message": f"运行 {run_id} 已删除"}


@router.get("/stats")
async def history_stats():
    """历史统计摘要"""
    stats = manager.get_stats()
    return {"status": "ok", **stats}
