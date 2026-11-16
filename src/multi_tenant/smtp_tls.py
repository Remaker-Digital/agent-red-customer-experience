# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""One SMTP connection path. The certificate and host name are always checked.

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from email.message import Message

logger = logging.getLogger(__name__)


@contextmanager
def open_verified_smtp(
    host: str,
    port: int,
    *,
    timeout: float | None = None,
    username: str = "",
    password: str = "",
) -> Iterator[smtplib.SMTP]:
    """Open SMTP with a verified TLS context.

    Port 465 uses implicit TLS. Any other port except 25 uses STARTTLS.
    A username or password on port 25 is refused before a socket is opened,
    because that port has no TLS. A certificate failure is logged and raised.
    The caller does not get an unverified connection instead.
    """
    if port == 25 and (username or password):
        logger.warning("Refusing SMTP login on port 25 without TLS: host=%s", host)
        raise smtplib.SMTPException(
            "SMTP login on port 25 is not sent because the connection is not TLS"
        )

    context = ssl.create_default_context()
    server: smtplib.SMTP | None = None
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
            server.ehlo()
            if port != 25:
                server.starttls(context=context)
        yield server
    except ssl.SSLError:
        logger.warning(
            "SMTP certificate check failed: host=%s port=%s",
            host,
            port,
            exc_info=True,
        )
        raise
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                try:
                    server.close()
                except Exception:
                    pass


def send_verified_smtp(
    message: Message,
    host: str,
    port: int,
    *,
    timeout: float | None = None,
    username: str = "",
    password: str = "",
) -> None:
    """Send one message over ``open_verified_smtp``.

    It logs in only when both a username and a password are set, as each
    sender did before this helper existed. Errors are raised to the caller,
    which decides whether to try another provider.
    """
    with open_verified_smtp(host, port, timeout=timeout, username=username, password=password) as server:
        if username and password:
            server.login(username, password)
        server.send_message(message)
