"""Minimal health check HTTP server using raw asyncio."""

import asyncio
import logging

logger = logging.getLogger(__name__)

HEALTH_PORT = 8080

HTTP_200 = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: application/json\r\n"
    b"Content-Length: 15\r\n"
    b"Connection: close\r\n"
    b"\r\n"
    b'{"status":"ok"}'
)


async def _handle_connection(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    """Handle a single health check request."""
    try:
        await asyncio.wait_for(reader.readline(), timeout=5.0)
        writer.write(HTTP_200)
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def start_health_server(port: int = HEALTH_PORT) -> asyncio.Server:
    """Start the health check server. Returns the server object."""
    server = await asyncio.start_server(_handle_connection, "0.0.0.0", port)
    logger.info(f"Health server listening on port {port}")
    return server
