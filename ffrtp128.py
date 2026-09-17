"""FFRTP-128 固定帧可靠传输协议。

FFRTP 表示 Fixed-Frame Reliable Transfer Protocol；128 表示每个
SOH 数据帧承载 128 字节文件数据。发送方发出的 SOH、EOT 和 CAN
均为 135 字节完整帧。

公开接口只有 transfer(memory, port, baudrate)：
- memory 为 bytes：发送内存中的文件数据；
- memory 为 bytearray：接收文件并写入该内存。
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass

try:
    import serial
except ImportError:  # 允许在未安装 pyserial 时导入并测试帧算法
    serial = None


# 控制字节
SOH = 0x01
EOT = 0x04
ACK = 0x06
NAK = 0x15
CAN = 0x18
CRC_REQ = 0x43  # 字符 'C'

# FFRTP-128 固定协议参数
PROTOCOL_NAME = "FFRTP-128"
PROTOCOL_VERSION = "1.0"
DATA_SIZE = 128
FRAME_SIZE = 135
TIMEOUT_SECONDS = 1.0
MAX_RETRIES = 60
FINAL_IDLE_SECONDS = 2.2  # 超过两个重发周期没有重复 EOT，即可退出
_FRAME_TYPES = {SOH, EOT, CAN}
_FILL = bytes([0xFF]) * DATA_SIZE


class TransferError(RuntimeError):
    """传输失败。"""


@dataclass(frozen=True)
class TransferResult:
    """一次传输的结果。"""

    direction: str
    size: int
    packets: int
    elapsed: float


def crc16_ccitt(data: bytes, initial: int = 0xFFFF) -> int:
    """计算 CRC16-CCITT-FALSE：poly=0x1021，init=0xFFFF。"""
    crc = initial
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _build_frame(frame_type: int, sequence: int, payload: bytes = _FILL) -> bytes:
    """组装固定 135 字节帧；序号小端，CRC 高字节在前。"""
    if frame_type not in _FRAME_TYPES:
        raise ValueError("无效帧类型")
    if not 0 <= sequence <= 0xFFFFFFFF:
        raise ValueError("序号超出 uint32_t 范围")
    if len(payload) != DATA_SIZE:
        raise ValueError("数据区必须正好为 128 字节")

    body = bytes([frame_type]) + struct.pack("<I", sequence) + bytes(payload)
    return body + struct.pack(">H", crc16_ccitt(body))


def _parse_frame(frame: bytes):
    """验证并解析一帧；失败返回 None。"""
    if len(frame) != FRAME_SIZE or frame[0] not in _FRAME_TYPES:
        return None
    received_crc = struct.unpack(">H", frame[-2:])[0]
    if crc16_ccitt(frame[:-2]) != received_crc:
        return None
    return frame[0], struct.unpack("<I", frame[1:5])[0], frame[5:133]


def _read_exact(port, size: int, timeout: float) -> bytes:
    """在指定时间内读取固定长度数据。"""
    deadline = time.monotonic() + timeout
    data = bytearray()
    while len(data) < size and time.monotonic() < deadline:
        block = port.read(size - len(data))
        if block:
            data.extend(block)
    if len(data) != size:
        raise TimeoutError
    return bytes(data)


def _read_frame(port, timeout: float) -> bytes:
    """寻找帧头，再读取剩余 134 字节；无帧或残帧均超时。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        first = port.read(1)
        if first and first[0] in _FRAME_TYPES:
            return first + _read_exact(port, FRAME_SIZE - 1, TIMEOUT_SECONDS)
    raise TimeoutError


