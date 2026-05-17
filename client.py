# -*- coding: utf-8 -*-
"""
局域网投屏系统 - 客户端（学生端）
功能：接收教师广播、屏幕共享、被远程控制、接收消息通知
"""

import sys
import os
import io
import time
import socket
import threading
import ctypes
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QTextEdit, QLineEdit, QGroupBox, QDialog,
    QMessageBox, QInputDialog, QFrame, QDesktopWidget, QGraphicsOpacityEffect
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QSize
from PyQt5.QtGui import QPixmap, QImage, QFont, QCursor, QMouseEvent

from protocol import (
    MSG_REGISTER, MSG_REGISTER_ACK, MSG_HEARTBEAT,
    MSG_BROADCAST_START, MSG_BROADCAST_STOP,
    MSG_SCREEN_REQ, MSG_SCREEN_STOP,
    MSG_REMOTE_CTRL, MSG_REMOTE_STOP,
    MSG_LOCK_SCREEN, MSG_UNLOCK_SCREEN,
    MSG_SEND_MSG, MSG_SHUTDOWN, MSG_DISCONNECT,
    pack_message, MessageReceiver,
    pack_frame, unpack_frame, unpack_audio_frame,
    DATA_TEACHER_SCREEN, DATA_STUDENT_SCREEN,
    DATA_TEACHER_AUDIO, DATA_STUDENT_AUDIO
)
from network import TCPTransport, UDPSender, UDPReceiver
from screen_capture import ScreenCapture
from audio_capture import AudioPlayer

# ==================== 常量 ====================
TCP_PORT = 9901
UDP_PORT = 9902
HEARTBEAT_INTERVAL = 3  # 心跳间隔（秒）


# ==================== 信号桥接 ====================
class ClientSignalBridge(QObject):
    """跨线程信号桥接"""
    connected = pyqtSignal(str, int)           # server_ip, student_id
    disconnected = pyqtSignal(str)             # reason
    teacher_frame = pyqtSignal(bytes)          # jpeg_data
    screen_requested = pyqtSignal()            # 教师请求查看屏幕
    screen_stop = pyqtSignal()                 # 停止共享屏幕
    remote_control_start = pyqtSignal()        # 开始远程控制
    remote_control_stop = pyqtSignal()         # 停止远程控制
    remote_event = pyqtSignal(str, dict)       # event_type, event_data
    screen_locked = pyqtSignal()               # 屏幕被锁定
    screen_unlocked = pyqtSignal()             # 屏幕解锁
    message_received = pyqtSignal(str)         # message
    shutdown_received = pyqtSignal(str)        # action
    log_message = pyqtSignal(str)              # log
    status_update = pyqtSignal(str)            # status text


