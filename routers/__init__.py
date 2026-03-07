"""
API路由模块
"""

from fastapi import APIRouter

from routers import upload, graph, history

# 创建主路由
api_router = APIRouter()

# 注册子路由
api_router.include_router(upload.router)
api_router.include_router(graph.router)
api_router.include_router(history.router)

__all__ = ['api_router']
