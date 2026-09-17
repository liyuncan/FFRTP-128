import queue
import threading
import time
import unittest

import ffrtp128 as protocol


class MemorySerial:
    """用于测试的双向内存串口。"""

    def __init__(self):
        self.rx = queue.Queue()
        self.peer = None
        self.timeout = 0.005
        self.drop_acks = 0

    def connect(self, peer):
        self.peer = peer

    def write(self, data):
        if data == bytes([protocol.ACK]) and self.drop_acks:
            self.drop_acks -= 1
            return 1
        for value in data:
            self.peer.rx.put(value)
        return len(data)

    def read(self, size=1):
        data = bytearray()
        deadline = time.monotonic() + self.timeout
        while len(data) < size:
            try:
                data.append(self.rx.get(timeout=max(0.0001, deadline - time.monotonic())))
            except queue.Empty:
                break
        return bytes(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        while True:
            try:
                self.rx.get_nowait()
            except queue.Empty:
                return


class ProtocolTests(unittest.TestCase):
    def test_protocol_identity(self):
        self.assertEqual(protocol.PROTOCOL_NAME, "FFRTP-128")
        self.assertEqual(protocol.PROTOCOL_VERSION, "1.0")

    def test_crc_reference(self):
        self.assertEqual(protocol.crc16_ccitt(b"123456789"), 0x29B1)

    def test_frame(self):
        payload = bytes(range(128))
        frame = protocol._build_frame(protocol.SOH, 7, payload)
        self.assertEqual(len(frame), 135)
        self.assertEqual(protocol._parse_frame(frame), (protocol.SOH, 7, payload))

    def test_memory_transfer(self):
        self._run_transfer_test(bytes(range(128)) * 3)

    def test_lost_ack_does_not_duplicate_data(self):
        self._run_transfer_test(bytes(range(128)), drop_first_ack=True)

    def _run_transfer_test(self, source, drop_first_ack=False):
        old_timeout = protocol.TIMEOUT_SECONDS
        old_linger = protocol.FINAL_IDLE_SECONDS
        protocol.TIMEOUT_SECONDS = 0.05
        protocol.FINAL_IDLE_SECONDS = 0.08
        try:
            sender = MemorySerial()
            receiver = MemorySerial()
            sender.connect(receiver)
            receiver.connect(sender)
            receiver.drop_acks = 1 if drop_first_ack else 0
            target = bytearray()
            errors = []

            thread = threading.Thread(
                target=lambda: self._capture(errors, protocol._receive_memory, receiver, target)
            )
            thread.start()
            result = protocol._send_memory(sender, source)
            thread.join(timeout=2)

            self.assertFalse(thread.is_alive())
            self.assertFalse(errors)
            self.assertEqual(result.size, len(source))
            self.assertEqual(bytes(target), source)
        finally:
            protocol.TIMEOUT_SECONDS = old_timeout
            protocol.FINAL_IDLE_SECONDS = old_linger

    @staticmethod
    def _capture(errors, function, *args):
        try:
            function(*args)
        except Exception as exc:
            errors.append(exc)


if __name__ == "__main__":
    unittest.main()