# ==================== 客户端核心 ====================
class ClientCore:
    """客户端核心逻辑"""

    def __init__(self, signal_bridge: ClientSignalBridge):
        self.bridge = signal_bridge
        self.tcp = TCPTransport()
        self.udp_sender = UDPSender()
        self.udp_receiver = None
        self.screen_capture = None
        self.audio_player = None
        self.student_id = None
        self.server_ip = None
        self.server_udp_port = UDP_PORT
        self.my_udp_port = 0
        self._running = False
        self._is_sharing = False
        self._is_controlled = False
        self._is_locked = False
        self._heartbeat_timer = None
        self._audio_enabled = False

    def connect_to_server(self, server_ip: str, student_name: str):
        """连接到服务器"""
        try:
            self.server_ip = server_ip
            self.tcp.connect(server_ip, TCP_PORT)

            # 设置消息回调
            self.tcp.on_receive(self._on_message)

            # 发送注册
            self.tcp.send(MSG_REGISTER, {"name": student_name})

            # 启动心跳（在线程中运行）
            self._running = True
            heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            heartbeat_thread.start()

            self.bridge.log_message.emit(f"正在连接到 {server_ip}:{TCP_PORT}...")
        except Exception as e:
            self.bridge.log_message.emit(f"连接失败: {e}")
            self.bridge.disconnected.emit(str(e))

    def _heartbeat_loop(self):
        """心跳循环"""
        while self._running:
            try:
                self.tcp.send(MSG_HEARTBEAT)
                time.sleep(HEARTBEAT_INTERVAL)
            except Exception:
                if self._running:
                    self.bridge.disconnected.emit("连接断开")
                break

    def _on_message(self, msg_type, payload):
        """处理服务器消息"""
        if msg_type == MSG_REGISTER_ACK:
            self.student_id = payload.get("student_id")
            self.my_udp_port = payload.get("udp_port", 0)
            self.server_udp_port = payload.get("server_udp_port", UDP_PORT)
            print(f"[客户端] 注册成功 ID={self.student_id} 我的UDP端口={self.my_udp_port} 服务器UDP端口={self.server_udp_port}")

            # 启动 UDP 接收
            if self.my_udp_port > 0:
                try:
                    self.udp_receiver = UDPReceiver(self.my_udp_port)
                    self.udp_receiver.on_frame(self._on_udp_frame)
                    self.udp_receiver.start()
                    print(f"[客户端] UDP接收器已启动，监听端口: {self.my_udp_port}")
                    
                    # 测试UDP连通性
                    threading.Thread(target=self._test_udp_connectivity, daemon=True).start()
                except Exception as e:
                    print(f"[客户端] UDP接收器启动失败: {e}")
            else:
                print("[客户端] UDP端口无效，无法启动UDP接收")

            self.bridge.connected.emit(self.server_ip, self.student_id)
            self.bridge.log_message.emit(
                f"已连接到服务器，分配ID: {self.student_id}，UDP端口: {self.my_udp_port}")
            
            # 提前初始化音频播放器
            self._start_audio()
            
            # 播放测试音验证音频输出
            if self._audio_enabled:
                self._play_test_tone()

        elif msg_type == MSG_BROADCAST_START:
            udp_port = payload.get("udp_port", UDP_PORT)
            self.server_udp_port = udp_port
            self.bridge.log_message.emit("教师开始广播屏幕")
            self.bridge.status_update.emit("📺 正在接收教师广播...")

        elif msg_type == MSG_BROADCAST_STOP:
            self.bridge.log_message.emit("教师停止广播")
            self.bridge.status_update.emit("已连接")

        elif msg_type == MSG_SCREEN_REQ:
            self.bridge.screen_requested.emit()
            self.bridge.log_message.emit("教师请求查看您的屏幕")

        elif msg_type == MSG_SCREEN_STOP:
            self._stop_sharing()
            self.bridge.screen_stop.emit()

        elif msg_type == MSG_REMOTE_CTRL:
            event_data = payload.get("event_data")
            if event_data:
                # 远程控制事件
                self.bridge.remote_event.emit(
                    payload.get("event_type", ""), event_data)
            else:
                # 开始远程控制
                self.bridge.remote_control_start.emit()
                self.bridge.log_message.emit("⚠ 教师开始远程控制您的电脑")

        elif msg_type == MSG_REMOTE_STOP:
            self.bridge.remote_control_stop.emit()
            self.bridge.log_message.emit("教师停止远程控制")

        elif msg_type == MSG_LOCK_SCREEN:
            self._is_locked = True
            self.bridge.screen_locked.emit()
            self.bridge.log_message.emit("🔒 屏幕已被教师锁定")

        elif msg_type == MSG_UNLOCK_SCREEN:
            self._is_locked = False
            self.bridge.screen_unlocked.emit()
            self.bridge.log_message.emit("🔓 屏幕已解锁")

        elif msg_type == MSG_SEND_MSG:
            message = payload.get("message", "")
            self.bridge.message_received.emit(message)

        elif msg_type == MSG_SHUTDOWN:
            action = payload.get("action", "shutdown")
            self.bridge.shutdown_received.emit(action)

    def _test_udp_connectivity(self):
        """测试UDP连通性"""
        time.sleep(1)
        
        # 测试1: 向本地端口发送数据包
        try:
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            test_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            test_message = b"LOCAL_TEST_PACKET"
            test_socket.sendto(test_message, ("127.0.0.1", self.my_udp_port))
            print(f"[客户端] 发送本地UDP测试包到 127.0.0.1:{self.my_udp_port}")
            test_socket.close()
        except Exception as e:
            print(f"[客户端] 本地UDP测试失败: {e}")
        
        # 测试2: 向服务端发送测试包
        try:
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            test_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            test_socket.bind(("0.0.0.0", 0))
            test_port = test_socket.getsockname()[1]
            test_message = b"TEST_UDP_CONNECTIVITY"
            test_socket.sendto(test_message, (self.server_ip, self.server_udp_port))
            print(f"[客户端] 发送UDP测试包到 {self.server_ip}:{self.server_udp_port} (本地端口: {test_port})")
            test_socket.settimeout(3)
            try:
                data, addr = test_socket.recvfrom(1024)
                print(f"[客户端] 收到UDP响应: {data} from {addr}")
            except socket.timeout:
                print(f"[客户端] UDP测试超时 - 可能存在网络问题或防火墙阻止")
            test_socket.close()
        except Exception as e:
            print(f"[客户端] UDP连通性测试失败: {e}")

    def _on_udp_frame(self, frame_data, sender_addr):
        """接收 UDP 数据帧"""
        print(f"[客户端] 收到UDP数据，长度={len(frame_data)}，来自={sender_addr}")
        
        # 首先检查第一个字节来识别帧类型
        if len(frame_data) < 1:
            return
        frame_type = frame_data[0]
        
        # 先判断是音频帧还是视频帧
        if frame_type == DATA_TEACHER_AUDIO or frame_type == DATA_STUDENT_AUDIO:
            # 音频帧 (0x03 or 0x04)
            audio_result = unpack_audio_frame(frame_data)
            if audio_result:
                frame_type, seq, sample_rate, channels, audio_data = audio_result
                print(f"[客户端] 收到音频帧 type={frame_type} seq={seq} 采样率={sample_rate} 声道={channels} 大小={len(audio_data)}")
                if frame_type == DATA_TEACHER_AUDIO:
                    print(f"[客户端] 播放音频，采样率={sample_rate}，声道={channels}")
                    self._play_audio(audio_data, sample_rate, channels)
                return
        
        # 尝试解包为屏幕帧
        result = unpack_frame(frame_data)
        if result is None:
            print("[客户端] 帧解包失败")
            return
        frame_type, seq, width, height, jpeg_data = result
        print(f"[客户端] 收到视频帧 type={frame_type} seq={seq} size={len(jpeg_data)}")
        if frame_type == DATA_TEACHER_SCREEN:
            self.bridge.teacher_frame.emit(jpeg_data)

    def _start_audio(self):
        """启动音频播放"""
        if self._audio_enabled:
            return
        
        print("[客户端] 正在初始化音频播放器...")
        try:
            self.audio_player = AudioPlayer(
                sample_rate=44100,
                channels=2,
                buffer_size=4096
            )
            # 不需要再调用 start()，因为 AudioPlayer 初始化时已经启动了线程
            self._audio_enabled = True
            print("[客户端] 音频播放器初始化成功")
        except Exception as e:
            print(f"[客户端] 音频播放器初始化失败: {e}")
            self._audio_enabled = False

    def _stop_audio(self):
        """停止音频播放"""
        if not self._audio_enabled:
            return
        
        self._audio_enabled = False
        if self.audio_player:
            self.audio_player.stop()
            self.audio_player = None
        print("[客户端] 音频播放器已停止")

    def _play_audio(self, audio_data, sample_rate, channels):
        """播放音频数据"""
        if not self._audio_enabled:
            self._start_audio()
        
        if self.audio_player:
            print(f"[客户端] 调用 play_audio，数据长度={len(audio_data)}")
            self.audio_player.play_audio(audio_data, sample_rate, channels)

    def _play_test_tone(self):
        """播放测试音验证音频输出"""
        print("[客户端] 播放测试音...")
        try:
            import numpy as np
            t = np.linspace(0, 0.5, int(44100 * 0.5), endpoint=False)
            test_tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
            test_tone_stereo = np.column_stack((test_tone, test_tone))
            audio_data = test_tone_stereo.tobytes()
            
            if self.audio_player:
                self.audio_player.play_audio(audio_data, 44100, 2)
            print("[客户端] 测试音播放完成")
        except Exception as e:
            print(f"[客户端] 测试音播放失败: {e}")

    # ==================== 屏幕共享 ====================

    def start_sharing(self, target_fps=10):
        """开始共享屏幕给教师"""
        if self._is_sharing:
            return
        self._is_sharing = True
        self.screen_capture = ScreenCapture(target_width=1024, target_height=768, quality=40)
        self.screen_capture.start_streaming(self._on_share_frame, target_fps)
        self.bridge.log_message.emit("开始共享屏幕")

    def _on_share_frame(self, jpeg_data, seq, width, height):
        """屏幕帧回调"""
        frame = pack_frame(jpeg_data, DATA_STUDENT_SCREEN, seq, width, height)
        self.udp_sender.send_frame(frame, self.server_ip, self.server_udp_port)

    def _stop_sharing(self):
        """停止共享屏幕"""
        if not self._is_sharing:
            return
        self._is_sharing = False
        if self.screen_capture:
            self.screen_capture.stop_streaming()
            self.screen_capture = None
        self.bridge.log_message.emit("停止共享屏幕")

    # ==================== 远程控制执行 ====================

    def execute_remote_event(self, event_type, event_data):
        """执行远程控制事件"""
        try:
            if event_type == "mouse_move":
                x = event_data.get("x", 0)
                y = event_data.get("y", 0)
                screen_w, screen_h = self._get_screen_size()
                scale_x = screen_w / 1280
                scale_y = screen_h / 720
                real_x = int(x * scale_x)
                real_y = int(y * scale_y)
                ctypes.windll.user32.SetCursorPos(real_x, real_y)

            elif event_type == "mouse_press":
                button = event_data.get("button", 1)
                x = event_data.get("x", 0)
                y = event_data.get("y", 0)
                screen_w, screen_h = self._get_screen_size()
                real_x = int(x * screen_w / 1280)
                real_y = int(y * screen_h / 720)
                ctypes.windll.user32.SetCursorPos(real_x, real_y)
                mouse_event_map = {1: 0x0002, 2: 0x0008, 4: 0x0020}
                down = mouse_event_map.get(button, 0x0002)
                ctypes.windll.user32.mouse_event(down, 0, 0, 0, 0)

            elif event_type == "mouse_release":
                button = event_data.get("button", 1)
                mouse_event_map = {1: 0x0004, 2: 0x0010, 4: 0x0040}
                up = mouse_event_map.get(button, 0x0004)
                ctypes.windll.user32.mouse_event(up, 0, 0, 0, 0)

            elif event_type == "key_press":
                key = event_data.get("key", 0)
                vk = self._qt_key_to_vk(key)
                if vk:
                    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)

            elif event_type == "key_release":
                key = event_data.get("key", 0)
                vk = self._qt_key_to_vk(key)
                if vk:
                    ctypes.windll.user32.keybd_event(vk, 0, 0x0002, 0)

        except Exception as e:
            print(f"[远程控制执行错误] {e}")

    def _qt_key_to_vk(self, qt_key):
        """Qt 键码转 Windows 虚拟键码"""
        if 0x01000000 <= qt_key <= 0x01000019:
            return 0x41 + (qt_key - 0x01000000)
        if 0x01000030 <= qt_key <= 0x01000039:
            return qt_key - 0x01000030
        if 0x01000030 <= qt_key <= 0x0100003b:
            return 0x70 + (qt_key - 0x01000030)
        special_map = {
            0x01000004: 0x0D,   # Enter
            0x01000001: 0x1B,   # Escape
            0x01000008: 0x08,   # Backspace
            0x01000009: 0x09,   # Tab
            0x0100000c: 0x09,   # Tab (alt)
            0x01000012: 0x0D,   # Return
            0x01000010: 0x14,   # CapsLock
            0x01000020: 0x2D,   # Minus
            0x01000021: 0x3D,   # Equal
            0x01000022: 0x5B,   # BracketLeft
            0x01000023: 0x5D,   # BracketRight
            0x01000024: 0x5C,   # Backslash
            0x01000025: 0x2E,   # Delete
            0x01000026: 0x2D,   # Insert
            0x01000027: 0x70,   # F1
            0x01000028: 0x71,   # F2
            0x01000029: 0x72,   # F3
            0x0100002a: 0x73,   # F4
            0x0100002b: 0x74,   # F5
            0x0100002c: 0x75,   # F6
            0x0100002d: 0x76,   # F7
            0x0100002e: 0x77,   # F8
            0x0100002f: 0x78,   # F9
            0x01000030: 0x2D,   # Minus
            0x01000031: 0x6D,   # Divide
            0x01000032: 0x6A,   # Multiply
            0x01000033: 0x6B,   # Subtract
            0x01000034: 0x6B,   # Add
            0x01000035: 0x6C,   # Decimal
            0x01000036: 0x11,   # Shift
            0x01000037: 0x12,   # Ctrl
            0x01000038: 0x5B,   # Meta/Win
            0x01000039: 0x12,   # Alt
            0x0100001d: 0x24,   # Home
            0x0100001e: 0x26,   # Up
            0x0100001f: 0x21,   # PageUp
            0x01000020: 0x25,   # Left
            0x01000021: 0x27,   # Right
            0x01000022: 0x23,   # End
            0x01000023: 0x28,   # Down
            0x01000024: 0x22,   # PageDown
            0x01000030: 0x79,   # F10
            0x01000031: 0x7A,   # F11
            0x01000032: 0x7B,   # F12
            0x01000033: 0x13,   # Pause
            0x01000034: 0x00,   # Print
            0x01000035: 0x00,   # SysReq
            0x01000036: 0x0C,   # Clear
            0x01000037: 0x00,   # (unknown)
            0x01000038: 0x00,   # (unknown)
            0x01000039: 0x00,   # (unknown)
            0x0100003a: 0x00,   # (unknown)
            0x0100003b: 0x7B,   # F12
        }
        return special_map.get(qt_key, 0)

    def _get_screen_size(self):
        """获取屏幕分辨率"""
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

    def disconnect(self):
        """断开连接"""
        self._running = False
        self._stop_sharing()
        self._stop_audio()
        if self.udp_receiver:
            self.udp_receiver.stop()
        self.udp_sender.close()
        self.tcp.send(MSG_DISCONNECT)
        self.tcp.close()

    @property
    def is_locked(self):
        return self._is_locked

    @property
    def is_controlled(self):
        return self._is_controlled


