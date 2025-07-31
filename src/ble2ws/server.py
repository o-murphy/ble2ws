import asyncio
import json
import logging
from typing import Optional, Dict, Tuple, List, Any

import websockets
from bleak import BleakScanner, BLEDevice, AdvertisementData
from websockets import ServerConnection

from ble2ws.jsonify import (
    dictify,
    jsonify,
    PeripheralDict,
    EventMessageWithData,
    MessageType,
    dejsonify,
)

_logger = logging.getLogger(__name__)


class BleakSession:
    def __init__(self, websocket):
        self.websocket = websocket
        self.scanner: Optional[BleakScanner] = None
        self.discovered_devices: Dict[
            str, Tuple[BLEDevice, Optional[AdvertisementData]]
        ] = {}
        _logger.info(f"New session initialised for {websocket.remote_address}")

    async def send_message(self, message: EventMessageWithData):
        await self.websocket.send(jsonify(message))

    async def _detection_callback(
        self, device: BLEDevice, advertisement_data: AdvertisementData
    ):
        self.discovered_devices[device.address] = (device, advertisement_data)
        try:
            peripheral: PeripheralDict = dictify(device, advertisement_data, None)
            message: EventMessageWithData = {
                "type": MessageType.DID_DISCOVER_PERIPHERAL,
                "data": peripheral,
            }
            await self.send_message(message)
        except websockets.exceptions.ConnectionClosedOK:
            _logger.warning(f"Connection closed during data sending {device.address}")
        except Exception as e:
            _logger.error(f"Error during data sending: {e}")
            message: EventMessageWithData = {
                "uid": None,
                "type": MessageType.DID_DISCOVER_PERIPHERAL,
                "error": f"Error during data sending: {e}",
            }
            await self.send_message(message)

    async def start_scan(self, timeout: float = 5.0, **kwargs):
        """
        Запускає сканування для цієї сесії.
        """
        if self.scanner:
            _logger.warning(f"Scanner already running {self.websocket.remote_address}")
            message: EventMessageWithData = {
                "uid": kwargs.pop("uid", None),
                "type": MessageType.START_SCAN,
                "error": "Scanning already running",
            }
            await self.send_message(message)
            return

        _logger.info(f"Perform scanning {self.websocket.remote_address}")
        self.discovered_devices.clear()
        # setup detection_callback
        self.scanner = BleakScanner(
            detection_callback=self._detection_callback, **kwargs
        )
        try:
            await self.scanner.start()
            # NOTE: possibly can be timeouted
            message: EventMessageWithData = {
                "uid": kwargs.pop("uid", None),
                "type": MessageType.START_SCAN,
                "message": "Scanning start success",
            }
            await self.send_message(message)
        except Exception as e:
            _logger.error(
                f"Scanning start error for {self.websocket.remote_address}: {e}"
            )
            self.scanner = None  # Очищуємо сканер у разі помилки
            message: EventMessageWithData = {
                "uid": kwargs.pop("uid", None),
                "type": MessageType.START_SCAN,
                "error": f"Scanning start error: {e}",
            }
            await self.send_message(message)

    async def stop_scan(self, **kwargs):
        if self.scanner:
            _logger.info(f"Scanning stop for {self.websocket.remote_address}")
            await self.scanner.stop()
            self.scanner = None
            message: EventMessageWithData = {
                "uid": kwargs.pop("uid", None),
                "type": MessageType.STOP_SCAN,
                "message": "Scanning stop success",
            }
        else:
            _logger.warning(
                f"Scanning is not running for {self.websocket.remote_address}"
            )
            message: EventMessageWithData = {
                "uid": kwargs.pop("uid", None),
                "type": MessageType.STOP_SCAN,
                "error": "Scanning is not running",
            }
        await self.send_message(message)

    async def discover(self, timeout: float = 10.0, return_adv: bool = False, **kwargs):
        _logger.info(f"Discover for {self.websocket.remote_address}")

        if self.scanner:
            _logger.warning(f"Scanner already running {self.websocket.remote_address}")
            message: EventMessageWithData = {
                "type": MessageType.START_SCAN,
                "error": "Scanning already running",
            }
            await self.send_message(message)
            return

        peripherals: List[PeripheralDict]
        self.discovered_devices.clear()
        try:
            if return_adv:
                self.discovered_devices = await BleakScanner.discover(
                    timeout=timeout, return_adv=True, **kwargs
                )
                peripherals = [
                    dictify(dev, adv)
                    for _, (dev, adv) in self.discovered_devices.items()
                ]
            else:
                devices = await BleakScanner.discover(
                    timeout=timeout, return_adv=False, **kwargs
                )
                for device in devices:
                    self.discovered_devices[device.address] = (device, None)
                peripherals: List[PeripheralDict] = [dictify(dev) for dev in devices]

            message: EventMessageWithData = {
                "uid": kwargs.pop("uid", None),
                "type": MessageType.DISCOVER,
                "data": peripherals,
            }
            await self.send_message(message)
        except Exception as e:
            _logger.error(f"Discover error for {self.websocket.remote_address}: {e}")
            message: EventMessageWithData = {
                "uid": kwargs.pop("uid", None),
                "type": MessageType.DISCOVER,
                "error": f"Discover error: {e}",
            }
            await self.send_message(message)

    async def find_device_by_address(
        self, device_identifier: str, timeout: float = 10.0, **kwargs
    ) -> None:
        _logger.info(
            f"Lookup address: {device_identifier} for {self.websocket.remote_address}"
        )

        self.discovered_devices.clear()
        try:
            device = await BleakScanner.find_device_by_name(
                device_identifier, timeout=timeout
            )
            if device:
                self.discovered_devices[device.address] = (device, None)
                peripheral: PeripheralDict = dictify(device)
                message: EventMessageWithData = {
                    "uid": kwargs.pop("uid", None),
                    "message": f"Found device: {peripheral}",
                    "type": MessageType.FIND_DEVICE_BY_ADDRESS,
                    "data": peripheral,
                }
                await self.send_message(message)
            else:
                _logger.error(
                    f"Device {device_identifier} not found for {self.websocket.remote_address}"
                )
                message: EventMessageWithData = {
                    "uid": kwargs.pop("uid", None),
                    "type": MessageType.FIND_DEVICE_BY_ADDRESS,
                    "error": f"Device {device_identifier} not found",
                }
                await self.send_message(message)
        except Exception as e:
            _logger.error(
                f"Device {device_identifier} lookup error for {self.websocket.remote_address}: {e}"
            )
            message: EventMessageWithData = {
                "uid": kwargs.pop("uid", None),
                "type": MessageType.FIND_DEVICE_BY_ADDRESS,
                "error": f"Device {device_identifier} error: {e}",
            }
            await self.send_message(message)

    async def find_device_by_name(
        self, name: str, timeout: float = 10.0, **kwargs
    ) -> None:
        _logger.info(f"Lookup name: {name} for {self.websocket.remote_address}")

        self.discovered_devices.clear()
        try:
            device = await BleakScanner.find_device_by_name(
                name, timeout=timeout, **kwargs
            )
            if device:
                self.discovered_devices[device.address] = (device, None)
                peripheral: PeripheralDict = dictify(device)
                message: EventMessageWithData = {
                    "uid": kwargs.pop("uid", None),
                    "type": MessageType.FIND_DEVICE_BY_NAME,
                    "data": peripheral,
                }
                await self.send_message(message)
            else:
                _logger.error(
                    f"Device {name} not found for {self.websocket.remote_address}"
                )
                message: EventMessageWithData = {
                    "uid": kwargs.pop("uid", None),
                    "type": MessageType.FIND_DEVICE_BY_NAME,
                    "error": f"Device {name} not found",
                }
                await self.send_message(message)
        except Exception as e:
            _logger.error(
                f"Device {name} lookup error for {self.websocket.remote_address}: {e}"
            )
            message: EventMessageWithData = {
                "uid": kwargs.pop("uid", None),
                "type": MessageType.FIND_DEVICE_BY_NAME,
                "error": f"Device {name} lookup error: {e}",
            }
            await self.send_message(message)

    async def cleanup(self):
        _logger.info(f"Session cleanup for {self.websocket.remote_address}")
        if self.scanner:
            await self.scanner.stop()
            self.scanner = None
        self.discovered_devices.clear()


