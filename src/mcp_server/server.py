"""MCP server (SDK oficial `mcp` 2.x, MCPServer) sobre el registro de tools compartido.

Las tools son de solo lectura y lo declaran con ToolAnnotations (read_only_hint), para
que el cliente MCP pueda omitir la confirmacion. La logica vive en src/tools/registry.py:
el server solo adapta firmas y serializa.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from src.tools.registry import Tool, build_tools, to_json
from src.tools.sql_tool import ANCHOR_DATE, SCHEMA_PROMPT

INSTRUCTIONS = (
    "Inventario sintetico de Distribuidora Industrial Ficticia (200 SKUs, 3 CEDIS). "
    "Usa get_sku_status para un SKU concreto, query_inventory para preguntas analiticas y "
    "search_documents para politicas internas. Todas las tools son de solo lectura."
)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)


def build_server(tools: dict[str, Tool] | None = None) -> MCPServer:
    tools = tools if tools is not None else build_tools()
    server = MCPServer(name="inventory-copilot", instructions=INSTRUCTIONS)

    @server.tool(description=tools["query_inventory"].description, annotations=READ_ONLY)
    def query_inventory(question: str) -> str:
        return to_json(tools["query_inventory"].fn({"question": question}))

    @server.tool(description=tools["get_sku_status"].description, annotations=READ_ONLY)
    def get_sku_status(sku: str) -> str:
        return to_json(tools["get_sku_status"].fn({"sku": sku}))

    @server.tool(description=tools["search_documents"].description, annotations=READ_ONLY)
    def search_documents(query: str, k: int = 4) -> str:
        return to_json(tools["search_documents"].fn({"query": query, "k": k}))

    @server.resource("inventory://schema", name="schema", mime_type="text/plain",
                     description="Esquema de tablas consultables y convenciones de datos")
    def schema() -> str:
        return SCHEMA_PROMPT.format(anchor=ANCHOR_DATE.isoformat())

    return server
