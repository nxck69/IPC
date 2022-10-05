import asyncio
import secrets
import weakref
from asyncio import AbstractEventLoop
from typing import Any

import aiohttp

RequestPayload = dict[str, Any]


class Client:
    def __init__(
        self, secret_key: str, host: str = "localhost", port: int = 8765
    ) -> None:
        self.loop: AbstractEventLoop = asyncio.get_event_loop()
        self.secret_key: str = secret_key
        self.host: str = host
        self.port: int = port
        self._session: aiohttp.ClientSession | None = None
        self._websocket: aiohttp.ClientWebSocketResponse | None = None
        self._requests: weakref.WeakValueDictionary = (
            weakref.WeakValueDictionary()
        )
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._sender: asyncio.Task | None = None

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    async def init_sock(self) -> aiohttp.ClientWebSocketResponse:
        self._session = aiohttp.ClientSession()
        self._websocket = await self._session.ws_connect(
            self.url, autoping=False, autoclose=False
        )

        if self._worker is None:
            self._worker = self.loop.create_task(self._receive_requests())

        if self._sender is None:
            self._sender = self.loop.create_task(self._send_requests())

        return self._websocket

    async def request(
        self,
        endpoint: str,
        timeout: int | float | None = 15,
        default_value: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        if not self._session:
            await self.init_sock()

        nonce = secrets.token_urlsafe(22)  # 30 Character Long Unique Nonce

        payload = {
            "endpoint": endpoint,
            "data": kwargs,
            "headers": {"Authorization": self.secret_key},
            "nonce": nonce,
        }

        self._queue.put_nowait(payload)
        self._requests[nonce] = future = self.loop.create_future()
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            # Can add Reconnect Logic Here
            return default_value

    async def _send_requests(self) -> None:
        while True:
            payload: RequestPayload = await self._queue.get()
            await self._websocket.send_json(payload)

    async def _receive_requests(self) -> None:
        while True:
            recv = await self._websocket.receive()

            if recv.type == aiohttp.WSMsgType.PING:
                await self._websocket.ping()

            elif recv.type == aiohttp.WSMsgType.PONG:
                pass

            elif recv.type == aiohttp.WSMsgType.CLOSED:
                await self._session.close()
                await asyncio.sleep(5)
                await self.init_sock()

            else:
                response: RequestPayload = recv.json()
                nonce: str = response["nonce"]

                try:
                    future: asyncio.Future = self._requests[nonce]

                except KeyError:
                    pass

                else:
                    ret = response.get("response", response)
                    future.set_result(ret)

    async def close(self) -> None:
        await self._session.close()

        if self._worker is not None:
            self._worker.cancel()
            self._worker = None

        if self._sender is not None:
            self._sender.cancel()
            self._sender = None
