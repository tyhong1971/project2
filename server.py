# -*- coding: utf-8 -*-
"""
局域网投屏系统 - 服务器端（教师端）
功能：教师屏幕广播、查看学生屏幕、远程控制、课堂管理
"""

import sys
import os
import io
import time
import socket
import threading
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QTextEdit,
    QLineEdit, QGroupBox, QGridLayout, QSplitter, QMenu, QAction,
    QMessageBox, QInputDialog, QTabWidget, QFrame, QStatusBar,
    QComboBox, QSpinBox, QCheckBox, QScrollArea, QDesktopWidget
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QSize
from PyQt5.QtGui import QPixmap, QImage, QIcon, QFont, QColor

from protocol import (
    MSG_REGISTER, MSG_REGISTER_ACK, MSG_HEARTBEAT,
    MSG_BROADCAST_START, MSG_BROADCAST_STOP,
    MSG_SCREEN_REQ, MSG_SCREEN_STOP,
    MSG_REMOTE_CTRL, MSG_REMOTE_STOP,
    MSG_LOCK_SCREEN, MSG_UNLOCK_SCREEN,
    MSG_SEND_MSG, MSG_DISCONNECT,
    pack_message, unpack_message, MessageReceiver,
    pack_frame, unpack_frame, pack_audio_frame, unpack_audio_frame,
    DATA_TEACHER_SCREEN, DATA_STUDENT_SCREEN,
    DATA_TEACHER_AUDIO, DATA_STUDENT_AUDIO
)
from network import TCPTransport, UDPSender, UDPReceiver
from screen_capture import ScreenCapture
from audio_capture import AudioCapture

# ==================== 默认端口 ====================
TCP_PORT = 9901
UDP_PORT = 9902
STUDENT_UDP_BASE = 9910  # 学生端 UDP 端口从 9910 开始分配


# ==================== 学生信息 ====================
class StudentInfo:
    """学生信息"""

    def __init__(self, student_id, name, tcp_socket, addr):
        self.student_id = student_id
        self.name = name
        self.tcp_socket = tcp_socket
        self.addr = addr  # (ip, port)
        self.ip = addr[0]
        self.udp_port = STUDENT_UDP_BASE + student_id
        self.connected = True
        self.connect_time = datetime.now()
        self.is_screen_sharing = False  # 是否正在共享屏幕
        self.is_controlled = False      # 是否正在被远程控制
        self.is_locked = False          # 屏幕是否被锁定


# ==================== 信号桥接 ====================
class SignalBridge(QObject):
    """跨线程信号桥接"""
    student_connected = pyqtSignal(int, str, str)       # id, name, ip
    student_disconnected = pyqtSignal(int)               # id
    student_screen_frame = pyqtSignal(int, bytes)        # student_id, jpeg_data
    teacher_screen_stats = pyqtSignal(str)               # stats text
    log_message = pyqtSignal(str)                        # log text
    student_list_update = pyqtSignal()                   # 刷新列表


