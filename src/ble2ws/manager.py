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
    INFO = "log"


class Message(NamedTuple):
    event: EventType
    data: Any


class BLEManager:
    def __init__(self):
        self.seen_devices: Dict[str, Tuple[BLEDevice, AdvertisementData]] = {}
        self._discover_task: Optional[asyncio.Task] = None
        self._bleak_scanner_instance: Optional[BleakScanner] = None
        self.output_queue: asyncio.Queue[Message] = asyncio.Queue()

    def message(self, message: str, event: EventType = EventType.INFO):
        asyncio.create_task(self.output_queue.put(Message(event=event, data=message)))

    def on_dev_discover(self, device: BLEDevice, adv: AdvertisementData):
        self.message(f"Discovered: {device.address}: {device} {adv}")
        self.seen_devices[device.address] = (device, adv)
        local_name = adv.local_name if adv.local_name else "N/A"
        discovered_data = {
            "address": device.address,
            "name": device.name or "N/A",
            "local_name": local_name,
            "rssi": adv.rssi,
            "uuids": list(adv.service_uuids) if adv.service_uuids else [],
            "manufacturer_data": dict(adv.manufacturer_data)
            if adv.manufacturer_data
            else None,
        }

        asyncio.create_task(
            self.output_queue.put(
                Message(event=EventType.DISCOVERY, data=discovered_data)
            )
        )

    async def start_discover(self, service_uuids: Optional[List[str]] = None):
        if self._discover_task and not self._discover_task.done():
            self.message(
                "Already scanning. Please wait for the current scan to finish.",
                EventType.ERROR,
            )
            return
        self._discover_task = asyncio.create_task(
            self._discover(service_uuids=service_uuids)
        )

    async def stop_discover(self):
        """Public method to stop an ongoing BLE scan."""
        if not self._discover_task or self._discover_task.done():
            self.message("No active scan to stop.", EventType.ERROR)
            return

        self.message("Requesting scan stop...")
        self._discover_task.cancel()  # Request cancellation of the scanning task
        try:
            # Await the task to ensure it completes its cancellation and cleanup
            # This is important for state consistency
            await self._discover_task
        except asyncio.CancelledError:
            # This is expected if we're awaiting a task we just cancelled
            pass
        finally:
            self._discover_task = None  # Clear the task reference after it's done

    async def _discover(self, service_uuids: Optional[List[str]] = None):
        """
        Internal coroutine to manage the BleakScanner instance's lifecycle.
        This task can be cancelled externally.
        """
        self.seen_devices.clear()

        self.message("Scanning for devices... (Press /s to stop)")

        try:
            # Create a BleakScanner instance for full control
            self._bleak_scanner_instance = BleakScanner(
                detection_callback=self.on_dev_discover, service_uuids=service_uuids
            )
            await self._bleak_scanner_instance.start()  # Start the scan

            # Keep the scan running indefinitely until cancelled or a specific timeout
            # For a manual stop via /s, we can just sleep indefinitely or until cancelled.
            # If you want it to also stop after N seconds automatically if not stopped manually:
            await asyncio.sleep(
                1000000
            )  # Sleep for a very long time, effectively indefinitely
        # This sleep is what will be cancelled by self._discover_task.cancel()

        except asyncio.CancelledError:
            # This is the expected way to stop the scan via '/s' command
            self.message("Scan was cancelled.")
        except Exception as e:
            # Catch any other unexpected errors during scan
            self.message(f"Scan error: {e}", EventType.ERROR)
        finally:
            # Ensure the scanner instance is stopped and cleared
            if self._bleak_scanner_instance:
                await self._bleak_scanner_instance.stop()
            self._bleak_scanner_instance = None
            self.message("Scanning stopped.")


# --- Console Input Loop ---
class ConsoleInterface:
    def __init__(self):
        self.manager = BLEManager()  # Each console also gets its own manager
        self.console_output_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        logger.info("ConsoleInterface initialized with its own BLEManager.")

    @staticmethod
    def console_print(*values, sep=" ", end="\n> ", flush=True):
        print(f"\x1b[2K\r{sep.join(str(v) for v in values)}", end=end, flush=flush)

    def _format_console_output(self, message: Message) -> str:
        """Formats a Message object for console printing."""
        if message.event is EventType.INFO:
            return f"* {message.data} *"
        elif message.event is EventType.ERROR:
            return f"! Error: {message.data}"
        elif message.event is EventType.DISCOVERY:
            data = message.data
            return (f"Discovered: {data.get('address', 'N/A')} "
                    f"(Name: {data.get('name', 'N/A')}, "
                    f"Local Name: {data.get('local_name', 'N/A')}, "
                    f"RSSI: {data.get('rssi', 'N/A')})")
        elif message.event is EventType.STATUS:
             return f"Status: {message.data}"
        else:
            return str(message.data)

    async def _get_input(self) -> str:
        """Helper to get input from console."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, input, "")

    async def _console_output_reader(self):
        """Reads from console_manager's output queue and prints to stdout."""
        while self._shutdown_event.is_set():
            try:
                message = await asyncio.wait_for(self.manager.output_queue.get(), timeout=0.1)
                self.console_print(self._format_console_output(message)) # Print without final newline
                self.manager.output_queue.task_done()  # Signal item processed
            except asyncio.TimeoutError:
                # No message for a while, just re-check shutdown event
                pass
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
        self.console_print("-- ble2ws --")
        while True:
            try:
                command = await loop.run_in_executor(None, input, "")
                if not command:
                    continue

                await self.handle_command(command)
            except asyncio.CancelledError:
                logger.info("Console input loop cancelled. Exiting.")
                self._shutdown_event.set()
                break
            except Exception as e:
                logger.error(f"! Error in console loop: {e}")
            await asyncio.sleep(0.1)

        logger.info("Console input loop stopped.")

    async def stop(self):
        """Stops the console interface."""
        if self.console_output_task and not self.console_output_task.done():
            self.console_output_task.cancel()
            try:
                await self.console_output_task
            except asyncio.CancelledError:
                pass

        # Cancel any active scan of the console manager
        await self.manager.stop_discover()
        logger.info("Console input loop stopped.")


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
    logging.basicConfig(level=logging.DEBUG)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application interrupted by user (Ctrl+C).")
    finally:
        logger.info("Application cleanup complete.")
        print("Exiting...")


if __name__ == "__main__":
    run()