def _read_reply(port, timeout: float):
    """等待单字节应答，忽略无关字节。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = port.read(1)
        if value and value[0] in (ACK, NAK, CRC_REQ):
            return value[0]
    return None


def _write(port, data: bytes) -> None:
    """完整写入并等待串口发送缓存清空。"""
    if port.write(data) != len(data):
        raise TransferError("串口未完整写入数据")
    port.flush()


def _wait_for_request(port) -> None:
    """发送方等待接收方发出字符 C。"""
    for _ in range(MAX_RETRIES + 1):
        if _read_reply(port, TIMEOUT_SECONDS) == CRC_REQ:
            return
    raise TransferError("等待接收端请求超时")


def _send_frame(port, frame: bytes) -> None:
    """发送一帧，收到 ACK 成功，NAK 或超时则重发。"""
    for _ in range(MAX_RETRIES + 1):
        # 清除上一轮可能残留的 C、ACK 或 NAK，避免把旧应答用于新帧。
        port.reset_input_buffer()
        _write(port, frame)
        if _read_reply(port, TIMEOUT_SECONDS) == ACK:
            return
    raise TransferError("单帧重试超过 60 次")


def _send_memory(port, memory: bytes) -> TransferResult:
    """发送 bytes 中的数据。"""
    if len(memory) % DATA_SIZE:
        raise ValueError("文件长度必须是 128 字节的整数倍")

    packet_count = len(memory) // DATA_SIZE
    if packet_count > 0xFFFFFFFF:
        raise ValueError("文件包数超出 32 位序号范围")

    started = time.monotonic()
    _wait_for_request(port)
    try:
        for sequence in range(packet_count):
            offset = sequence * DATA_SIZE
            _send_frame(port, _build_frame(SOH, sequence, memory[offset:offset + DATA_SIZE]))

        # EOT 序号等于接收端的下一个期望序号。
        _send_frame(port, _build_frame(EOT, packet_count))
    except Exception:
        # CAN 也使用完整 135 字节帧，不等待应答。
        try:
            _write(port, _build_frame(CAN, min(packet_count, 0xFFFFFFFF)))
        except Exception:
            pass
        raise

    return TransferResult("send", len(memory), packet_count, time.monotonic() - started)


def _reply(port, value: int) -> None:
    """接收方发送单字节控制应答。"""
    _write(port, bytes([value]))


def _handle_data_frame(port, parsed, memory: bytearray, expected: int):
    """处理 SOH 数据帧，返回更新后的期望序号和是否开始传输。"""
    frame_type, sequence, payload = parsed
    if frame_type != SOH:
        return expected, False

    if sequence == expected:
        memory.extend(payload)
        _reply(port, ACK)
        return expected + 1, True

    if expected > 0 and sequence == expected - 1:
        # ACK 丢失引起的重复包：只重发 ACK，不重复写入。
        _reply(port, ACK)
        return expected, True

    _reply(port, NAK)
    return expected, False


def _linger_after_eot(port, final_sequence: int) -> None:
    """保持完成状态；重复 EOT 到来时重新 ACK，并延长静默等待。"""
    deadline = time.monotonic() + FINAL_IDLE_SECONDS
    while time.monotonic() < deadline:
        try:
            frame = _read_frame(port, max(0.01, deadline - time.monotonic()))
        except TimeoutError:
            break
        parsed = _parse_frame(frame)
        if parsed and parsed[0] == EOT and parsed[1] == final_sequence:
            _reply(port, ACK)
            deadline = time.monotonic() + FINAL_IDLE_SECONDS
        elif parsed and parsed[0] == CAN:
            break
        else:
            _reply(port, NAK)


def _receive_memory(port, memory: bytearray) -> TransferResult:
    """接收文件并覆盖写入 bytearray。"""
    memory.clear()
    expected = 0
    started = time.monotonic()

    # 启动阶段：每秒发送一次 C，最多重试 60 次。
    first_frame = None
    for _ in range(MAX_RETRIES + 1):
        _reply(port, CRC_REQ)
        try:
            first_frame = _read_frame(port, TIMEOUT_SECONDS)
            break
        except TimeoutError:
            continue
    if first_frame is None:
        raise TransferError("等待发送端数据超时")

    failures = 0
    frame = first_frame
    while True:
        parsed = _parse_frame(frame)
        if not parsed:
            _reply(port, NAK)
            failures += 1
        elif parsed[0] == CAN:
            raise TransferError("发送端取消传输")
        elif parsed[0] == EOT:
            if parsed[1] == expected:
                _reply(port, ACK)
                _linger_after_eot(port, expected)
                return TransferResult("receive", len(memory), expected, time.monotonic() - started)
            _reply(port, NAK)
            failures += 1
        else:
            expected, valid = _handle_data_frame(port, parsed, memory, expected)
            failures = 0 if valid else failures + 1
            if expected > 0xFFFFFFFF:
                raise TransferError("接收包数超出 32 位序号范围")

        if failures > MAX_RETRIES:
            raise TransferError("连续接收失败超过 60 次")

        try:
            frame = _read_frame(port, TIMEOUT_SECONDS)
        except TimeoutError:
            # 当前帧可能残缺，丢弃残留字节后要求发送端重发。
            port.reset_input_buffer()
            _reply(port, NAK)
            failures += 1
            if failures > MAX_RETRIES:
                raise TransferError("等待下一帧超时超过 60 次")
            continue


def transfer(memory, port: str, baudrate: int) -> TransferResult:
    """唯一公开入口。

    Args:
        memory: bytes 表示发送；bytearray 表示接收，原内容会被清空。
        port: 串口名，例如 Windows 的 ``COM3`` 或 Linux 的 ``/dev/ttyUSB0``。
        baudrate: 波特率，例如 115200 或 921600。
    """
    if serial is None:
        raise RuntimeError("缺少 pyserial，请执行：pip install pyserial")
    if not isinstance(memory, (bytes, bytearray)):
        raise TypeError("memory 必须是 bytes（发送）或 bytearray（接收）")

    with serial.Serial(
        port=port,
        baudrate=int(baudrate),
        bytesize=8,
        parity="N",
        stopbits=1,
        timeout=0.05,
        write_timeout=TIMEOUT_SECONDS,
    ) as serial_port:
        serial_port.reset_input_buffer()
        serial_port.reset_output_buffer()
        return _send_memory(serial_port, memory) if isinstance(memory, bytes) else _receive_memory(serial_port, memory)


__all__ = [
    "transfer",
    "TransferResult",
    "TransferError",
    "PROTOCOL_NAME",
    "PROTOCOL_VERSION",
]
