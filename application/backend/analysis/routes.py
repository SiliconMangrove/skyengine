"""
Analysis 路由：薄封装 AnalysisService 到 FastAPI。

注册：server.py 顶层 `app.include_router(analysis_router)`，与 history_router 同级，
不依赖工厂切换。

API 形态（详见 0701日志详细设计.md §3.3）：
  GET    /analysis/runs           列表（轻，仅 summary）
  GET    /analysis/runs/{run_id}  详情（重，含三流）
  POST   /analysis/runs           落盘（body = Run JSON）
  DELETE /analysis/runs/{run_id}  删除
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException, Query

from .service import AnalysisService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis", tags=["analysis"])

# 单例服务：所有请求共用同一个 archive_dir
_service = AnalysisService()


@router.get("/runs")
async def list_runs(
    factory_id: str | None = Query(None, description="按 factory_id 过滤"),
):
    """Run 列表（轻量，仅元信息 + summary，不含三流）。"""
    runs = _service.list_runs()
    if factory_id:
        runs = [r for r in runs if r.get("factory_id") == factory_id]
    return {"status": "ok", "runs": runs, "count": len(runs)}


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    """Run 详情（含三流 frames/metricsTimeline/events）。"""
    run = _service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return {"status": "ok", "run": run}


@router.post("/runs")
async def save_run(
    payload: dict = Body(...),
    source: str = Query("archive", pattern="^(archive|imported)$"),
):
    """落盘一个 Run。

    body = Run JSON（必须含 frames / metricsTimeline / events 三字段）。
    query:
      source=archive   本地产出（finalize 调用）
      source=imported  用户上传

    返回 {status, id, filename}。
    """
    try:
        result = _service.save_run(payload, source=source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", **result}


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str):
    """删除一个 Run 文件。"""
    ok = _service.delete_run(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Run not found or delete failed: {run_id}")
    return {"status": "ok", "id": run_id}
