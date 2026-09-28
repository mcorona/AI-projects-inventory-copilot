"""MCP server (SDK oficial `mcp` 2.x, MCPServer) sobre el registro de tools compartido.

Las tools son de solo lectura y lo declaran con ToolAnnotations (read_only_hint), para
que el cliente MCP pueda omitir la confirmacion. La logica vive en src/tools/registry.py:
el server solo adapta firmas y serializa.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from src.guardrails.pipeline import GuardrailPipeline
from src.tools.registry import Tool, build_tools, to_json
from src.tools.sql_tool import ANCHOR_DATE, SCHEMA_PROMPT

INSTRUCTIONS = (
    "Inventario sintetico de Distribuidora Industrial Ficticia (200 SKUs, 3 CEDIS). "
    "Usa get_sku_status para un SKU concreto, query_inventory para preguntas analiticas y "
    "search_documents para politicas internas. Todas las tools son de solo lectura."
)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)


def build_server(tools: dict[str, Tool] | None = None,
                 guardrails: GuardrailPipeline | None = None) -> MCPServer:
    # solo lectura: un cliente MCP arbitrario no garantiza la confirmacion humana que exige
    # propose_purchase_order, asi que las acciones no se exponen por MCP
    tools = tools if tools is not None else build_tools(include_actions=False)
    guardrails = guardrails or GuardrailPipeline(actor="mcp")
    server = MCPServer(name="inventory-copilot", instructions=INSTRUCTIONS)

    def run(name: str, args: dict) -> str:
        # el cliente MCP recibe los mismos datos que el agente: se retiran instrucciones inyectadas
        result = tools[name].fn(args)
        if isinstance(result, dict):
            result.pop("_usage", None)   # telemetria interna, no para el cliente
        clean, _ = guardrails.sanitize_tool_result(name, result)
        return to_json(clean)

    @server.tool(description=tools["query_inventory"].description, annotations=READ_ONLY)
    def query_inventory(question: str) -> str:
        return run("query_inventory", {"question": question})

    @server.tool(description=tools["get_sku_status"].description, annotations=READ_ONLY)
    def get_sku_status(sku: str) -> str:
        return run("get_sku_status", {"sku": sku})

    @server.tool(description=tools["search_documents"].description, annotations=READ_ONLY)
    def search_documents(query: str, k: int = 4) -> str:
        return run("search_documents", {"query": query, "k": k})

    @server.resource("inventory://schema", name="schema", mime_type="text/plain",
                     description="Esquema de tablas consultables y convenciones de datos")
    def schema() -> str:
        return SCHEMA_PROMPT.format(anchor=ANCHOR_DATE.isoformat())

    return server
