# -*- coding: utf-8 -*-
"""
安卓客户端核心模块
复用现有协议，实现接收服务器投屏功能
"""

import socket
import threading
import time
import struct
import json
import zlib
from kivy.clock import Clock


# ==================== 协议常量（复用现有定义）====================
MSG_REGISTER        = "register"
MSG_REGISTER_ACK    = "register_ack"
MSG_HEARTBEAT       = "heartbeat"
MSG_BROADCAST_START = "broadcast_start"
MSG_BROADCAST_STOP  = "broadcast_stop"
MSG_SCREEN_REQ      = "screen_request"
MSG_SCREEN_STOP     = "screen_stop"
MSG_REMOTE_CTRL     = "remote_control"
MSG_REMOTE_STOP     = "remote_stop"
MSG_LOCK_SCREEN     = "lock_screen"
MSG_UNLOCK_SCREEN   = "unlock_screen"
MSG_SEND_MSG        = "send_message"
MSG_DISCONNECT      = "disconnect"

DATA_TEACHER_SCREEN = 0x01
DATA_STUDENT_SCREEN = 0x02
DATA_TEACHER_AUDIO  = 0x03
DATA_STUDENT_AUDIO  = 0x04

TCP_PORT = 9901
UDP_PORT = 9902
HEARTBEAT_INTERVAL = 3


# ==================== 协议打包/解包函数 ====================

def pack_message(msg_type: str, payload: dict = None) -> bytes:
    """打包控制消息"""
    msg = {"type": msg_type}
    if payload:
        msg.update(payload)
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    length = struct.pack("!I", len(body))
    return length + body


def unpack_message(data: bytes):
    """解包控制消息"""
    try:
        if len(data) < 4:
            return None
        length = struct.unpack("!I", data[:4])[0]
        body = data[4:4 + length]
        msg = json.loads(body.decode("utf-8"))
        return msg.get("type"), msg
    except Exception:
        return None


def unpack_frame(data: bytes):
    """解包屏幕帧"""
    try:
        if len(data) < 21:
            return None
        frame_type, seq, width, height, comp_len, orig_len = struct.unpack("!BIIIII", data[:21])
        compressed = data[21:21 + comp_len]
        frame_data = zlib.decompress(compressed)
        return frame_type, seq, width, height, frame_data
    except Exception:
        return None


def unpack_audio_frame(data: bytes):
    """解包音频帧"""
    try:
        if len(data) < 17:
            return None
        frame_type, seq, sample_rate, channels, audio_len = struct.unpack("!BIIII", data[:17])
        audio_data = data[17:17 + audio_len]
        return frame_type, seq, sample_rate, channels, audio_data
    except Exception:
        return None


# ==================== 消息接收器 ====================

class MessageReceiver:
    """TCP 流式消息接收器"""

    def __init__(self):
        self.buffer = b""

    def feed(self, data: bytes):
        self.buffer += data

    def extract_messages(self):
        messages = []
        while len(self.buffer) >= 4:
            length = struct.unpack("!I", self.buffer[:4])[0]
            if length > 10 * 1024 * 1024:
                self.buffer = b""
                break
            if len(self.buffer) < 4 + length:
                break
            msg_data = self.buffer[:4 + length]
            self.buffer = self.buffer[4 + length:]
            result = unpack_message(msg_data)
            if result:
                messages.append(result)
        return messages


# ==================== UDP 接收器 ====================

class UDPReceiver:
    """UDP 数据接收器"""

    def __init__(self, port: int):
        self._socket = None
        self._port = port
        self._running = False
        self._recv_thread = None
        self._frame_callback = None
        self._assemblies = {}

    def bind(self):
        """绑定端口"""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        self._socket.bind(("0.0.0.0", self._port))
        print(f"[UDP] 已绑定端口 {self._port}")

    def on_frame(self, callback):
        self._frame_callback = callback

    def start(self):
        self._running = True
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

    def _recv_loop(self):
        while self._running:
            try:
                data, addr = self._socket.recvfrom(65536)
                if len(data) < 4:
                    continue

                total = int.from_bytes(data[:2], "big")
                index = int.from_bytes(data[2:4], "big")
                chunk = data[4:]

                key = addr
                if key not in self._assemblies:
                    self._assemblies[key] = {"chunks": {}, "total": total}

                assembly = self._assemblies[key]
                assembly["chunks"][index] = chunk

                if len(assembly["chunks"]) == total:
                    frame_data = b""
                    for i in range(total):
                        frame_data += assembly["chunks"][i]

                    if self._frame_callback:
                        self._frame_callback(frame_data, addr)
                    del self._assemblies[key]

            except Exception as e:
                if self._running:
                    print(f"[UDP错误] {e}")

    def stop(self):
        self._running = False
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass


# ==================== 安卓音频播放器 ====================

class AndroidAudioPlayer:
    """安卓音频播放器 - 使用 Kivy Audio"""

    def __init__(self):
        self._audio = None
        self._enabled = False
        self._sample_rate = 44100
        self._channels = 2
        self._buffer = []
        self._lock = threading.Lock()
        self._playing = False

    def init_audio(self, sample_rate=44100, channels=2):
        """初始化音频"""
        self._sample_rate = sample_rate
        self._channels = channels
        self._enabled = True
        print(f"[音频] 初始化完成 采样率={sample_rate} 声道={channels}")

    def play_audio(self, audio_data, sample_rate=None, channels=None):
        """播放音频数据"""
        if not self._enabled:
            return

        try:
            # 将音频数据写入缓冲区
            with self._lock:
                self._buffer.append(audio_data)
                # 限制缓冲区大小
                if len(self._buffer) > 5:
                    self._buffer = self._buffer[-5:]
        except Exception as e:
            print(f"[音频播放错误] {e}")

    def get_buffer_data(self):
        """获取缓冲的音频数据"""
        with self._lock:
            if len(self._buffer) == 0:
                return None
            data = self._buffer.pop(0)
            return data

    def stop(self):
        self._enabled = False
        self._buffer = []


