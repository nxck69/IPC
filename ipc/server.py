from asyncio import AbstractEventLoop
from dataclasses import dataclass
from hmac import compare_digest
from typing import Any, Coroutine, Callable, TypedDict

import aiohttp
from aiohttp import web, web_request

RequestPayloadData = dict[str, Any]


class RequestHeaders(TypedDict):
    Authorization: str


class RequestPayload(TypedDict):
    endpoint: str
    data: RequestPayloadData
    headers: RequestHeaders
    nonce: str


@dataclass(slots=True)
class Context:
    data: RequestPayloadData
    endpoint: str


def route(
    name: str | None = None,
) -> Callable[[Coroutine[Any, Any, Any]], Coroutine[Any, Any, Any]]:
    def decorator(func: Coroutine[Any, Any, Any]) -> Coroutine[Any, Any, Any]:
        if not name:
            Server.ROUTES[func.__name__] = func

        else:
            Server.ROUTES[name] = func

        return func

    return decorator


class Server:
    __slots__ = (
        "bot",
        "loop",
        "secret_key",
        "host",
        "port",
        "_server",
        "endpoints",
    )

    ROUTES = {}

    def __init__(
        self,
        bot: Any,
        secret_key: str,
        host: str = "localhost",
        port: int = 8765,
    ) -> None:
        self.bot: Any = bot
        self.loop: AbstractEventLoop = bot.loop
        self.secret_key: str = secret_key
        self.host: str = host
        self.port: int = port
        self._server: web.Application | None = None
        self.endpoints = {}

    def update_endpoints(self) -> None:
        self.endpoints = {**self.endpoints, **self.ROUTES}
        self.ROUTES.clear()

    async def handle_accept(self, request: web_request.Request) -> None:
        self.update_endpoints()
        websocket = aiohttp.web.WebSocketResponse()
        await websocket.prepare(request)

        async for message in websocket:
            request: RequestPayload = message.json()  # type: ignore
            endpoint = request.get("endpoint")
            headers = request.get("headers")
            nonce = request.get("nonce")

            if not headers or not compare_digest(
                headers.get("Authorization"), self.secret_key
            ):
                response = {
                    "nonce": nonce,
                    "response": {
                        "error": "Invalid or no token provided.",
                        "code": 401,
                    },
                }

            else:
                if not endpoint or endpoint not in self.endpoints:
                    response = {
                        "nonce": nonce,
                        "response": {
                            "error": "Invalid or no endpoint given.",
                            "code": 400,
                        },
                    }

                else:
                    server_response = Context(
                        data=request["data"], endpoint=endpoint
                    )
                    arguments = (server_response,)

                    try:
                        ret = await self.endpoints[endpoint](*arguments)

                    except Exception as error:
                        response = {
                            "nonce": nonce,
                            "response": {
                                "error": "IPC route raised error of type {}".format(
                                    type(error).__name__
                                ),
                                "code": 500,
                            },
                        }

                    else:
                        response = {"nonce": nonce, "response": ret}

            try:
                await websocket.send_json(response)

            except TypeError as error:
                if str(error).startswith("Object of type") and str(
                    error
                ).endswith("is not JSON serializable"):
                    error_response = (
                        "IPC route returned values which are not able to be sent over sockets."
                        " If you are trying to send a discord.py object,"
                        " please only send the data you need."
                    )

                    response = {
                        "nonce": nonce,
                        "response": {"error": error_response, "code": 500},
                    }

                    await websocket.send_json(response)

    async def __start(self, application: web.Application, port: int) -> None:
        runner = aiohttp.web.AppRunner(application)
        await runner.setup()

        site = aiohttp.web.TCPSite(runner, self.host, port)
        await site.start()

    def start(self) -> None:
        self._server = aiohttp.web.Application()
        self._server.router.add_route("GET", "/", self.handle_accept)
        self.loop.run_until_complete(self.__start(self._server, self.port))
