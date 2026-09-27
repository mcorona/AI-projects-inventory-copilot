"""Arranca el MCP server por stdio: python -m src.mcp_server"""
from dotenv import load_dotenv

from src.mcp_server import build_server

load_dotenv()
build_server().run("stdio")
