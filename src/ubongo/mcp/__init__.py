"""The MCP channel (candidate 13, v0.1.4): Ubongo as an MCP server.

`service.py` is the channel-free core (imports no SDK; unit-testable offline);
`server.py` is the only module that imports the `mcp` SDK and is loaded lazily
by the `ubongo mcp` entrypoint, so a core install without the optional extra
never pays for it. The MCP client half (Ubongo consuming external servers like
Compendium) is v0.1.5, not this package.
"""
