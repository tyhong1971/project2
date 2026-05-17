# -*- coding: utf-8 -*-
"""
安卓投屏客户端 - Web UI 版本
使用 Python 内置 HTTP 服务器 + WebSocket 通信
"""

import sys
import os
import json
import base64
import threading
import asyncio
import socket
from http.server import HTTPServer, SimpleHTTPRequestHandler
from functools import partial

# 添加父目录到路径，复用现有协议
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import websockets
except ImportError:
    print("请安装 websockets: pip install websockets")
    sys.exit(1)

from protocol import (
    MSG_REGISTER, MSG_REGISTER_ACK, MSG_HEARTBEAT,
    MSG_BROADCAST_START, MSG_BROADCAST_STOP,
    MSG_LOCK_SCREEN, MSG_UNLOCK_SCREEN,
    MSG_SEND_MSG, MSG_DISCONNECT,
    pack_message, unpack_message, MessageReceiver,
    unpack_frame, unpack_audio_frame,
    DATA_TEACHER_SCREEN, DATA_TEACHER_AUDIO
)
from network import TCPTransport, UDPReceiver

# ==================== 常量 ====================
TCP_PORT = 9901
UDP_PORT = 9902
WS_PORT = 8765
HTTP_PORT = 8080
HEARTBEAT_INTERVAL = 3


# ==================== 客户端核心 ====================

class WebUIClient:
    """Web UI 客户端核心"""
    
    def __init__(self, ws_handler):
        self.ws_handler = ws_handler
        self.tcp = None
        self.udp_receiver = None
        self.student_id = None
        self.server_ip = None
        self.my_udp_port = 0
        self._running = False
        self._audio_enabled = True
        self._tcp_receiver = MessageReceiver()
        self._loop = asyncio.get_event_loop()
    
    async def connect(self, server_ip: str, student_name: str):
        """连接到服务器"""
        try:
            self.server_ip = server_ip
            self.tcp = TCPTransport()
            self.tcp.connect(server_ip, TCP_PORT)
            
            # 发送注册
            self.tcp.send(MSG_REGISTER, {"name": student_name})
            
            self._running = True
            
            # 启动 TCP 接收线程
            threading.Thread(target=self._tcp_loop, daemon=True).start()
            # 启动心跳线程
            threading.Thread(target=self._heartbeat_loop, daemon=True).start()
            
            return True, "连接中..."
            
        except Exception as e:
            return False, str(e)
    
    def _tcp_loop(self):
        """TCP 接收循环"""
        try:
            while self._running:
                data = self.tcp.socket.recv(65536)
                if not data:
                    break
                
                self._tcp_receiver.feed(data)
                for msg_type, payload in self._tcp_receiver.extract_messages():
                    self._handle_message(msg_type, payload)
                    
        except Exception as e:
            if self._running:
                self._send_ws_message('disconnected', {'reason': str(e)})
        finally:
            self._running = False
    
    def _handle_message(self, msg_type, payload):
        """处理服务器消息"""
        if msg_type == MSG_REGISTER_ACK:
            self.student_id = payload.get("student_id")
            self.my_udp_port = payload.get("udp_port", 0)
            
            # 启动 UDP 接收
            if self.my_udp_port > 0:
                self.udp_receiver = UDPReceiver(self.my_udp_port)
                self.udp_receiver.bind()
                self.udp_receiver.on_frame(self._on_udp_frame)
                self.udp_receiver.start()
            
            self._send_ws_message('connected', {
                'server_ip': self.server_ip,
                'student_id': self.student_id
            })
            
        elif msg_type == MSG_BROADCAST_START:
            print("[WebUI] 教师开始广播")
            
        elif msg_type == MSG_BROADCAST_STOP:
            print("[WebUI] 教师停止广播")
            
        elif msg_type == MSG_LOCK_SCREEN:
            self._send_ws_message('screen_locked', {})
            
        elif msg_type == MSG_UNLOCK_SCREEN:
            self._send_ws_message('screen_unlocked', {})
            
        elif msg_type == MSG_SEND_MSG:
            message = payload.get("message", "")
            self._send_ws_message('message', {'text': message})
    
    def _on_udp_frame(self, frame_data, sender_addr):
        """处理 UDP 数据帧"""
        if len(frame_data) < 1:
            return
        
        frame_type = frame_data[0]
        
        # 音频帧
        if frame_type == DATA_TEACHER_AUDIO:
            if self._audio_enabled:
                result = unpack_audio_frame(frame_data)
                if result:
                    _, seq, sample_rate, channels, audio_data = result
                    # 这里可以播放音频，简化版本暂不处理
            return
        
        # 屏幕帧
        result = unpack_frame(frame_data)
        if result:
            frame_type, seq, width, height, jpeg_data = result
            if frame_type == DATA_TEACHER_SCREEN:
                # 转换为 base64 发送给前端
                image_base64 = base64.b64encode(jpeg_data).decode('utf-8')
                self._send_ws_message('screen_frame', {'image': image_base64})
    
    def _send_ws_message(self, msg_type, data):
        """发送消息到 WebSocket"""
        data['type'] = msg_type
        asyncio.run_coroutine_threadsafe(
            self.ws_handler(json.dumps(data)),
            self._loop
        )
    
    def _heartbeat_loop(self):
        """心跳循环"""
        import time
        while self._running:
            try:
                self.tcp.send(MSG_HEARTBEAT)
                time.sleep(HEARTBEAT_INTERVAL)
            except Exception:
                if self._running:
                    self._send_ws_message('disconnected', {'reason': '连接断开'})
                break
    
    def set_audio_enabled(self, enabled: bool):
        """设置音频开关"""
        self._audio_enabled = enabled
    
    def disconnect(self):
        """断开连接"""
        self._running = False
        if self.udp_receiver:
            self.udp_receiver.stop()
        if self.tcp:
            try:
                self.tcp.send(MSG_DISCONNECT)
                self.tcp.close()
            except Exception:
                pass


