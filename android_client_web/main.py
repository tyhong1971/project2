# -*- coding: utf-8 -*-
"""
安卓投屏客户端 - 主入口
用于 buildozer 打包
"""

import os
import sys
import threading
import asyncio
import socket
import json
import base64
from http.server import HTTPServer, SimpleHTTPRequestHandler
from functools import partial

# 设置工作目录
if getattr(sys, 'frozen', False):
    # 打包后的路径
    WORK_DIR = os.path.dirname(sys.executable)
else:
    WORK_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(WORK_DIR)


# ==================== 简化版协议 ====================

import struct
import zlib

MSG_REGISTER = "register"
MSG_REGISTER_ACK = "register_ack"
MSG_HEARTBEAT = "heartbeat"
MSG_BROADCAST_START = "broadcast_start"
MSG_BROADCAST_STOP = "broadcast_stop"
MSG_LOCK_SCREEN = "lock_screen"
MSG_UNLOCK_SCREEN = "unlock_screen"
MSG_SEND_MSG = "send_message"
MSG_DISCONNECT = "disconnect"

DATA_TEACHER_SCREEN = 0x01
DATA_TEACHER_AUDIO = 0x03

TCP_PORT = 9901
UDP_PORT = 9902
WS_PORT = 8765
HTTP_PORT = 8080


def pack_message(msg_type: str, payload: dict = None) -> bytes:
    msg = {"type": msg_type}
    if payload:
        msg.update(payload)
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    length = struct.pack("!I", len(body))
    return length + body


def unpack_message(data: bytes):
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
    try:
        if len(data) < 21:
            return None
        frame_type, seq, width, height, comp_len, orig_len = struct.unpack("!BIIIII", data[:21])
        compressed = data[21:21 + comp_len]
        frame_data = zlib.decompress(compressed)
        return frame_type, seq, width, height, frame_data
    except Exception:
        return None


class MessageReceiver:
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
    def __init__(self, port: int):
        self._socket = None
        self._port = port
        self._running = False
        self._recv_thread = None
        self._frame_callback = None
        self._assemblies = {}

    def bind(self):
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        self._socket.bind(("0.0.0.0", self._port))

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

            except Exception:
                pass

    def stop(self):
        self._running = False
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass


# ==================== 客户端核心 ====================

class Client:
    def __init__(self, ws_handler):
        self.ws_handler = ws_handler
        self.tcp_socket = None
        self.udp_receiver = None
        self.student_id = None
        self.server_ip = None
        self.my_udp_port = 0
        self._running = False
        self._audio_enabled = True
        self._tcp_receiver = MessageReceiver()
        self._loop = asyncio.get_event_loop()

    async def connect(self, server_ip: str, student_name: str):
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

            threading.Thread(target=self._tcp_loop, daemon=True).start()
            threading.Thread(target=self._heartbeat_loop, daemon=True).start()

            return True, "连接中..."

        except Exception as e:
            return False, str(e)

    def _tcp_loop(self):
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
                self._send_ws('disconnected', {'reason': str(e)})
        finally:
            self._running = False

    def _handle_message(self, msg_type, payload):
        if msg_type == MSG_REGISTER_ACK:
            self.student_id = payload.get("student_id")
            self.my_udp_port = payload.get("udp_port", 0)

            if self.my_udp_port > 0:
                self.udp_receiver = UDPReceiver(self.my_udp_port)
                self.udp_receiver.bind()
                self.udp_receiver.on_frame(self._on_udp_frame)
                self.udp_receiver.start()

            self._send_ws('connected', {
                'server_ip': self.server_ip,
                'student_id': self.student_id
            })

        elif msg_type == MSG_LOCK_SCREEN:
            self._send_ws('screen_locked', {})

        elif msg_type == MSG_UNLOCK_SCREEN:
            self._send_ws('screen_unlocked', {})

        elif msg_type == MSG_SEND_MSG:
            message = payload.get("message", "")
            self._send_ws('message', {'text': message})

    def _on_udp_frame(self, frame_data, sender_addr):
        if len(frame_data) < 1:
            return

        frame_type = frame_data[0]

        if frame_type == DATA_TEACHER_SCREEN:
            result = unpack_frame(frame_data)
            if result:
                _, _, _, _, jpeg_data = result
                image_base64 = base64.b64encode(jpeg_data).decode('utf-8')
                self._send_ws('screen_frame', {'image': image_base64})

    def _send_ws(self, msg_type, data):
        data['type'] = msg_type
        asyncio.run_coroutine_threadsafe(
            self.ws_handler(json.dumps(data)),
            self._loop
        )

    def _heartbeat_loop(self):
        import time
        while self._running:
            try:
                hb = pack_message(MSG_HEARTBEAT)
                self.tcp_socket.sendall(hb)
                time.sleep(3)
            except Exception:
                if self._running:
                    self._send_ws('disconnected', {'reason': '连接断开'})
                break

    def set_audio_enabled(self, enabled: bool):
        self._audio_enabled = enabled

    def disconnect(self):
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


# ==================== WebSocket 服务器 ====================

try:
    import websockets
except ImportError:
    print("请安装 websockets: pip install websockets")
    sys.exit(1)


class WSServer:
    def __init__(self):
        self.client = None
        self.ws_clients = set()

    async def handler(self, websocket):
        self.ws_clients.add(websocket)
        try:
            async for message in websocket:
                data = json.loads(message)
                await self.handle_message(data, websocket)
        except Exception:
            pass
        finally:
            self.ws_clients.discard(websocket)

    async def handle_message(self, data, websocket):
        msg_type = data.get('type')

        if msg_type == 'connect':
            server_ip = data.get('serverIp')
            student_name = data.get('studentName')

            self.client = Client(self.broadcast)
            success, msg = await self.client.connect(server_ip, student_name)

            if not success:
                await websocket.send(json.dumps({
                    'type': 'error',
                    'message': f'连接失败: {msg}'
                }))

        elif msg_type == 'toggle_audio':
            if self.client:
                self.client.set_audio_enabled(data.get('enabled', True))

        elif msg_type == 'disconnect':
            if self.client:
                self.client.disconnect()
                self.client = None

    async def broadcast(self, message):
        for ws in self.ws_clients:
            try:
                await ws.send(message)
            except Exception:
                pass


# ==================== HTTP 服务器 ====================

class HTTPHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


# ==================== 主程序 ====================

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


async def main_async():
    ws_server = WSServer()

    async with websockets.serve(ws_server.handler, "0.0.0.0", WS_PORT):
        print(f"[WS] WebSocket: ws://localhost:{WS_PORT}")

        handler = partial(HTTPHandler, directory=WORK_DIR)
        http_server = HTTPServer(("0.0.0.0", HTTP_PORT), handler)

        http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        http_thread.start()

        local_ip = get_local_ip()
        print(f"[HTTP] HTTP: http://{local_ip}:{HTTP_PORT}")
        print(f"\n请在浏览器打开: http://{local_ip}:{HTTP_PORT}")

        await asyncio.Future()


def main():
    print("=" * 40)
    print("局域网投屏 - 安卓客户端")
    print("=" * 40)

    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
