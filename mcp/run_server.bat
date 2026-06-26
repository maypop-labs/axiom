@echo off
REM AXIOM MCP server launcher.
REM
REM Activates the shared Python venv at E:\bin\axiom\Python\venv and runs
REM the MCP server over stdio. Designed to be invoked by Claude Desktop
REM via its mcpServers config; can also be run from a terminal for
REM smoke-testing (server will block waiting for MCP protocol on stdin).
REM
REM This script intentionally produces no stdout output of its own;
REM stdout is reserved for the MCP protocol. All status messages go to
REM stderr via the Python logging module.

call "%~dp0..\Python\venv\Scripts\activate.bat"
python "%~dp0server.py" %*
