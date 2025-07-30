import asyncio
from typing import Dict, Tuple, List, Optional
from uuid import UUID

from bleak import BleakScanner, BLEDevice, AdvertisementData


class BLEManager:
    def __init__(self):
        self.seen_devices: Dict[str, Tuple[BLEDevice, AdvertisementData]] = {}
        self._discover_task: Optional[asyncio.Task] = None
        self._bleak_scanner_instance: Optional[BleakScanner] = None


    @staticmethod
    def console_print(*values, sep=" ", end="\n> ", flush=True):
        print(f"\x1b[2K\r{sep.join(str(v) for v in values)}", end=end, flush=flush)

    def on_dev_discover(self, device: BLEDevice, adv: AdvertisementData):
        self.console_print(f"Discovered: {device.address}:", device, adv)
        self.seen_devices[device.address] = (device, adv)

    async def start_discover(self, service_uuids: Optional[List[str]] = None):
        if self._discover_task and not self._discover_task.done():
            self.console_print("* Already scanning. Please wait for the current scan to finish. *")
            return
        self._discover_task = asyncio.create_task(
            self._discover(service_uuids=service_uuids)
        )

    async def stop_discover(self):
        """Public method to stop an ongoing BLE scan."""
        if not self._discover_task or self._discover_task.done():
            self.console_print("* No active scan to stop. *")
            return

        self.console_print("* Requesting scan stop... *")
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

        self.console_print("* Scanning for devices... (Press /s to stop) *")

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
            self.console_print("\x1b[2K\r* Scan was cancelled. *", end='', flush=True)
        except Exception as e:
            # Catch any other unexpected errors during scan
            self.console_print(f"\x1b[2K\r* Scan error: {e} *", end='', flush=True)
        finally:
            # Ensure the scanner instance is stopped and cleared
            if self._bleak_scanner_instance:
                await self._bleak_scanner_instance.stop()
            self._bleak_scanner_instance = None
            self.console_print("\x1b[2K\r* Scanning stopped. *", end='', flush=True)

    async def handle_command(self, command: str):
        if not command:
            return
        if command == "/d":
            await self.start_discover()
        elif command.startswith("/d"):
            cmd, *args = command.split(" ")
            service_uuids = [str(UUID(arg)) for arg in args]
            await self.start_discover(service_uuids=service_uuids)
        elif command == "/s":
            await self.stop_discover()
        else:
            raise RuntimeError(f"Unknown command: {command}")

    async def input_loop(self):
        loop = asyncio.get_running_loop()
        self.console_print("ble2ws")
        while True:
            command = await loop.run_in_executor(None, input, )
            try:
                await self.handle_command(command)
            except Exception as e:
               self.console_print(f"! Error: {e}")
            await asyncio.sleep(0.1)



def main():
    manager = BLEManager()
    try:
        asyncio.run(manager.input_loop())
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        pass
    # "f47b5e2d-4a9e-4c5a-9b3f-8e1d2c3a4b5c"

if __name__ == "__main__":
    main()