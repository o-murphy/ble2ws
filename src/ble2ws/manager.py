import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Tuple, List, Optional, Any, NamedTuple
from uuid import UUID

import websockets
from bleak import BleakScanner, BLEDevice, AdvertisementData


logger = logging.getLogger(__name__)


class EventType(Enum):
    DISCOVERY = "discovery"
    STATUS = "status"
    ERROR = "error"
    UNKNOWN = "unknown"
    LOG = "log"



class Message(NamedTuple):
    event: EventType
    data: Any


class BLEManager:
    def __init__(self):
        self.seen_devices: Dict[str, Tuple[BLEDevice, AdvertisementData]] = {}
        self._discover_task: Optional[asyncio.Task] = None
        self._bleak_scanner_instance: Optional[BleakScanner] = None
        self.output_queue: asyncio.Queue[Message] = asyncio.Queue()

    def log_message(self, message: str, level: int = logging.INFO):
        asyncio.create_task(
            self.output_queue.put(
                Message(event=EventType.LOG, data=message)
            )
        )

    def on_dev_discover(self, device: BLEDevice, adv: AdvertisementData):
        self.log_message(f"Discovered: {device.address}: {device} {adv}")
        self.seen_devices[device.address] = (device, adv)
        local_name = adv.local_name if adv.local_name else "N/A"
        discovered_data = {
            "address": device.address,
            "name": device.name or "N/A",
            "local_name": local_name,
            "rssi": adv.rssi,
            "uuids": list(adv.service_uuids) if adv.service_uuids else [],
            "manufacturer_data": dict(adv.manufacturer_data) if adv.manufacturer_data else None
        }

        asyncio.create_task(
            self.output_queue.put(
                Message(event=EventType.DISCOVERY, data=discovered_data)
            )
        )


    async def start_discover(self, service_uuids: Optional[List[str]] = None):
        if self._discover_task and not self._discover_task.done():
            self.log_message(
                "Already scanning. Please wait for the current scan to finish.",
                logging.ERROR
            )
            return
        self._discover_task = asyncio.create_task(
            self._discover(service_uuids=service_uuids)
        )

    async def stop_discover(self):
        """Public method to stop an ongoing BLE scan."""
        if not self._discover_task or self._discover_task.done():
            self.log_message("No active scan to stop.", logging.ERROR)
            return

        self.log_message("Requesting scan stop...")
        self._discover_task.cancel() # Request cancellation of the scanning task
        try:
            # Await the task to ensure it completes its cancellation and cleanup
            # This is important for state consistency
            await self._discover_task
        except asyncio.CancelledError:
            # This is expected if we're awaiting a task we just cancelled
            pass
        finally:
            self._discover_task = None # Clear the task reference after it's done

    async def _discover(self, service_uuids: Optional[List[str]] = None):
        """
        Internal coroutine to manage the BleakScanner instance's lifecycle.
        This task can be cancelled externally.
        """
        self.seen_devices.clear()

        self.log_message("Scanning for devices... (Press /s to stop)")

        try:
            # Create a BleakScanner instance for full control
            self._bleak_scanner_instance = BleakScanner(
                detection_callback=self.on_dev_discover,
                service_uuids=service_uuids
            )
            await self._bleak_scanner_instance.start() # Start the scan

            # Keep the scan running indefinitely until cancelled or a specific timeout
            # For a manual stop via /s, we can just sleep indefinitely or until cancelled.
            # If you want it to also stop after N seconds automatically if not stopped manually:
            await asyncio.sleep(1000000) # Sleep for a very long time, effectively indefinitely
                                        # This sleep is what will be cancelled by self._discover_task.cancel()

        except asyncio.CancelledError:
            # This is the expected way to stop the scan via '/s' command
            self.log_message("Scan was cancelled.", logging.ERROR)
        except Exception as e:
            # Catch any other unexpected errors during scan
            self.log_message(f"Scan error: {e}", logging.ERROR)
        finally:
            # Ensure the scanner instance is stopped and cleared
            if self._bleak_scanner_instance:
                await self._bleak_scanner_instance.stop()
            self._bleak_scanner_instance = None
            self.log_message("Scanning stopped.")


