from __future__ import annotations

import logging
import os
import re
from logging.handlers import WatchedFileHandler


DNS_ERROR_MARKERS = (
    "failed to resolve",
    "temporary failure in name resolution",
    "name or service not known",
    "nodename nor servname provided",
    "no address associated with hostname",
    "getaddrinfo failed",
    "name resolution",
    "dns",
)

CONNECTION_ERROR_MARKERS = (
    "failed to establish a new connection",
    "newconnectionerror",
    "connection aborted",
    "connection reset",
    "connection refused",
    "connection broken",
    "network is unreachable",
    "remote end closed connection",
    "remotedisconnected",
    "server disconnected",
    "remoteprotocolerror",
    "protocolerror",
    "readerror",
    "connecterror",
)

GATEWAY_ERROR_MARKERS = (
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "gateway time-out",
)

TLS_ERROR_MARKERS = (
    "proxyerror",
    "sslerror",
    "certificate verify failed",
    "tlsv1 alert",
)

HTTP_GATEWAY_STATUS_RX = re.compile(r"\b(?:502|503|504)\b")
TIMEOUT_ERROR_RX = re.compile(r"\btime[ -]?out\b")


def build_file_logger(
    name: str,
    *,
    log_file: str,
    log_format: str,
    level: int,
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    target_path = os.path.abspath(log_file)
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", "") == target_path:
            handler.setLevel(level)
            handler.setFormatter(logging.Formatter(log_format))
            return logger

    try:
        handler = WatchedFileHandler(log_file)
    except Exception:
        return logger

    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(log_format))
    logger.addHandler(handler)
    return logger


def _contains_any_marker(error_text: str, markers: tuple[str, ...]) -> bool:
    text = (error_text or "").lower()
    return bool(text) and any(marker in text for marker in markers)


def is_max_retries_exceeded_error(error_text: str) -> bool:
    return "max retries exceeded with url" in (error_text or "").lower()


def is_timeout_error(error_text: str) -> bool:
    text = (error_text or "").lower()
    if not text:
        return False
    markers = (
        "timed out",
        "read timeout",
        "connect timeout",
        "gateway timeout",
        "gateway time-out",
    )
    if any(marker in text for marker in markers):
        return True
    return bool(TIMEOUT_ERROR_RX.search(text))


def is_dns_error(error_text: str) -> bool:
    return _contains_any_marker(error_text, DNS_ERROR_MARKERS)


def is_connection_error(error_text: str) -> bool:
    return _contains_any_marker(error_text, CONNECTION_ERROR_MARKERS)


def is_gateway_error(error_text: str) -> bool:
    text = (error_text or "").lower()
    return _contains_any_marker(text, GATEWAY_ERROR_MARKERS) or bool(HTTP_GATEWAY_STATUS_RX.search(text))


def is_tls_error(error_text: str) -> bool:
    return _contains_any_marker(error_text, TLS_ERROR_MARKERS)


def describe_network_error(error_text: str) -> str:
    if is_max_retries_exceeded_error(error_text):
        return "max retries exceeded"
    if is_timeout_error(error_text):
        return "timeout"
    if is_dns_error(error_text):
        return "dns/namensaufloesung"
    if is_gateway_error(error_text):
        return "gateway"
    if is_tls_error(error_text):
        return "ssl/tls"
    if is_connection_error(error_text):
        return "verbindung"
    return "netzwerk"


def should_pause_on_network_error(error_text: str) -> bool:
    return any(
        (
            is_max_retries_exceeded_error(error_text),
            is_timeout_error(error_text),
            is_dns_error(error_text),
            is_connection_error(error_text),
            is_gateway_error(error_text),
            is_tls_error(error_text),
        )
    )