class Server:
    def __init__(self):
        self.active_sessions: Dict[Any, BleakSession] = {}

    async def _reply(self, websocket, message: EventMessageWithData):
        await websocket.send(jsonify(message))

    async def websocket_handler(self, websocket: ServerConnection):
        reply: EventMessageWithData
        session = BleakSession(websocket)
        self.active_sessions[websocket] = session

        try:
            async for message in websocket:
                try:
                    msg_kwargs = dejsonify(message)
                    msg_type = MessageType(msg_kwargs.pop("type", None))
                except json.JSONDecodeError:
                    _logger.error(
                        f"Message format error {websocket.remote_address}: {message}"
                    )
                    reply = {
                        "type": MessageType.INPUT_ERROR,
                        "error": "Message format error",
                    }
                    await self._reply(websocket, reply)
                    return
                except Exception as e:
                    _logger.error(
                        f"Message type error for {websocket.remote_address}: {e}"
                    )
                    reply = {
                        "type": MessageType.INPUT_ERROR,
                        "error": f"Message type error: {e}",
                    }
                    await self._reply(websocket, reply)
                    return

                _logger.info(
                    f"Received message '{msg_type}' from {websocket.remote_address}"
                )

                try:
                    if msg_type is MessageType.START_SCAN:
                        await session.start_scan(**msg_kwargs)
                    elif msg_type is MessageType.STOP_SCAN:
                        await session.stop_scan(**msg_kwargs)
                    elif msg_type is MessageType.DISCOVER:
                        await session.discover(**msg_kwargs)
                    elif msg_type is MessageType.FIND_DEVICE_BY_NAME:
                        name = msg_kwargs.pop("name")
                        timeout = msg_kwargs.pop("timeout", 10.0)
                        if name:
                            await session.find_device_by_name(
                                name, timeout=timeout, **msg_kwargs
                            )
                        else:
                            reply = {
                                "uid": msg_kwargs.pop("uid", None),
                                "type": msg_type,
                                "error": "Name field required for FIND_DEVICE_BY_NAME",
                            }
                            await self._reply(websocket, reply)
                    elif msg_type is MessageType.FIND_DEVICE_BY_ADDRESS:
                        address = msg_kwargs.pop("address")
                        timeout = msg_kwargs.pop("timeout", 10.0)
                        if address:
                            await session.find_device_by_address(
                                address, timeout=timeout, **msg_kwargs
                            )
                        else:
                            reply = {
                                "uid": msg_kwargs.pop("uid", None),
                                "type": msg_type,
                                "error": "Address field required for FIND_DEVICE_BY_ADDRESS",
                            }
                            await self._reply(websocket, reply)

                except Exception as e:
                    _logger.error(
                        f"Undefined error in messages handler from {websocket.remote_address}: {e}"
                    )
                    reply = {
                        "uid": msg_kwargs.pop("uid", None),
                        "type": msg_type,
                        "error": f"Undefined error: {e}",
                    }
                    await self._reply(websocket, reply)

        except websockets.exceptions.ConnectionClosedOK:
            _logger.info(f"Connection closed {websocket.remote_address}")
        except Exception as e:
            _logger.error(
                f"Undefined error in websocket handler {websocket.remote_address}: {e}"
            )
        finally:
            await session.cleanup()
            del self.active_sessions[websocket]


async def run():
    _logger.info("Starting WebSocket on ws://localhost:8765")
    server = Server()
    async with websockets.serve(server.websocket_handler, "localhost", 8765):
        await asyncio.Future()


def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
