# FFRTP-128 固定帧可靠传输协议

FFRTP-128 是面向 UART 或 RS485 的轻量级停等文件传输协议。FFRTP 表示
Fixed-Frame Reliable Transfer Protocol，128 表示每个 SOH 数据帧承载
128 字节文件数据。发送方发出的 SOH、EOT、CAN 都是 135 字节完整帧。

完整协议见 [`docs/FFRTP-128_PROTOCOL.md`](docs/FFRTP-128_PROTOCOL.md)，带上位机和下位机流程图的 Word 版本位于 `docs` 目录。

## 安装和启动

Windows 11：

```powershell
py -m pip install -r requirements.txt
py app.py
```

Linux：

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```

发送文件的长度必须是128字节的整数倍。协议不传输文件名，接收端需要自行指定保存文件名。

## 协议模块唯一入口

```python
from ffrtp128 import transfer

# 发送：bytes 代表发送模式
source = open("firmware.bin", "rb").read()
result = transfer(source, "COM3", 115200)

# 接收：bytearray 代表接收模式，接收到的数据写入该内存
destination = bytearray()
result = transfer(destination, "COM3", 115200)
open("received.bin", "wb").write(destination)
```

同一模块可以运行在链路任意一端。传输时必须一端进入发送模式，另一端进入接收模式。

## 文件说明

- `ffrtp128.py`：FFRTP-128协议模块，外部只需调用 `transfer()`。
- `app.py`：Tkinter图形界面，支持选择文件、发送和接收。
- `requirements.txt`：仅依赖PySerial，Tkinter由常规Python安装提供。

## 测试

```bash
python -m unittest discover -s tests -v
```
