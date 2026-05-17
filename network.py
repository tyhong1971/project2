# -*- coding: utf-8 -*-
"""
网络通信模块
TCP 控制通道 + UDP 数据通道
"""

import socket
import threading
import queue
from protocol import pack_message, MessageReceiver


class TCPTransport:
    """TCP 传输层 - 用于控制信令"""

    def __init__(self):
        self._socket = None
        self._receiver = MessageReceiver()
        self._recv_callback = None
        self._recv_thread = None
        self._running = False

    @property
    def socket(self):
        return self._socket

    def connect(self, host: str, port: int, timeout=5):
        """作为客户端连接到服务器"""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.settimeout(timeout)
        self._socket.connect((host, port))
        self._socket.settimeout(None)
        self._running = True
        self._start_recv()

    def bind_and_listen(self, port: int, backlog=50):
        """作为服务器绑定端口并监听"""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("0.0.0.0", port))
        self._socket.listen(backlog)
        self._running = True

    def accept(self):
        """接受新连接，返回 (client_socket, addr)"""
        return self._socket.accept()

    def send(self, msg_type: str, payload: dict = None):
        """发送控制消息"""
        if self._socket:
            try:
                data = pack_message(msg_type, payload)
                self._socket.sendall(data)
            except Exception as e:
                print(f"[TCP发送错误] {e}")

    def on_receive(self, callback):
        """设置消息接收回调 callback(msg_type, payload)"""
        self._recv_callback = callback

    def _start_recv(self):
        """启动接收线程"""
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

    def _recv_loop(self):
        """接收循环"""
        while self._running and self._socket:
            try:
                data = self._socket.recv(65536)
                if not data:
                    break
                self._receiver.feed(data)
                for msg_type, payload in self._receiver.extract_messages():
                    if self._recv_callback:
                        self._recv_callback(msg_type, payload)
            except Exception as e:
                if self._running:
                    print(f"[TCP接收错误] {e}")
                break

    def close(self):
        """关闭连接"""
        self._running = False
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass


class UDPSender:
    """UDP 发送器 - 用于屏幕数据传输"""

    def __init__(self):
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
        print("[UDPSender] 已初始化")

    def send_frame(self, data: bytes, host: str, port: int):
        """发送一帧数据（自动分片）"""
        CHUNK_SIZE = 60000
        total_chunks = (len(data) + CHUNK_SIZE - 1) // CHUNK_SIZE

        for i in range(total_chunks):
            chunk = data[i * CHUNK_SIZE:(i + 1) * CHUNK_SIZE]
            header = total_chunks.to_bytes(2, "big") + i.to_bytes(2, "big")
            self._socket.sendto(header + chunk, (host, port))

    def send_to_multiple(self, data: bytes, targets: list):
        """向多个目标发送同一帧"""
        for host, port in targets:
            self.send_frame(data, host, port)

    def close(self):
        self._socket.close()


class UDPReceiver:
    """UDP 接收器 - 用于接收屏幕数据"""

    def __init__(self, port: int):
        self._socket = None
        self._port = port
        self._running = False
        self._recv_thread = None
        self._frame_callback = None
        self._assemblies = {}
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            self._socket.bind(("0.0.0.0", port))
            print(f"[UDPReceiver] 已绑定端口 {port}")
        except Exception as e:
            print(f"[UDPReceiver] 绑定端口 {port} 失败: {e}")
            raise

    def on_frame(self, callback):
        """设置帧接收回调 callback(frame_data, sender_addr)"""
        self._frame_callback = callback

    def start(self):
        """启动接收线程"""
        if not self._socket:
            print("[UDPReceiver] socket未初始化，无法启动")
            return
        self._running = True
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()
        print(f"[UDPReceiver] 接收线程已启动，监听端口 {self._port}")

    def _recv_loop(self):
        """接收循环 - 处理分片重组"""
        print(f"[UDPReceiver] 进入接收循环")
        while self._running:
            try:
                data, addr = self._socket.recvfrom(65536)
                print(f"[UDPReceiver] 收到数据: {len(data)} bytes from {addr}")
                
                if len(data) < 4:
                    print(f"[UDPReceiver] 数据太短: {len(data)} bytes")
                    continue

                total = int.from_bytes(data[:2], "big")
                index = int.from_bytes(data[2:4], "big")
                chunk = data[4:]

                key = addr
                if key not in self._assemblies:
                    self._assemblies[key] = {"chunks": {}, "total": total}
                    print(f"[UDPReceiver] 新建分片缓冲 for {addr}, 共{total}片")

                assembly = self._assemblies[key]
                assembly["chunks"][index] = chunk
                print(f"[UDPReceiver] 收到第{index+1}/{total}片")

                if len(assembly["chunks"]) == total:
                    frame_data = b""
                    for i in range(total):
                        frame_data += assembly["chunks"][i]

                    print(f"[UDPReceiver] 分片重组完成，总大小: {len(frame_data)}")
                    if self._frame_callback:
                        self._frame_callback(frame_data, addr)

                    del self._assemblies[key]

            except Exception as e:
                if self._running:
                    print(f"[UDP接收错误] {e}")

    def stop(self):
        """停止接收"""
        self._running = False
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass

    @property
    def port(self):
        return self._port
