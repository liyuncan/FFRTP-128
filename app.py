"""FFRTP-128 双向串口文件传输图形界面。"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from serial.tools import list_ports
except ImportError:
    list_ports = None

try:
    from .ffrtp128 import DATA_SIZE, transfer
except ImportError:  # 兼容直接执行 python app.py
    from ffrtp128 import DATA_SIZE, transfer


class TransferApp(tk.Tk):
    """同一界面既能发送，也能接收。"""

    def __init__(self):
        super().__init__()
        self.title("FFRTP-128 双向串口文件传输工具")
        self.geometry("720x470")
        self.minsize(650, 420)
        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value="115200")
        self.file_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪")
        self._build_ui()
        self.refresh_ports()

    def _build_ui(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(5, weight=1)

        ttk.Label(root, text="串口").grid(row=0, column=0, sticky="w", pady=5)
        self.port_box = ttk.Combobox(root, textvariable=self.port_var, width=22)
        self.port_box.grid(row=0, column=1, sticky="ew", padx=8, pady=5)
        self.refresh_button = ttk.Button(root, text="刷新", command=self.refresh_ports)
        self.refresh_button.grid(row=0, column=2, pady=5)

        ttk.Label(root, text="波特率").grid(row=1, column=0, sticky="w", pady=5)
        self.baud_box = ttk.Combobox(
            root,
            textvariable=self.baud_var,
            values=("9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"),
        )
        self.baud_box.grid(row=1, column=1, sticky="ew", padx=8, pady=5)

        ttk.Label(root, text="发送文件").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(root, textvariable=self.file_var).grid(row=2, column=1, sticky="ew", padx=8, pady=5)
        self.select_button = ttk.Button(root, text="选择文件", command=self.select_file)
        self.select_button.grid(row=2, column=2, pady=5)

        buttons = ttk.Frame(root)
        buttons.grid(row=3, column=0, columnspan=3, pady=(14, 8))
        self.send_button = ttk.Button(buttons, text="发送文件", command=self.send_file)
        self.send_button.pack(side="left", padx=6)
        self.receive_button = ttk.Button(buttons, text="接收文件", command=self.receive_file)
        self.receive_button.pack(side="left", padx=6)

        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.grid(row=4, column=0, columnspan=3, sticky="ew", pady=8)

        self.log_text = tk.Text(root, height=13, state="disabled", wrap="word")
        self.log_text.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=5)

        ttk.Label(root, textvariable=self.status_var).grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(root, text="FFRTP-128 · 128字节数据区 · 135字节发送帧").grid(
            row=7, column=0, columnspan=3, sticky="e", pady=(4, 0)
        )

    def refresh_ports(self):
        """刷新串口列表，同时允许用户手动输入串口号。"""
        ports = [item.device for item in list_ports.comports()] if list_ports else []
        self.port_box["values"] = ports
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])

    def select_file(self):
        path = filedialog.askopenfilename(title="选择长度为128字节整数倍的文件")
        if path:
            self.file_var.set(path)

    def _connection(self):
        port = self.port_var.get().strip()
        if not port:
            raise ValueError("请选择或输入串口号")
        return port, int(self.baud_var.get())

    def send_file(self):
        try:
            port, baudrate = self._connection()
            path = Path(self.file_var.get())
            if not path.is_file():
                raise ValueError("请先选择发送文件")
            if path.stat().st_size % DATA_SIZE:
                raise ValueError("文件长度必须是128字节的整数倍")
        except Exception as exc:
            messagebox.showerror("无法发送", str(exc))
            return
        self._run("发送", lambda: self._send_worker(path, port, baudrate))

    def receive_file(self):
        try:
            port, baudrate = self._connection()
        except Exception as exc:
            messagebox.showerror("无法接收", str(exc))
            return
        path = filedialog.asksaveasfilename(title="保存接收到的文件", defaultextension=".bin")
        if path:
            self._run("接收", lambda: self._receive_worker(Path(path), port, baudrate))

    def _send_worker(self, path: Path, port: str, baudrate: int):
        data = path.read_bytes()
        result = transfer(data, port, baudrate)
        return f"发送成功：{result.size} 字节，{result.packets} 包，用时 {result.elapsed:.2f} 秒"

    def _receive_worker(self, path: Path, port: str, baudrate: int):
        data = bytearray()
        result = transfer(data, port, baudrate)
        path.write_bytes(data)
        return f"接收成功：{result.size} 字节，{result.packets} 包，已保存到 {path}"

    def _run(self, operation: str, worker):
        """在线程中执行串口操作，避免界面卡死。"""
        self._set_busy(True)
        self.status_var.set(f"正在{operation}……")
        self._log(f"开始{operation}")

        def task():
            try:
                message = worker()
                self.after(0, lambda: self._finish(True, message))
            except Exception as exc:
                self.after(0, lambda: self._finish(False, f"{operation}失败：{exc}"))

        threading.Thread(target=task, daemon=True).start()

    def _finish(self, success: bool, message: str):
        self._set_busy(False)
        self.status_var.set("完成" if success else "失败")
        self._log(message)
        (messagebox.showinfo if success else messagebox.showerror)("传输结果", message)

    def _set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        for widget in (self.send_button, self.receive_button, self.select_button, self.refresh_button):
            widget.configure(state=state)
        self.progress.start(12) if busy else self.progress.stop()

    def _log(self, text: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{datetime.now():%H:%M:%S}] {text}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


if __name__ == "__main__":
    TransferApp().mainloop()