# ==================== 屏幕锁定遮罩 ====================
class LockOverlay(QWidget):
    """屏幕锁定遮罩窗口 - 全屏覆盖"""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet("background-color: #000000;")
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)

        lock_icon = QLabel("🔒")
        lock_icon.setAlignment(Qt.AlignCenter)
        lock_icon.setStyleSheet("font-size: 80px;")
        layout.addWidget(lock_icon)

        lock_text = QLabel("屏幕已被教师锁定\n请等待教师解锁...")
        lock_text.setAlignment(Qt.AlignCenter)
        lock_text.setStyleSheet(
            "color: white; font-size: 24px; font-weight: bold; margin-top: 20px;"
        )
        layout.addWidget(lock_text)

    def show_lock(self):
        """显示锁定遮罩"""
        screen = QDesktopWidget().screenGeometry()
        self.setGeometry(screen)
        self.showFullScreen()
        self.setFocusPolicy(Qt.StrongFocus)
        self.activateWindow()

    def hide_lock(self):
        """隐藏锁定遮罩"""
        self.hide()

    def keyPressEvent(self, event):
        """锁定状态下忽略所有按键"""
        event.ignore()

    def mousePressEvent(self, event):
        event.ignore()

    def mouseMoveEvent(self, event):
        event.ignore()

    def closeEvent(self, event):
        """阻止关闭"""
        event.ignore()


