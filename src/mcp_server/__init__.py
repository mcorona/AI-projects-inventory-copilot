"""MCP server de inventario: expone las mismas tools del agente a cualquier cliente MCP."""
from src.mcp_server.server import build_server

__all__ = ["build_server"]
