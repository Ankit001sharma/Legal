"""Launch the API gateway with HTTP/1.1 (uvicorn) or HTTP/2 over TLS (hypercorn)."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from legal_ai_platform.config import get_settings
from legal_ai_platform.gateway.app import app

logger = logging.getLogger(__name__)

_PLATFORM_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _resolve_ssl_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = _PLATFORM_ROOT / candidate
    return candidate.resolve()


def _build_hypercorn_config() -> "Config":
    from hypercorn.config import Config

    settings = get_settings()
    config = Config()
    config.bind = [f"{settings.platform_host}:{settings.platform_port}"]
    cert = _resolve_ssl_path(settings.platform_ssl_certfile)
    key = _resolve_ssl_path(settings.platform_ssl_keyfile)
    if not cert.is_file():
        raise FileNotFoundError(f"SSL certificate not found: {cert}")
    if not key.is_file():
        raise FileNotFoundError(f"SSL private key not found: {key}")
    config.certfile = str(cert)
    config.keyfile = str(key)
    config.alpn_protocols = ["h2", "http/1.1"]
    config.loglevel = settings.platform_log_level.lower()
    config.accesslog = "-"
    config.errorlog = "-"
    return config


def _run_hypercorn() -> None:
    from hypercorn.asyncio import serve

    settings = get_settings()
    config = _build_hypercorn_config()
    logger.info(
        "Starting gateway with HTTP/2 (TLS) on https://%s:%s",
        settings.platform_host,
        settings.platform_port,
    )
    asyncio.run(serve(app, config))


def _run_uvicorn() -> None:
    import uvicorn

    settings = get_settings()
    logger.info(
        "Starting gateway with HTTP/1.1 on http://%s:%s",
        settings.platform_host,
        settings.platform_port,
    )
    uvicorn.run(
        "legal_ai_platform.gateway.app:app",
        host=settings.platform_host,
        port=settings.platform_port,
        log_level=settings.platform_log_level.lower(),
    )


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.platform_log_level,
        format="%(levelname)s:     %(message)s",
    )
    if settings.platform_http2:
        if not settings.platform_ssl_certfile or not settings.platform_ssl_keyfile:
            logger.error(
                "PLATFORM_HTTP2=true requires PLATFORM_SSL_CERTFILE and "
                "PLATFORM_SSL_KEYFILE. Generate dev certs with:\n"
                "  python -m legal_ai_platform.scripts.generate_dev_cert "
                "--san IP:YOUR_LAN_IP"
            )
            sys.exit(1)
        try:
            _run_hypercorn()
        except FileNotFoundError as exc:
            logger.error("%s", exc)
            sys.exit(1)
    else:
        _run_uvicorn()


if __name__ == "__main__":
    main()