# ==================== 学生端主窗口 ====================
class StudentMainWindow(QMainWindow):
    """学生端主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("局域网投屏系统 - 学生端")
        self.setMinimumSize(800, 600)
        self._current_pixmap = None
        self._lock_overlay = LockOverlay()
        self._init_ui()
        self._init_client()

    def _init_ui(self):
        self.bridge = ClientSignalBridge()
        self.bridge.connected.connect(self._on_connected)
        self.bridge.disconnected.connect(self._on_disconnected)
        self.bridge.teacher_frame.connect(self._on_teacher_frame)
        self.bridge.screen_requested.connect(self._on_screen_requested)
        self.bridge.screen_stop.connect(self._on_screen_stop)
        self.bridge.remote_control_start.connect(self._on_remote_start)
        self.bridge.remote_control_stop.connect(self._on_remote_stop)
        self.bridge.remote_event.connect(self._on_remote_event)
        self.bridge.screen_locked.connect(self._on_screen_locked)
        self.bridge.screen_unlocked.connect(self._on_screen_unlocked)
        self.bridge.message_received.connect(self._on_message)
        self.bridge.shutdown_received.connect(self._on_shutdown)
        self.bridge.log_message.connect(self._on_log)
        self.bridge.status_update.connect(self._on_status)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        top_bar = QHBoxLayout()
        self.lbl_status = QLabel("● 未连接")
        self.lbl_status.setStyleSheet("color: red; font-weight: bold; font-size: 14px;")
        top_bar.addWidget(self.lbl_status)
        top_bar.addStretch()
        self.lbl_info = QLabel("")
        self.lbl_info.setStyleSheet("color: #666;")
        top_bar.addWidget(self.lbl_info)
        layout.addLayout(top_bar)

        self.screen_label = QLabel("等待教师广播...")
        self.screen_label.setAlignment(Qt.AlignCenter)
        self.screen_label.setStyleSheet("""
            background-color: #1a1a2e; color: #555;
            border: 2px solid #333; border-radius: 8px;
        """)
        self.screen_label.setMinimumSize(640, 480)
        layout.addWidget(self.screen_label, 1)

        bottom_bar = QHBoxLayout()
        self.lbl_sharing = QLabel("")
        self.lbl_sharing.setStyleSheet("color: #f44336; font-weight: bold;")
        bottom_bar.addWidget(self.lbl_sharing)
        bottom_bar.addStretch()
        self.lbl_control = QLabel("")
        self.lbl_control.setStyleSheet("color: #ff9800; font-weight: bold;")
        bottom_bar.addWidget(self.lbl_control)
        layout.addLayout(bottom_bar)

        self.msg_bar = QLabel("")
        self.msg_bar.setStyleSheet("""
            background-color: #2196F3; color: white; padding: 8px;
            border-radius: 4px; font-size: 13px;
        """)
        self.msg_bar.setWordWrap(True)
        self.msg_bar.hide()
        layout.addWidget(self.msg_bar)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        self.log_text.setStyleSheet("""
            QTextEdit { background-color: #1e1e2e; color: #a6adc8;
                       font-family: Consolas, monospace; font-size: 11px;
                       border: 1px solid #333; border-radius: 4px; }
        """)
        self.log_text.hide()
        layout.addWidget(self.log_text)

        self.setStyleSheet("""
            QMainWindow { background-color: #f5f5f5; }
            QLabel { font-family: "Microsoft YaHei"; }
        """)

    def _init_client(self):
        """初始化客户端"""
        self.client = ClientCore(self.bridge)

    def connect_to_server(self, server_ip, student_name):
        """连接到服务器"""
        self.client.connect_to_server(server_ip, student_name)

    def _on_connected(self, server_ip, student_id):
        self.lbl_status.setText("● 已连接")
        self.lbl_status.setStyleSheet("color: green; font-weight: bold; font-size: 14px;")
        self.lbl_info.setText(f"服务器: {server_ip} | 学生ID: {student_id}")
        self.setWindowTitle(f"局域网投屏系统 - 学生端 (ID:{student_id})")

    def _on_disconnected(self, reason):
        self.lbl_status.setText("● 已断开")
        self.lbl_status.setStyleSheet("color: red; font-weight: bold; font-size: 14px;")
        self.lbl_info.setText(f"断开原因: {reason}")
        self._lock_overlay.hide_lock()

    def _on_teacher_frame(self, jpeg_data):
        """收到教师广播帧"""
        pixmap = QPixmap()
        pixmap.loadFromData(jpeg_data)
        if not pixmap.isNull():
            self._current_pixmap = pixmap
            scaled = pixmap.scaled(
                self.screen_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            self.screen_label.setPixmap(scaled)

    def _on_screen_requested(self):
        """教师请求查看屏幕"""
        self.client.start_sharing(target_fps=10)
        self.lbl_sharing.setText("📺 教师正在查看您的屏幕")

    def _on_screen_stop(self):
        """停止共享屏幕"""
        self.client._stop_sharing()
        self.lbl_sharing.setText("")

    def _on_remote_start(self):
        """开始被远程控制"""
        self.client._is_controlled = True
        self.lbl_control.setText("⚠ 教师正在远程控制您的电脑")

    def _on_remote_stop(self):
        """停止远程控制"""
        self.client._is_controlled = False
        self.lbl_control.setText("")

    def _on_remote_event(self, event_type, event_data):
        """执行远程控制事件"""
        self.client.execute_remote_event(event_type, event_data)

    def _on_screen_locked(self):
        """屏幕被锁定"""
        self._lock_overlay.show_lock()

    def _on_screen_unlocked(self):
        """屏幕解锁"""
        self._lock_overlay.hide_lock()

    def _on_message(self, message):
        """收到教师消息"""
        self.msg_bar.setText(f"💬 教师消息: {message}")
        self.msg_bar.show()
        QTimer.singleShot(8000, self.msg_bar.hide)

    def _on_shutdown(self, action):
        """收到关机/重启指令"""
        if action == "shutdown":
            QMessageBox.warning(self, "远程关机", "教师已发送关机指令，电脑即将关机。")
            os.system("shutdown /s /t 5")
        elif action == "restart":
            QMessageBox.warning(self, "远程重启", "教师已发送重启指令，电脑即将重启。")
            os.system("shutdown /r /t 5")

    def _on_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f'<span style="color:#666;">[{timestamp}]</span> {message}')
        self.log_text.show()

    def _on_status(self, text):
        self.statusBar().showMessage(text)

    def resizeEvent(self, event):
        """窗口大小改变时重新缩放"""
        if self._current_pixmap:
            scaled = self._current_pixmap.scaled(
                self.screen_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            self.screen_label.setPixmap(scaled)
        super().resizeEvent(event)

    def closeEvent(self, event):
        """关闭窗口"""
        reply = QMessageBox.question(
            self, "确认退出",
            "确定要断开连接并退出吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.client.disconnect()
            self._lock_overlay.hide_lock()
            event.accept()
        else:
            event.ignore()


# ==================== 连接对话框 ====================
class ConnectDialog(QDialog):
    """连接对话框"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("连接到教师端")
        self.setFixedSize(400, 300)
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self._result = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(20)

        title = QLabel("🏫 局域网投屏系统 - 学生端")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #333; margin: 10px;")
        layout.addWidget(title)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #ddd;")
        layout.addWidget(line)

        form = QGridLayout()
        form.setSpacing(12)

        form.addWidget(QLabel("🖥 教师IP地址:"), 0, 0)
        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("例如: 192.168.1.100")
        self.ip_input.setStyleSheet("padding: 8px; font-size: 14px; border: 1px solid #ccc; border-radius: 4px;")
        form.addWidget(self.ip_input, 0, 1)

        form.addWidget(QLabel("👤 学生姓名:"), 1, 0)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("请输入您的姓名")
        self.name_input.setStyleSheet("padding: 8px; font-size: 14px; border: 1px solid #ccc; border-radius: 4px;")
        form.addWidget(self.name_input, 1, 1)

        layout.addLayout(form)

        layout.addStretch()

        self.btn_connect = QPushButton("🔗 连接")
        self.btn_connect.setStyleSheet("""
            QPushButton { background-color: #2196F3; color: white; font-size: 16px;
                         padding: 12px; border-radius: 6px; font-weight: bold; border: none; }
            QPushButton:hover { background-color: #1976D2; }
            QPushButton:pressed { background-color: #0D47A1; }
        """)
        self.btn_connect.clicked.connect(self._on_connect)
        layout.addWidget(self.btn_connect)

        hint = QLabel("请确保与教师在同一局域网内")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #999; font-size: 11px;")
        layout.addWidget(hint)

        self.setStyleSheet("background-color: #fafafa;")

    def _on_connect(self):
        ip = self.ip_input.text().strip()
        name = self.name_input.text().strip()
        if not ip:
            QMessageBox.warning(self, "提示", "请输入教师IP地址")
            return
        if not name:
            QMessageBox.warning(self, "提示", "请输入学生姓名")
            return
        self._result = (ip, name)
        self.accept()

    def get_result(self):
        return self._result

    def show_and_wait(self):
        """显示对话框并等待结果"""
        self.show()
        self.raise_()
        self.activateWindow()
        self.ip_input.setFocus()
        return self._result


# ==================== 入口 ====================
def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 9))

    dialog = ConnectDialog()
    dialog.move(
        (QDesktopWidget().width() - dialog.width()) // 2,
        (QDesktopWidget().height() - dialog.height()) // 2
    )
    result = None
    if dialog.exec_() == QDialog.Accepted:
        result = dialog.get_result()

    if not result:
        return

    server_ip, student_name = result

    window = StudentMainWindow()
    window.show()
    window.move(
        (QDesktopWidget().width() - window.width()) // 2,
        (QDesktopWidget().height() - window.height()) // 2
    )
    window.connect_to_server(server_ip, student_name)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