# ==================== 安卓客户端核心 ====================

class AndroidClientCore:
    """安卓客户端核心逻辑"""

    def __init__(self, callbacks=None):
        """
        Args:
            callbacks: 回调函数字典
                - on_connected(server_ip, student_id)
                - on_disconnected(reason)
                - on_screen_frame(jpeg_data)
                - on_audio_frame(audio_data, sample_rate, channels)
                - on_message(text)
                - on_screen_locked()
                - on_screen_unlocked()
                - on_log(text)
        """
        self.callbacks = callbacks or {}
        self.tcp_socket = None
        self.udp_receiver = None
        self.audio_player = AndroidAudioPlayer()
        self.student_id = None
        self.server_ip = None
        self.server_udp_port = UDP_PORT
        self.my_udp_port = 0
        self._running = False
        self._is_locked = False
        self._tcp_receiver = MessageReceiver()

    def _callback(self, name, *args):
        """触发回调"""
        cb = self.callbacks.get(name)
        if cb:
            try:
                cb(*args)
            except Exception as e:
                print(f"[回调错误] {name}: {e}")

    def connect(self, server_ip: str, student_name: str):
        """连接到服务器"""
        try:
            self.server_ip = server_ip
            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.settimeout(5)
            self.tcp_socket.connect((server_ip, TCP_PORT))
            self.tcp_socket.settimeout(None)
            self.tcp_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            # 发送注册
            reg_msg = pack_message(MSG_REGISTER, {"name": student_name})
            self.tcp_socket.sendall(reg_msg)

            self._running = True

            # 启动 TCP 接收线程
            threading.Thread(target=self._tcp_loop, daemon=True).start()
            # 启动心跳线程
            threading.Thread(target=self._heartbeat_loop, daemon=True).start()

            self._callback("on_log", f"正在连接到 {server_ip}:{TCP_PORT}...")

        except Exception as e:
            self._callback("on_log", f"连接失败: {e}")
            self._callback("on_disconnected", str(e))

    def _tcp_loop(self):
        """TCP 接收循环"""
        try:
            while self._running:
                data = self.tcp_socket.recv(65536)
                if not data:
                    break

                self._tcp_receiver.feed(data)
                for msg_type, payload in self._tcp_receiver.extract_messages():
                    self._handle_message(msg_type, payload)

        except Exception as e:
            if self._running:
                self._callback("on_disconnected", str(e))
        finally:
            self._running = False

    def _handle_message(self, msg_type, payload):
        """处理服务器消息"""
        if msg_type == MSG_REGISTER_ACK:
            self.student_id = payload.get("student_id")
            self.my_udp_port = payload.get("udp_port", 0)
            self.server_udp_port = payload.get("server_udp_port", UDP_PORT)

            # 启动 UDP 接收
            if self.my_udp_port > 0:
                self.udp_receiver = UDPReceiver(self.my_udp_port)
                self.udp_receiver.bind()
                self.udp_receiver.on_frame(self._on_udp_frame)
                self.udp_receiver.start()

            self._callback("on_connected", self.server_ip, self.student_id)
            self._callback("on_log", f"已连接，ID: {self.student_id}")

        elif msg_type == MSG_BROADCAST_START:
            self._callback("on_log", "教师开始广播")

        elif msg_type == MSG_BROADCAST_STOP:
            self._callback("on_log", "教师停止广播")

        elif msg_type == MSG_LOCK_SCREEN:
            self._is_locked = True
            self._callback("on_screen_locked")
            self._callback("on_log", "屏幕已被锁定")

        elif msg_type == MSG_UNLOCK_SCREEN:
            self._is_locked = False
            self._callback("on_screen_unlocked")
            self._callback("on_log", "屏幕已解锁")

        elif msg_type == MSG_SEND_MSG:
            message = payload.get("message", "")
            self._callback("on_message", message)

    def _on_udp_frame(self, frame_data, sender_addr):
        """处理 UDP 数据帧"""
        if len(frame_data) < 1:
            return

        frame_type = frame_data[0]

        # 音频帧
        if frame_type == DATA_TEACHER_AUDIO:
            result = unpack_audio_frame(frame_data)
            if result:
                _, seq, sample_rate, channels, audio_data = result
                self._callback("on_audio_frame", audio_data, sample_rate, channels)
            return

        # 屏幕帧
        result = unpack_frame(frame_data)
        if result:
            frame_type, seq, width, height, jpeg_data = result
            if frame_type == DATA_TEACHER_SCREEN:
                self._callback("on_screen_frame", jpeg_data)

    def _heartbeat_loop(self):
        """心跳循环"""
        while self._running:
            try:
                hb = pack_message(MSG_HEARTBEAT)
                self.tcp_socket.sendall(hb)
                time.sleep(HEARTBEAT_INTERVAL)
            except Exception:
                if self._running:
                    self._callback("on_disconnected", "连接断开")
                break

    def disconnect(self):
        """断开连接"""
        self._running = False
        if self.udp_receiver:
            self.udp_receiver.stop()
        if self.tcp_socket:
            try:
                disc = pack_message(MSG_DISCONNECT)
                self.tcp_socket.sendall(disc)
                self.tcp_socket.close()
            except Exception:
                pass
        self.audio_player.stop()

    @property
    def is_locked(self):
        return self._is_locked

    @property
    def is_connected(self):
        return self._running