# ==================== WebSocket 服务器 ====================

class WebSocketServer:
    """WebSocket 服务器"""
    
    def __init__(self):
        self.client = None
        self.ws_clients = set()
    
    async def handler(self, websocket):
        """WebSocket 处理器"""
        self.ws_clients.add(websocket)
        
        try:
            async for message in websocket:
                data = json.loads(message)
                await self.handle_message(data, websocket)
        except Exception as e:
            print(f"[WS] 连接关闭: {e}")
        finally:
            self.ws_clients.discard(websocket)
    
    async def handle_message(self, data, websocket):
        """处理客户端消息"""
        msg_type = data.get('type')
        
        if msg_type == 'connect':
            server_ip = data.get('serverIp')
            student_name = data.get('studentName')
            
            self.client = WebUIClient(self.broadcast)
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
        """广播消息给所有 WebSocket 客户端"""
        for ws in self.ws_clients:
            try:
                await ws.send(message)
            except Exception:
                pass


# ==================== HTTP 服务器 ====================

class CustomHTTPHandler(SimpleHTTPRequestHandler):
    """自定义 HTTP 处理器"""
    
    def __init__(self, *args, directory=None, **kwargs):
        if directory:
            super().__init__(*args, directory=directory, **kwargs)
        else:
            super().__init__(*args, **kwargs)
    
    def log_message(self, format, *args):
        pass  # 静默日志


# ==================== 主程序 ====================

def get_local_ip():
    """获取本机 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


async def main_async():
    """异步主函数"""
    ws_server = WebSocketServer()
    
    # 启动 WebSocket 服务器
    async with websockets.serve(ws_server.handler, "0.0.0.0", WS_PORT):
        print(f"[WS] WebSocket 服务器已启动: ws://localhost:{WS_PORT}")
        
        # 启动 HTTP 服务器（在线程中）
        script_dir = os.path.dirname(os.path.abspath(__file__))
        handler = partial(CustomHTTPHandler, directory=script_dir)
        http_server = HTTPServer(("0.0.0.0", HTTP_PORT), handler)
        
        http_thread = threading.Thread(
            target=http_server.serve_forever,
            daemon=True
        )
        http_thread.start()
        
        local_ip = get_local_ip()
        print(f"[HTTP] HTTP 服务器已启动: http://{local_ip}:{HTTP_PORT}")
        print(f"\n请在浏览器中打开: http://{local_ip}:{HTTP_PORT}")
        print("或在安卓设备上打开此地址")
        print("\n按 Ctrl+C 退出...")
        
        # 保持运行
        await asyncio.Future()


def main():
    """主函数"""
    print("=" * 50)
    print("局域网投屏 - 安卓客户端 (Web UI 版本)")
    print("=" * 50)
    
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