# --- Console Input Loop ---
class ConsoleInterface:
    def __init__(self):
        self.manager = BLEManager() # Each console also gets its own manager
        self.console_output_task: Optional[asyncio.Task] = None
        logger.info("ConsoleInterface initialized with its own BLEManager.")

    @staticmethod
    def console_print(*values, sep=" ", end="\n> ", flush=True):
        print(f"\x1b[2K\r{sep.join(str(v) for v in values)}", end=end, flush=flush)

    async def _console_output_reader(self):
        """Reads from console_manager's output queue and prints to stdout."""
        while True:
            try:
                message = await self.manager.output_queue.get()
                if message.event is EventType.LOG:
                    self.console_print(f"* {message.data} *")
                if message.event is EventType.ERROR:
                    self.console_print(f"! Error: {message.data}")
                else:
                    self.console_print(message.data)

                # if message_type == "discovery":
                #     addr = data.get("address")
                #     name = data.get("name")
                #     rssi = data.get("rssi")
                #     print(f"Discovered: {addr} (Name: {name}, RSSI: {rssi})")
                # elif message_type == "status":
                #     print(f"Status: {data}")
                # elif message_type == "error":
                #     print(f"ERROR: {data}")
                # elif message_type == "list_results":
                #     print("--- Console Seen Devices ---")
                #     if not data:
                #         print("No devices seen yet.")
                #     else:
                #         for dev in data:
                #             print(f"  {dev['address']}: Name='{dev['name']}', RSSI={dev['rssi']}")
                #     print("----------------------------")
                # else:
                #     print(f"Raw Output: {message_dict}")
                #
                # print("> ", end="", flush=True) # Reprint prompt
                self.manager.output_queue.task_done() # Signal item processed
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in console output reader: {e}")
                break

    async def handle_command(self, command: str):
        if not command:
            return
        if command == "/d":
            await self.manager.start_discover()
        elif command.startswith("/d"):
            cmd, *args = command.split(" ")
            service_uuids = [str(UUID(arg)) for arg in args]
            await self.manager.start_discover(service_uuids=service_uuids)
        elif command == "/s":
            await self.manager.stop_discover()
        else:
            raise RuntimeError(f"Unknown command: {command}")

    async def start(self):
        """Starts the console input loop."""
        self.console_output_task = asyncio.create_task(self._console_output_reader())

        loop = asyncio.get_running_loop()
        self.console_print("ble2ws")
        while True:
            try:
                command = await loop.run_in_executor(None, input, "")
                if not command:
                    continue

                await self.handle_command(command)
            except asyncio.CancelledError:
                self.console_print("* Console input loop cancelled. Exiting. *")
            except Exception as e:
               self.console_print(f"! Error in console loop: {e}")
            await asyncio.sleep(0.1)

    async def stop(self):
        """Stops the console interface."""
        if self.console_output_task and not self.console_output_task.done():
            self.console_output_task.cancel()
            try:
                await self.console_output_task
            except asyncio.CancelledError:
                pass

        # Cancel any active scan of the console manager
        if self.manager.stop_discover():
            self.console_print("* Console input loop stopped. *")


# class BLEWebSocketServer:
#     def __init__(self, host: str, port: int):
#         self.host = host
#         self.port = port
#         self.server: Optional[websockets.WebSocketServer] = None
#         logger.info(f"BLEWebSocketServer initialized on {self.host}:{self.port}")



async def main():
    """Main entry point for the application, starting server and console."""
    # server = BLEWebSocketServer("0.0.0.0", 8765)
    console = ConsoleInterface()

    # server_task = asyncio.create_task(server.start())
    console_task = asyncio.create_task(console.start())

    try:
        # Wait for both tasks (or until cancelled)
        await asyncio.gather(
            # server_task,
            console_task
        )
    except asyncio.CancelledError:
        logger.info("Main application tasks cancelled.")
    finally:
        # Ensure proper shutdown of all components
        # await server.stop()
        await console.stop()
        logger.info("All background tasks shut down.")
    # "f47b5e2d-4a9e-4c5a-9b3f-8e1d2c3a4b5c"

def run():
    """Wrapper to run the main async function with KeyboardInterrupt handling."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application interrupted by user (Ctrl+C).")
    finally:
        logger.info("Application cleanup complete.")

if __name__ == "__main__":
    run()