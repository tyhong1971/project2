# -*- coding: utf-8 -*-
"""
通信协议定义模块
定义客户端与服务器之间的消息类型和数据格式
"""

import struct
import json
import zlib

# ==================== 消息类型常量 ====================
# 控制消息 (TCP)
MSG_REGISTER        = "register"          # 学生注册
MSG_REGISTER_ACK    = "register_ack"      # 注册确认
MSG_HEARTBEAT       = "heartbeat"         # 心跳
MSG_BROADCAST_START = "broadcast_start"   # 教师开始广播
MSG_BROADCAST_STOP  = "broadcast_stop"    # 教师停止广播
MSG_SCREEN_REQ      = "screen_request"    # 请求学生屏幕
MSG_SCREEN_STOP     = "screen_stop"       # 停止接收学生屏幕
MSG_REMOTE_CTRL     = "remote_control"    # 远程控制指令
MSG_REMOTE_STOP     = "remote_stop"       # 停止远程控制
MSG_LOCK_SCREEN     = "lock_screen"       # 锁定学生屏幕
MSG_UNLOCK_SCREEN   = "unlock_screen"     # 解锁学生屏幕
MSG_SEND_MSG        = "send_message"      # 发送消息通知
MSG_FILE_TRANSFER   = "file_transfer"     # 文件分发
MSG_SHUTDOWN        = "shutdown"          # 关机/重启指令
MSG_STUDENT_LIST    = "student_list"      # 学生列表更新
MSG_DISCONNECT      = "disconnect"        # 断开连接

# 数据消息类型标识 (UDP 数据包头)
DATA_TEACHER_SCREEN = 0x01  # 教师屏幕帧
DATA_STUDENT_SCREEN = 0x02  # 学生屏幕帧
DATA_TEACHER_AUDIO = 0x03   # 教师音频帧
DATA_STUDENT_AUDIO = 0x04   # 学生音频帧


# ==================== 消息打包/解包 ====================

def pack_message(msg_type: str, payload: dict = None) -> bytes:
    """
    将消息打包为二进制格式: [4字节长度][JSON消息体]
    """
    msg = {"type": msg_type}
    if payload:
        msg.update(payload)
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    length = struct.pack("!I", len(body))
    return length + body


def unpack_message(data: bytes):
    """
    解包消息，返回 (msg_type, payload_dict) 或 None
    """
    try:
        if len(data) < 4:
            return None
        length = struct.unpack("!I", data[:4])[0]
        body = data[4:4 + length]
        msg = json.loads(body.decode("utf-8"))
        return msg.get("type"), msg
    except Exception:
        return None


def pack_frame(frame_data: bytes, frame_type: int = DATA_TEACHER_SCREEN,
               seq: int = 0, width: int = 0, height: int = 0) -> bytes:
    """
    打包屏幕帧: [1字节类型][4字节序号][4字节宽][4字节高][4字节压缩长度][4字节原始长度][压缩数据]
    """
    compressed = zlib.compress(frame_data, level=1)
    header = struct.pack("!BIIIII",
                         frame_type,
                         seq,
                         width,
                         height,
                         len(compressed),
                         len(frame_data))
    return header + compressed


def unpack_frame(data: bytes):
    """
    解包屏幕帧，返回 (frame_type, seq, width, height, frame_data) 或 None
    """
    try:
        if len(data) < 21:
            return None
        frame_type, seq, width, height, comp_len, orig_len = struct.unpack("!BIIIII", data[:21])
        compressed = data[21:21 + comp_len]
        frame_data = zlib.decompress(compressed)
        return frame_type, seq, width, height, frame_data
    except Exception:
        return None


def pack_audio_frame(audio_data: bytes, sample_rate: int = 44100, 
                    channels: int = 2, seq: int = 0) -> bytes:
    """
    打包音频帧: [1字节类型][4字节序号][4字节采样率][4字节声道数][4字节音频数据长度][音频数据]
    """
    header = struct.pack("!BIIII",
                        DATA_TEACHER_AUDIO,
                        seq,
                        sample_rate,
                        channels,
                        len(audio_data))
    return header + audio_data


def unpack_audio_frame(data: bytes):
    """
    解包音频帧，返回 (frame_type, seq, sample_rate, channels, audio_data) 或 None
    """
    try:
        if len(data) < 17:
            return None
        frame_type, seq, sample_rate, channels, audio_len = struct.unpack("!BIIII", data[:17])
        audio_data = data[17:17 + audio_len]
        return frame_type, seq, sample_rate, channels, audio_data
    except Exception:
        return None


# ==================== TCP 消息接收器 ====================

class MessageReceiver:
    """TCP 流式消息接收器，处理粘包问题"""

    def __init__(self):
        self.buffer = b""

    def feed(self, data: bytes):
        """喂入新数据"""
        self.buffer += data

    def extract_messages(self):
        """从缓冲区提取所有完整消息"""
        messages = []
        while len(self.buffer) >= 4:
            length = struct.unpack("!I", self.buffer[:4])[0]
            if length > 10 * 1024 * 1024:  # 单条消息不超过10MB
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