# ==================== 服务器核心 ====================
class ServerCore:
    """服务器核心逻辑"""

    def __init__(self, signal_bridge: SignalBridge):
        self.bridge = signal_bridge
        self.students = {}
        self._next_id = 1
        self._running = False
        self._tcp_server = None
        self._udp_sender = UDPSender()
        self._udp_receiver = UDPReceiver(UDP_PORT)
        self._screen_capture = None
        self._audio_capture = None
        self._broadcasting = False
        self._audio_broadcasting = False
        self._lock = threading.Lock()
        self._audio_seq = 0

        # 获取本机IP
        self.server_ip = self._get_local_ip()

    def _get_local_ip(self):
        """获取本机局域网IP"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def start(self):
        """启动服务器"""
        self._running = True
        # 启动 TCP 监听
        self._tcp_server = TCPTransport()
        self._tcp_server.bind_and_listen(TCP_PORT)
        threading.Thread(target=self._accept_loop, daemon=True).start()
        # 启动 UDP 接收
        self._udp_receiver.on_frame(self._on_udp_frame)
        self._udp_receiver.start()
        self.bridge.log_message.emit(
            f"[服务器已启动] IP: {self.server_ip}  TCP端口: {TCP_PORT}  UDP端口: {UDP_PORT}")

    def stop(self):
        """停止服务器"""
        self._running = False
        self.stop_broadcast()
        self.stop_audio_broadcast()
        # 断开所有学生
        for sid, student in list(self.students.items()):
            try:
                student.tcp_socket.close()
            except Exception:
                pass
        self.students.clear()
        if self._tcp_server:
            self._tcp_server.close()
        self._udp_receiver.stop()
        self._udp_sender.close()

    def _accept_loop(self):
        """接受客户端连接"""
        while self._running:
            try:
                client_socket, addr = self._tcp_server.socket.accept()
                client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                threading.Thread(
                    target=self._handle_client,
                    args=(client_socket, addr),
                    daemon=True
                ).start()
            except Exception:
                break

    def _handle_client(self, client_socket, addr):
        """处理客户端连接"""
        receiver = MessageReceiver()
        student_id = None

        try:
            while self._running:
                data = client_socket.recv(65536)
                if not data:
                    break
                receiver.feed(data)
                for msg_type, payload in receiver.extract_messages():
                    if msg_type == MSG_REGISTER:
                        student_id = self._next_id
                        self._next_id += 1
                        name = payload.get("name", f"学生{student_id}")

                        with self._lock:
                            self.students[student_id] = StudentInfo(
                                student_id, name, client_socket, addr)

                        # 发送注册确认
                        ack = pack_message(MSG_REGISTER_ACK, {
                            "student_id": student_id,
                            "udp_port": self.students[student_id].udp_port,
                            "server_udp_port": UDP_PORT
                        })
                        client_socket.sendall(ack)

                        self.bridge.student_connected.emit(student_id, name, addr[0])
                        self.bridge.log_message.emit(f"[学生上线] {name} ({addr[0]}) ID:{student_id}")

                    elif msg_type == MSG_HEARTBEAT:
                        if student_id and student_id in self.students:
                            self.students[student_id].connected = True

                    elif msg_type == MSG_DISCONNECT:
                        break
        except Exception:
            pass
        finally:
            if student_id and student_id in self.students:
                with self._lock:
                    del self.students[student_id]
                self.bridge.student_disconnected.emit(student_id)
                self.bridge.log_message.emit(f"[学生下线] ID:{student_id}")
            try:
                client_socket.close()
            except Exception:
                pass

    def _on_udp_frame(self, frame_data, sender_addr):
        """接收学生发来的屏幕帧"""
        # 检查是否是测试包
        if frame_data == b"TEST_UDP_CONNECTIVITY":
            print(f"[服务器] 收到UDP测试包 from {sender_addr}")
            # 回复测试包
            try:
                self._udp_sender.send_frame(b"UDP_OK", sender_addr[0], sender_addr[1])
                print(f"[服务器] 回复UDP测试包到 {sender_addr}")
            except Exception as e:
                print(f"[服务器] 回复UDP测试包失败: {e}")
            return
        
        result = unpack_frame(frame_data)
        if result is None:
            return
        frame_type, seq, width, height, jpeg_data = result
        if frame_type == DATA_STUDENT_SCREEN:
            # 查找对应学生
            for sid, student in self.students.items():
                if student.ip == sender_addr[0]:
                    self.bridge.student_screen_frame.emit(sid, jpeg_data)
                    break

    # ==================== 教师广播 ====================

    def start_broadcast(self, target_fps=15):
        """开始教师屏幕广播"""
        if self._broadcasting:
            return
        self._broadcasting = True
        self._screen_capture = ScreenCapture(target_width=1280, target_height=720, quality=50)
        self._screen_capture.start_streaming(self._on_teacher_frame, target_fps)

        # 通知所有学生开始接收
        self._send_to_all(MSG_BROADCAST_START, {"udp_port": UDP_PORT})
        self.bridge.log_message.emit("[教师广播] 已开始屏幕广播")

    def stop_broadcast(self):
        """停止教师屏幕广播"""
        if not self._broadcasting:
            return
        self._broadcasting = False
        if self._screen_capture:
            self._screen_capture.stop_streaming()
            self._screen_capture = None
        self._send_to_all(MSG_BROADCAST_STOP)
        self.bridge.log_message.emit("[教师广播] 已停止屏幕广播")

    def _on_teacher_frame(self, jpeg_data, seq, width, height):
        """教师屏幕帧回调 - 广播给所有学生"""
        frame = pack_frame(jpeg_data, DATA_TEACHER_SCREEN, seq, width, height)
        targets = []
        for s in self.students.values():
            if s.connected:
                targets.append((s.ip, s.udp_port))
                print(f"[广播] 发送到 {s.name} ({s.ip}:{s.udp_port}) - 学生ID: {s.student_id}")
        if targets:
            print(f"[广播] 帧大小: {len(frame)} bytes, 目标数: {len(targets)}")
            # 直接发送而不使用send_to_multiple
            for host, port in targets:
                try:
                    self._udp_sender.send_frame(frame, host, port)
                    print(f"[广播] 已发送到 {host}:{port}")
                except Exception as e:
                    print(f"[广播] 发送到 {host}:{port} 失败: {e}")
        else:
            print("[广播] 没有在线学生")
        fps = self._screen_capture.fps if self._screen_capture else 0
        self.bridge.teacher_screen_stats.emit(
            f"广播中 | FPS: {fps} | 在线: {len(self.students)} | 帧大小: {len(jpeg_data)//1024}KB")

    # ==================== 音频广播 ====================

    def start_audio_broadcast(self):
        """开始音频广播"""
        if self._audio_broadcasting:
            return
        
        try:
            self._audio_capture = AudioCapture(
                sample_rate=44100,
                channels=2,
                chunk_size=4096
            )
            self._audio_broadcasting = True
            self._audio_capture.start_streaming(self._on_teacher_audio, target_fps=30)
            print("[音频广播] 音频广播已开始")
        except Exception as e:
            print(f"[音频广播] 启动失败: {e}")
            self._audio_broadcasting = False

    def stop_audio_broadcast(self):
        """停止音频广播"""
        if not self._audio_broadcasting:
            return
        self._audio_broadcasting = False
        if self._audio_capture:
            self._audio_capture.stop_streaming()
            self._audio_capture = None
        print("[音频广播] 音频广播已停止")

    def _on_teacher_audio(self, audio_data, timestamp):
        """教师音频帧回调 - 广播给所有学生"""
        self._audio_seq += 1
        frame = pack_audio_frame(
            audio_data, 
            sample_rate=self._audio_capture.sample_rate if self._audio_capture else 44100,
            channels=self._audio_capture.channels if self._audio_capture else 2,
            seq=self._audio_seq
        )
        
        targets = []
        for s in self.students.values():
            if s.connected:
                targets.append((s.ip, s.udp_port))
        
        if targets:
            for host, port in targets:
                try:
                    self._udp_sender.send_frame(frame, host, port)
                except Exception as e:
                    print(f"[音频广播] 发送到 {host}:{port} 失败: {e}")

    # ==================== 查看学生屏幕 ====================

    def request_student_screen(self, student_id):
        """请求查看某个学生的屏幕"""
        student = self.students.get(student_id)
        if student:
            student.is_screen_sharing = True
            self._send_to_student(student_id, MSG_SCREEN_REQ, {
                "server_udp_port": UDP_PORT
            })
            self.bridge.log_message.emit(f"[查看屏幕] 请求学生 {student.name} 的屏幕")

    def stop_student_screen(self, student_id):
        """停止查看学生屏幕"""
        student = self.students.get(student_id)
        if student:
            student.is_screen_sharing = False
            self._send_to_student(student_id, MSG_SCREEN_STOP)
            self.bridge.log_message.emit(f"[查看屏幕] 停止查看学生 {student.name} 的屏幕")

    # ==================== 远程控制 ====================

    def start_remote_control(self, student_id):
        """开始远程控制学生电脑"""
        student = self.students.get(student_id)
        if student:
            student.is_controlled = True
            self._send_to_student(student_id, MSG_REMOTE_CTRL)
            self.bridge.log_message.emit(f"[远程控制] 开始控制学生 {student.name} 的电脑")

    def stop_remote_control(self, student_id):
        """停止远程控制"""
        student = self.students.get(student_id)
        if student:
            student.is_controlled = False
            self._send_to_student(student_id, MSG_REMOTE_STOP)
            self.bridge.log_message.emit(f"[远程控制] 停止控制学生 {student.name} 的电脑")

    def send_remote_event(self, student_id, event_type, event_data):
        """发送远程控制事件（鼠标/键盘）"""
        self._send_to_student(student_id, MSG_REMOTE_CTRL, {
            "event_type": event_type,
            "event_data": event_data
        })

    # ==================== 课堂管理 ====================

    def lock_student_screen(self, student_id):
        """锁定学生屏幕"""
        student = self.students.get(student_id)
        if student:
            student.is_locked = True
            self._send_to_student(student_id, MSG_LOCK_SCREEN)
            self.bridge.log_message.emit(f"[屏幕锁定] 已锁定学生 {student.name} 的屏幕")

    def unlock_student_screen(self, student_id):
        """解锁学生屏幕"""
        student = self.students.get(student_id)
        if student:
            student.is_locked = False
            self._send_to_student(student_id, MSG_UNLOCK_SCREEN)
            self.bridge.log_message.emit(f"[屏幕解锁] 已解锁学生 {student.name} 的屏幕")

    def lock_all_screens(self):
        """锁定所有学生屏幕"""
        self._send_to_all(MSG_LOCK_SCREEN)
        for s in self.students.values():
            s.is_locked = True
        self.bridge.log_message.emit("[屏幕锁定] 已锁定所有学生屏幕")

    def unlock_all_screens(self):
        """解锁所有学生屏幕"""
        self._send_to_all(MSG_UNLOCK_SCREEN)
        for s in self.students.values():
            s.is_locked = False
        self.bridge.log_message.emit("[屏幕解锁] 已解锁所有学生屏幕")

    def send_message(self, student_id, message):
        """发送消息给学生"""
        if student_id == -1:
            self._send_to_all(MSG_SEND_MSG, {"message": message})
            self.bridge.log_message.emit(f"[消息] 广播消息: {message}")
        else:
            self._send_to_student(student_id, MSG_SEND_MSG, {"message": message})
            student = self.students.get(student_id)
            name = student.name if student else str(student_id)
            self.bridge.log_message.emit(f"[消息] 发送给 {name}: {message}")

    def shutdown_student(self, student_id):
        """远程关机"""
        self._send_to_student(student_id, MSG_SHUTDOWN, {"action": "shutdown"})
        student = self.students.get(student_id)
        name = student.name if student else str(student_id)
        self.bridge.log_message.emit(f"[远程关机] 已发送关机指令给 {name}")

    def restart_student(self, student_id):
        """远程重启"""
        self._send_to_student(student_id, MSG_SHUTDOWN, {"action": "restart"})
        student = self.students.get(student_id)
        name = student.name if student else str(student_id)
        self.bridge.log_message.emit(f"[远程重启] 已发送重启指令给 {name}")

    # ==================== 内部方法 ====================

    def _send_to_student(self, student_id, msg_type, payload=None):
        """发送消息给指定学生"""
        student = self.students.get(student_id)
        if student and student.connected:
            try:
                data = pack_message(msg_type, payload)
                student.tcp_socket.sendall(data)
            except Exception as e:
                self.bridge.log_message.emit(f"[发送错误] {e}")

    def _send_to_all(self, msg_type, payload=None):
        """广播消息给所有学生"""
        data = pack_message(msg_type, payload)
        for student in list(self.students.values()):
            if student.connected:
                try:
                    student.tcp_socket.sendall(data)
                except Exception:
                    pass


# ==================== 学生屏幕查看窗口 ====================
class StudentScreenWindow(QWidget):
    """查看学生屏幕的独立窗口"""

    def __init__(self, student_id, student_name, parent_server):
        super().__init__()
        self.student_id = student_id
        self.student_name = student_name
        self.parent_server = parent_server
        self._current_pixmap = None
        self._is_controlling = False

        self.setWindowTitle(f"学生屏幕 - {student_name} (ID:{student_id})")
        self.setMinimumSize(800, 600)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 工具栏
        toolbar = QHBoxLayout()

        self.btn_remote = QPushButton("🖥 开始远程控制")
        self.btn_remote.setCheckable(True)
        self.btn_remote.clicked.connect(self._toggle_remote_control)
        toolbar.addWidget(self.btn_remote)

        self.btn_fullscreen = QPushButton("⛶ 全屏")
        self.btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        toolbar.addWidget(self.btn_fullscreen)

        toolbar.addStretch()

        self.btn_close = QPushButton("✕ 关闭")
        self.btn_close.clicked.connect(self.closeEvent)
        toolbar.addWidget(self.btn_close)

        layout.addLayout(toolbar)

        # 屏幕显示区域
        self.screen_label = QLabel("等待屏幕数据...")
        self.screen_label.setAlignment(Qt.AlignCenter)
        self.screen_label.setStyleSheet("background-color: #1a1a2e; color: #888;")
        self.screen_label.setMinimumSize(640, 480)
        layout.addWidget(self.screen_label, 1)

        # 状态栏
        self.status_label = QLabel("就绪")
        layout.addWidget(self.status_label)

    def update_frame(self, jpeg_data):
        """更新屏幕帧"""
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
            self.status_label.setText(
                f"分辨率: {pixmap.width()}x{pixmap.height()} | "
                f"大小: {len(jpeg_data)//1024}KB")

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

    def _toggle_remote_control(self):
        """切换远程控制"""
        if self.btn_remote.isChecked():
            self._is_controlling = True
            self.btn_remote.setText("🖥 停止远程控制")
            self.parent_server.start_remote_control(self.student_id)
            self.screen_label.setCursor(Qt.CrossCursor)
        else:
            self._is_controlling = False
            self.btn_remote.setText("🖥 开始远程控制")
            self.parent_server.stop_remote_control(self.student_id)
            self.screen_label.setCursor(Qt.ArrowCursor)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def mousePressEvent(self, event):
        if self._is_controlling:
            self.parent_server.send_remote_event(self.student_id, "mouse_press", {
                "x": event.x(), "y": event.y(),
                "button": event.button()
            })

    def mouseReleaseEvent(self, event):
        if self._is_controlling:
            self.parent_server.send_remote_event(self.student_id, "mouse_release", {
                "x": event.x(), "y": event.y(),
                "button": event.button()
            })

    def mouseMoveEvent(self, event):
        if self._is_controlling:
            self.parent_server.send_remote_event(self.student_id, "mouse_move", {
                "x": event.x(), "y": event.y()
            })

    def keyPressEvent(self, event):
        if self._is_controlling:
            self.parent_server.send_remote_event(self.student_id, "key_press", {
                "key": event.key(), "text": event.text(),
                "modifiers": int(event.modifiers())
            })

    def keyReleaseEvent(self, event):
        if self._is_controlling:
            self.parent_server.send_remote_event(self.student_id, "key_release", {
                "key": event.key(), "text": event.text(),
                "modifiers": int(event.modifiers())
            })

    def closeEvent(self, event=None):
        """关闭窗口"""
        if self._is_controlling:
            self.parent_server.stop_remote_control(self.student_id)
        self.parent_server.stop_student_screen(self.student_id)
        self.close()


# ==================== 教师端主窗口 ====================
class TeacherMainWindow(QMainWindow):
    """教师端主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("局域网投屏系统 - 教师端")
        self.setMinimumSize(1200, 800)
        self._screen_windows = {}  # student_id -> StudentScreenWindow
        self._init_ui()
        self._init_server()

    def _init_ui(self):
        # 信号桥接
        self.bridge = SignalBridge()
        self.bridge.student_connected.connect(self._on_student_connected)
        self.bridge.student_disconnected.connect(self._on_student_disconnected)
        self.bridge.student_screen_frame.connect(self._on_student_frame)
        self.bridge.teacher_screen_stats.connect(self._on_broadcast_stats)
        self.bridge.log_message.connect(self._on_log)
        self.bridge.student_list_update.connect(self._refresh_student_list)

        # 主布局
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # ===== 左侧面板 =====
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_panel.setFixedWidth(320)

        # 服务器信息
        info_group = QGroupBox("服务器信息")
        info_layout = QGridLayout()
        self.lbl_ip = QLabel("启动中...")
        self.lbl_status = QLabel("● 未启动")
        self.lbl_status.setStyleSheet("color: red; font-weight: bold;")
        info_layout.addWidget(QLabel("IP地址:"), 0, 0)
        info_layout.addWidget(self.lbl_ip, 0, 1)
        info_layout.addWidget(QLabel("状态:"), 1, 0)
        info_layout.addWidget(self.lbl_status, 1, 1)
        info_group.setLayout(info_layout)
        left_layout.addWidget(info_group)

        # 学生列表
        student_group = QGroupBox(f"在线学生 (0)")
        student_group.setObjectName("studentGroup")
        student_layout = QVBoxLayout()
        self.student_list = QListWidget()
        self.student_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.student_list.customContextMenuRequested.connect(self._on_student_context_menu)
        self.student_list.itemDoubleClicked.connect(self._on_student_double_click)
        student_layout.addWidget(self.student_list)
        student_group.setLayout(student_layout)
        left_layout.addWidget(student_group, 1)

        # 控制按钮
        ctrl_group = QGroupBox("快捷操作")
        ctrl_layout = QGridLayout()

        self.btn_broadcast = QPushButton("📺 开始广播")
        self.btn_broadcast.setCheckable(True)
        self.btn_broadcast.clicked.connect(self._toggle_broadcast)
        self.btn_broadcast.setStyleSheet("""
            QPushButton { background-color: #2196F3; color: white; font-size: 14px;
                         padding: 8px; border-radius: 4px; font-weight: bold; }
            QPushButton:checked { background-color: #f44336; }
            QPushButton:hover { background-color: #1976D2; }
        """)
        ctrl_layout.addWidget(self.btn_broadcast, 0, 0, 1, 2)

        self.btn_audio = QPushButton("🔊 开始音频广播")
        self.btn_audio.setCheckable(True)
        self.btn_audio.clicked.connect(self._toggle_audio_broadcast)
        self.btn_audio.setStyleSheet("""
            QPushButton { background-color: #4CAF50; color: white; font-size: 14px;
                         padding: 8px; border-radius: 4px; font-weight: bold; }
            QPushButton:checked { background-color: #f44336; }
            QPushButton:hover { background-color: #45a049; }
        """)
        ctrl_layout.addWidget(self.btn_audio, 1, 0, 1, 2)

        self.btn_lock_all = QPushButton("🔒 锁定全部屏幕")
        self.btn_lock_all.clicked.connect(self._lock_all_screens)
        ctrl_layout.addWidget(self.btn_lock_all, 2, 0)

        self.btn_unlock_all = QPushButton("🔓 解锁全部屏幕")
        self.btn_unlock_all.clicked.connect(self._unlock_all_screens)
        ctrl_layout.addWidget(self.btn_unlock_all, 2, 1)

        self.btn_send_msg = QPushButton("💬 发送消息")
        self.btn_send_msg.clicked.connect(self._send_message)
        ctrl_layout.addWidget(self.btn_send_msg, 3, 0, 1, 2)

        ctrl_group.setLayout(ctrl_layout)
        left_layout.addWidget(ctrl_group)

        main_layout.addWidget(left_panel)

        # ===== 右侧面板 =====
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        # 广播预览 / 消息日志 标签页
        self.tabs = QTabWidget()

        # 广播预览标签
        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        self.broadcast_stats = QLabel("未开始广播")
        self.broadcast_stats.setStyleSheet("color: #666; padding: 4px;")
        preview_layout.addWidget(self.broadcast_stats)
        self.preview_label = QLabel("教师屏幕预览")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet("background-color: #1a1a2e; color: #555;")
        self.preview_label.setMinimumSize(640, 480)
        preview_layout.addWidget(self.preview_label, 1)
        self.tabs.addTab(preview_widget, "📺 广播预览")

        # 消息日志标签
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("""
            QTextEdit { background-color: #1e1e2e; color: #cdd6f4;
                       font-family: Consolas, monospace; font-size: 12px; }
        """)
        log_layout.addWidget(self.log_text)
        self.tabs.addTab(log_widget, "📋 消息日志")

        right_layout.addWidget(self.tabs)

        # 底部消息发送栏
        msg_layout = QHBoxLayout()
        msg_layout.addWidget(QLabel("消息:"))
        self.msg_input = QLineEdit()
        self.msg_input.setPlaceholderText("输入要发送给学生的消息...")
        self.msg_input.returnPressed.connect(self._send_message)
        msg_layout.addWidget(self.msg_input, 1)
        self.msg_target = QComboBox()
        self.msg_target.addItem("广播给全部")
        msg_layout.addWidget(self.msg_target)
        right_layout.addLayout(msg_layout)

        main_layout.addWidget(right_panel, 1)

        # 状态栏
        self.statusBar().showMessage("就绪")

        # 样式
        self.setStyleSheet("""
            QMainWindow { background-color: #f5f5f5; }
            QGroupBox { font-weight: bold; border: 1px solid #ddd;
                       border-radius: 6px; margin-top: 8px; padding-top: 16px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPushButton { padding: 6px 12px; border-radius: 4px; border: 1px solid #ccc; }
            QPushButton:hover { background-color: #e0e0e0; }
            QListWidget { border: 1px solid #ddd; border-radius: 4px; }
            QListWidget::item { padding: 6px; }
            QListWidget::item:selected { background-color: #2196F3; color: white; }
        """)

    def _init_server(self):
        """初始化服务器"""
        self.server = ServerCore(self.bridge)
        self.server.start()
        self.lbl_ip.setText(self.server.server_ip)
        self.lbl_status.setText("● 运行中")
        self.lbl_status.setStyleSheet("color: green; font-weight: bold; font-size: 14px;")

        # 心跳检测定时器
        self._heartbeat_timer = QTimer()
        self._heartbeat_timer.timeout.connect(self._check_heartbeats)
        self._heartbeat_timer.start(5000)

    # ==================== 事件处理 ====================

    def _on_student_connected(self, student_id, name, ip):
        """学生上线"""
        self.bridge.log_message.emit(f"学生 {name} (ID:{student_id}) 从 {ip} 上线")
        self._refresh_student_list()

    def _on_student_disconnected(self, student_id):
        """学生下线"""
        self.bridge.log_message.emit(f"学生 ID:{student_id} 已下线")
        # 关闭对应的屏幕查看窗口
        if student_id in self._screen_windows:
            self._screen_windows[student_id].close()
            del self._screen_windows[student_id]
        self._refresh_student_list()

    def _on_student_frame(self, student_id, jpeg_data):
        """收到学生屏幕帧"""
        if student_id in self._screen_windows:
            self._screen_windows[student_id].update_frame(jpeg_data)

    def _on_broadcast_stats(self, stats):
        """更新广播统计"""
        self.broadcast_stats.setText(stats)
        # 同时更新预览
        if self.server._screen_capture:
            frame = self.server._screen_capture.capture_frame()
            pixmap = QPixmap()
            pixmap.loadFromData(frame)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.preview_label.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                self.preview_label.setPixmap(scaled)

    def _on_log(self, message):
        """添加日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f'<span style="color:#888;">[{timestamp}]</span> {message}')

    def _refresh_student_list(self):
        """刷新学生列表"""
        self.student_list.clear()
        self.msg_target.clear()
        self.msg_target.addItem("广播给全部")

        group = self.findChild(QGroupBox, "studentGroup")
        if group:
            group.setTitle(f"在线学生 ({len(self.server.students)})")

        for sid, student in sorted(self.server.students.items()):
            status = "🔒" if student.is_locked else ("📺" if student.is_screen_sharing else "🖥")
            text = f"{status} {student.name} ({student.ip})"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, sid)
            self.student_list.addItem(item)
            self.msg_target.addItem(f"{student.name} (ID:{sid})", sid)

    def _on_student_context_menu(self, pos):
        """学生右键菜单"""
        item = self.student_list.itemAt(pos)
        if not item:
            return
        student_id = item.data(Qt.UserRole)
        student = self.server.students.get(student_id)
        if not student:
            return

        menu = QMenu(self)
        menu.addAction(f"📺 查看屏幕 - {student.name}",
                       lambda: self._view_student_screen(student_id))
        menu.addAction(f"🖥 远程控制 - {student.name}",
                       lambda: self._remote_control_student(student_id))
        menu.addSeparator()
        if student.is_locked:
            menu.addAction(f"🔓 解锁屏幕",
                           lambda: self._unlock_screen(student_id))
        else:
            menu.addAction(f"🔒 锁定屏幕",
                           lambda: self._lock_screen(student_id))
        menu.addSeparator()
        menu.addAction(f"💬 发送消息",
                       lambda: self._send_message_to(student_id))
        menu.addSeparator()
        menu.addAction(f"⚠ 关机",
                       lambda: self._shutdown_student(student_id))
        menu.addAction(f"⚠ 重启",
                       lambda: self._restart_student(student_id))
        menu.addSeparator()
        menu.addAction("断开连接",
                       lambda: self._disconnect_student(student_id))
        menu.exec_(self.student_list.mapToGlobal(pos))

    def _on_student_double_click(self, item):
        """双击学生 - 查看屏幕"""
        student_id = item.data(Qt.UserRole)
        self._view_student_screen(student_id)

    # ==================== 操作方法 ====================

    def _toggle_broadcast(self, checked):
        if checked:
            self.btn_broadcast.setText("⏹ 停止广播")
            self.server.start_broadcast(target_fps=15)
        else:
            self.btn_broadcast.setText("📺 开始广播")
            self.server.stop_broadcast()
            self.preview_label.clear()
            self.preview_label.setText("教师屏幕预览")
            self.broadcast_stats.setText("未开始广播")

    def _toggle_audio_broadcast(self, checked):
        """切换音频广播"""
        if checked:
            self.btn_audio.setText("⏹ 停止音频广播")
            self.server.start_audio_broadcast()
            self.bridge.log_message.emit("开始音频广播")
        else:
            self.btn_audio.setText("🔊 开始音频广播")
            self.server.stop_audio_broadcast()
            self.bridge.log_message.emit("停止音频广播")

    def _view_student_screen(self, student_id):
        """查看学生屏幕"""
        if student_id in self._screen_windows:
            self._screen_windows[student_id].raise_()
            self._screen_windows[student_id].activateWindow()
            return
        student = self.server.students.get(student_id)
        if not student:
            return
        window = StudentScreenWindow(student_id, student.name, self.server)
        self._screen_windows[student_id] = window
        window.show()
        self.server.request_student_screen(student_id)

    def _remote_control_student(self, student_id):
        """远程控制学生"""
        self._view_student_screen(student_id)
        if student_id in self._screen_windows:
            self._screen_windows[student_id].btn_remote.setChecked(True)

    def _lock_screen(self, student_id):
        self.server.lock_student_screen(student_id)
        self._refresh_student_list()

    def _unlock_screen(self, student_id):
        self.server.unlock_student_screen(student_id)
        self._refresh_student_list()

    def _lock_all_screens(self):
        self.server.lock_all_screens()
        self._refresh_student_list()

    def _unlock_all_screens(self):
        self.server.unlock_all_screens()
        self._refresh_student_list()

    def _send_message(self):
        message = self.msg_input.text().strip()
        if not message:
            return
        idx = self.msg_target.currentIndex()
        if idx == 0:
            self.server.send_message(-1, message)
        else:
            student_id = self.msg_target.itemData(idx)
            self.server.send_message(student_id, message)
        self.msg_input.clear()

    def _send_message_to(self, student_id):
        message, ok = QInputDialog.getText(self, "发送消息", "请输入消息内容:")
        if ok and message.strip():
            self.server.send_message(student_id, message.strip())

    def _shutdown_student(self, student_id):
        reply = QMessageBox.question(
            self, "确认关机",
            f"确定要远程关闭该学生电脑吗？\n此操作不可撤销！",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.server.shutdown_student(student_id)

    def _restart_student(self, student_id):
        reply = QMessageBox.question(
            self, "确认重启",
            f"确定要远程重启该学生电脑吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.server.restart_student(student_id)

    def _disconnect_student(self, student_id):
        self.server.stop_student_screen(student_id)
        self.server.stop_remote_control(student_id)
        if student_id in self._screen_windows:
            self._screen_windows[student_id].close()
            del self._screen_windows[student_id]

    def _check_heartbeats(self):
        """检查学生连接状态"""
        self._refresh_student_list()

    def closeEvent(self, event):
        """关闭窗口"""
        reply = QMessageBox.question(
            self, "确认退出",
            "确定要关闭服务器吗？\n所有学生将断开连接。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            # 关闭所有屏幕窗口
            for window in list(self._screen_windows.values()):
                window.close()
            self.server.stop()
            event.accept()
        else:
            event.ignore()


# ==================== 入口 ====================
def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 9))

    window = TeacherMainWindow()
    window.show()

    # 居中显示
    screen = QDesktopWidget().availableGeometry()
    window.move((screen.width() - window.width()) // 2,
                (screen.height() - window.height()) // 2)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
