FROM python:3.12-slim
WORKDIR /app
COPY mcp_server.py .
# Stdio MCP server: starts instantly, answers introspection, proxies one
# read-only call to the public JSON API. No keys, no state, stdlib only.
CMD ["python", "mcp_server.py"]
