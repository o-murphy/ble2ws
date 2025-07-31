from enum import IntEnum

from bleak import (
    BleakClient,
    BLEDevice,
    AdvertisementData,
    BleakGATTServiceCollection,
    BleakGATTCharacteristic,
    BleakGATTDescriptor,
)
from bleak.assigned_numbers import CHARACTERISTIC_PROPERTIES, CharacteristicPropertyName
import json
from binascii import hexlify
from typing import TypedDict, Dict, List, Optional, Literal, Tuple, Any

from bleak.backends.service import BleakGATTService

CHARACTERISTIC_PROPERTIES_TO_INT: dict[CharacteristicPropertyName, int] = {
    v: k for k, v in CHARACTERISTIC_PROPERTIES.items()
}


class DescriptorDict(TypedDict):
    handle: int
    uuid: str
    description: str


class CharacteristicDict(TypedDict):
    handle: int
    uuid: str
    description: str
    properties: int
    max_write_without_response_size: int
    descriptors: Dict[str, DescriptorDict]


class ServiceDict(TypedDict):
    handle: int
    uuid: str
    description: str
    characteristics: Dict[str, CharacteristicDict]


class ManufacturerData(TypedDict):
    company_id: Optional[int]
    payload: Optional[bytes]


class AdvertisementDataDict(TypedDict):
    local_name: Optional[str]
    manufacturer_data: ManufacturerData
    service_uuids: List[str]
    service_data: Dict[str, bytes]
    tx_power: Optional[int]
    rssi: int


class PeripheralDict(TypedDict):
    address: str
    name: Optional[str]
    state: Literal[0, 1, 2]
    mtu_size: Optional[int]
    advertisement_data: Optional[AdvertisementDataDict]
    services: Optional[Dict[str, ServiceDict]]


def _characteristic_property_names_to_int(
    props: List[CharacteristicPropertyName],
) -> int:
    v = 0
    for p in props:
        v |= CHARACTERISTIC_PROPERTIES_TO_INT.get(p, 0)
    return v


def _bleak_gatt_descriptor_to_dict(desc: BleakGATTDescriptor) -> DescriptorDict:
    return {
        "handle": desc.handle,
        "uuid": desc.uuid,
        "description": desc.description,
    }


def _bleak_gatt_characteristic_to_dict(
    char: BleakGATTCharacteristic,
) -> CharacteristicDict:
    return {
        "handle": char.handle,
        "uuid": char.uuid,
        "description": char.description,
        "properties": _characteristic_property_names_to_int(char.properties),
        "max_write_without_response_size": char.max_write_without_response_size,
        "descriptors": {
            d.uuid: _bleak_gatt_descriptor_to_dict(d) for d in char.descriptors
        },
    }


def _bleak_gatt_service_to_dict(service: BleakGATTService) -> ServiceDict:
    return {
        "handle": service.handle,
        "uuid": service.uuid,
        "description": service.description,
        "characteristics": {
            ch.uuid: _bleak_gatt_characteristic_to_dict(ch)
            for ch in service.characteristics
        },
    }


def _bleak_gatt_service_collection_to_dict(
    collection: BleakGATTServiceCollection,
) -> Dict[str, ServiceDict]:
    return {serv.uuid: _bleak_gatt_service_to_dict(serv) for serv in collection}


def _advertisement_data_to_dict(adv: AdvertisementData) -> AdvertisementDataDict:
    def _resolve_manufacturer_data(
        adv_: AdvertisementData,
    ) -> Tuple[Optional[int], Optional[bytes]]:
        manufacturer_data = adv_.manufacturer_data
        if manufacturer_data:
            return next(iter(manufacturer_data.items()))
        return None, None

    def _resolve_service_data(
        adv_: AdvertisementData,
    ) -> Tuple[List[str], Dict[str, bytes]]:
        data: Dict[str, bytes] = adv_.service_data
        for uuid in adv.service_uuids:
            if uuid not in data:
                data[uuid] = b""
        uuids: List[str] = list(data.keys())
        return uuids, data

    company_id, payload = _resolve_manufacturer_data(adv)
    service_uuids, service_data = _resolve_service_data(adv)

    return {
        "local_name": adv.local_name,
        "manufacturer_data": {
            "company_id": company_id if company_id else None,
            "payload": payload,
        },
        "service_uuids": service_uuids,
        "service_data": service_data,
        "tx_power": adv.tx_power,
        "rssi": adv.rssi,
    }


class MessageType(IntEnum):
    INPUT_ERROR = -1

    # requests
    START_SCAN = 1
    STOP_SCAN = 2
    DISCOVER = 3
    FIND_DEVICE_BY_NAME = 4
    FIND_DEVICE_BY_ADDRESS = 5

    CONNECT = 6
    DISCONNECT = 7
    PAIR = 8
    UNPAIR = 9
    SERVICES = 10

    DISCOVER_SERVICES = 11
    DISCOVER_CHARACTERISTICS = 12
    DISCOVER_DESCRIPTORS = 13

    READ_GATT_CHAR = 14
    WRITE_GATT_CHAR = 15
    START_NOTIFY = 16
    STOP_NOTIFY = 17
    READ_GATT_DESCRIPTOR = 18
    WRITE_GATT_DESCRIPTOR = 19

    # events
    DID_DISCOVER_PERIPHERAL = 20
    DID_CONNECT_PERIPHERAL = 21
    DID_DISCONNECT_PERIPHERAL = 22
    DID_NOTIFY_STATE_CHANGE = 23


class EventMessage(TypedDict):
    type: MessageType


class EventMessageWithData(EventMessage, total=False):
    data: Any
    uid: Optional[int]
    message: Optional[str]
    error: Optional[str]

    address: Optional[str]
    name: Optional[str]


def dictify(
    dev: BLEDevice, adv: Optional[AdvertisementData] = None, client: BleakClient = None
) -> PeripheralDict:
    ble_device_dict: PeripheralDict = {
        "address": dev.address,
        "name": dev.name,
        "state": 1 if client and client.is_connected else 0,
        "mtu_size": client.mtu_size if client else None,
        "advertisement_data": _advertisement_data_to_dict(adv) if adv else None,
        "services": _bleak_gatt_service_collection_to_dict(client.services)
        if client
        else None,
    }
    return ble_device_dict


def jsonify(obj: object) -> str:
    def _default(obj_):
        if isinstance(obj_, (bytes, bytearray)):
            return hexlify(obj_).decode("ascii")
        return "Unsupported(%r)" % obj_

    return json.dumps(obj, indent=2, default=_default)


def dejsonify(obj: str) -> Dict:
    message = json.loads(obj)
    # there will be parser
    parsed_message = message
    # there will be parser
    return parsed_message
