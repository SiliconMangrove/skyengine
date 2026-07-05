"""Analysis 模块：离线分析 Run 的后端持久化与查询。

工厂无关的通用服务。StaticFactory / 容器化通路共用同一个 Run 仓库
（默认 dataset/run/）。

路由注册：server.py 顶层 `app.include_router(analysis_router)`。
"""

from .service import AnalysisService
from .routes import router as analysis_router

__all__ = ["AnalysisService", "analysis_router"]
