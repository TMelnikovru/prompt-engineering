"""Запуск asyncio, совместимый с psycopg на Windows.

На Windows asyncio по умолчанию использует ProactorEventLoop, а асинхронный psycopg
работает только с SelectorEventLoop. На Linux (сервер, Docker) ничего не меняется.
"""

import asyncio
import selectors
import sys


def selector_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


def run(coroutine):
    if sys.platform == "win32":
        return asyncio.run(coroutine, loop_factory=selector_loop)
    return asyncio.run(coroutine)
