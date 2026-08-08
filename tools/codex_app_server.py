"""Launch Codex app-server with the repository's Parts Bin MCP server configured."""

from __future__ import annotations

import os
import sys


def main() -> None:
    db_path = os.environ.get("PARTS_BIN_DB_PATH", "data/parts.db")
    config_path = os.environ.get("PARTS_BIN_CONFIG_PATH", "config.toml")
    codex = os.environ.get("PARTS_BIN_CODEX_BIN", "codex")
    mcp_command = json_toml_string(sys.executable)
    mcp_args = json_toml_array(["-m", "tools.mcp_server", db_path, config_path])
    args = [codex, "app-server", "--stdio",
            "-c", f"mcp_servers.parts_bin.command={mcp_command}",
            "-c", f"mcp_servers.parts_bin.args={mcp_args}",
            "-c", f"mcp_servers.parts_bin.cwd={json_toml_string(os.getcwd())}"]
    os.execvp(codex, args)


def json_toml_string(value: str) -> str:
    # TOML basic strings are sufficient here; escape the only characters that
    # can alter the value passed through Codex's -c parser.
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def json_toml_array(values: list[str]) -> str:
    return "[" + ",".join(json_toml_string(value) for value in values) + "]"


if __name__ == "__main__":
    main()
