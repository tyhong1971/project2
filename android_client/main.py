# -*- coding: utf-8 -*-
"""
安卓投屏客户端 - Kivy UI
支持：屏幕接收、音频接收、消息通知、屏幕锁定
"""

from kivy.app import App
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.image import Image
from kivy.uix.popup import Popup
from kivy.uix.modalview import ModalView
from kivy.core.image import Image as CoreImage
from kivy.clock import Clock
from kivy.graphics import Color, Rectangle
from kivy.properties import StringProperty, BooleanProperty, ObjectProperty
from kivy.core.audio import SoundLoader

from io import BytesIO
import threading

from android_client import AndroidClientCore


# ==================== 连接界面 ====================

class ConnectScreen(Screen):
    """连接界面"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._init_ui()

    def _init_ui(self):
        layout = BoxLayout(orientation='vertical', padding=30, spacing=20)

        # 标题
        title = Label(
            text='[size=28][b]局域网投屏系统[/b][/size]\n[size=18]安卓客户端[/size]',
            markup=True,
            size_hint_y=0.25
        )
        layout.add_widget(title)

        # 表单
        form = GridLayout(cols=2, spacing=15, size_hint_y=0.3)
        form.add_widget(Label(text='教师IP:', font_size=18, size_hint_x=0.3))
        self.ip_input = TextInput(
            hint_text='例如: 192.168.1.100',
            font_size=18,
            multiline=False,
            size_hint_x=0.7
        )
        form.add_widget(self.ip_input)

        form.add_widget(Label(text='学生姓名:', font_size=18, size_hint_x=0.3))
        self.name_input = TextInput(
            hint_text='请输入姓名',
            font_size=18,
            multiline=False,
            size_hint_x=0.7
        )
        form.add_widget(self.name_input)
        layout.add_widget(form)

        # 连接按钮
        self.btn_connect = Button(
            text='连接',
            font_size=22,
            size_hint_y=0.15,
            background_color=(0.13, 0.59, 0.95, 1)
        )
        self.btn_connect.bind(on_press=self._on_connect)
        layout.add_widget(self.btn_connect)

        # 提示
        hint = Label(
            text='请确保与教师在同一局域网内',
            font_size=14,
            size_hint_y=0.1,
            color=(0.5, 0.5, 0.5, 1)
        )
        layout.add_widget(hint)

        # 版本信息
        version = Label(
            text='v1.0 | Kivy',
            font_size=12,
            size_hint_y=0.1,
            color=(0.7, 0.7, 0.7, 1)
        )
        layout.add_widget(version)

        self.add_widget(layout)

    def _on_connect(self, instance):
        ip = self.ip_input.text.strip()
        name = self.name_input.text.strip()

        if not ip:
            self._show_popup('提示', '请输入教师IP地址')
            return
        if not name:
            self._show_popup('提示', '请输入学生姓名')
            return

        # 切换到主界面并连接
        self.manager.current = 'main'
        self.manager.get_screen('main').connect(ip, name)

    def _show_popup(self, title, message):
        popup = Popup(
            title=title,
            content=Label(text=message, font_size=16),
            size_hint=(0.7, 0.3)
        )
        popup.open()


# ==================== 主界面 ====================

class MainScreen(Screen):
    """主界面 - 接收投屏"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.client = None
        self._current_texture = None
        self._audio_enabled = True
        self._init_ui()

    def _init_ui(self):
        # 主布局
        self.layout = BoxLayout(orientation='vertical', padding=10, spacing=10)

        # 顶部状态栏
        top_bar = BoxLayout(size_hint_y=0.08, spacing=10)
        self.lbl_status = Label(
            text='● 未连接',
            font_size=16,
            color=(1, 0, 0, 1),
            size_hint_x=0.4
        )
        top_bar.add_widget(self.lbl_status)

        self.lbl_info = Label(
            text='',
            font_size=14,
            color=(0.5, 0.5, 0.5, 1),
            size_hint_x=0.6
        )
        top_bar.add_widget(self.lbl_info)
        self.layout.add_widget(top_bar)

        # 屏幕显示区域
        self.screen_image = Image(
            source='',
            allow_stretch=True,
            keep_ratio=True,
            size_hint_y=0.75
        )
        # 设置默认背景
        with self.screen_image.canvas.before:
            Color(0.1, 0.1, 0.18, 1)
            self._bg_rect = Rectangle(pos=self.screen_image.pos, size=self.screen_image.size)
        self.screen_image.bind(pos=self._update_bg, size=self._update_bg)

        self.layout.add_widget(self.screen_image)

        # 状态信息栏
        status_bar = BoxLayout(size_hint_y=0.06, spacing=10)
        self.lbl_broadcast = Label(
            text='等待教师广播...',
            font_size=14,
            color=(0.5, 0.5, 0.5, 1)
        )
        status_bar.add_widget(self.lbl_broadcast)
        self.layout.add_widget(status_bar)

        # 消息栏
        self.msg_bar = BoxLayout(size_hint_y=0.06, spacing=5)
        with self.msg_bar.canvas.before:
            Color(0.13, 0.59, 0.95, 1)
            self._msg_bg = Rectangle(pos=self.msg_bar.pos, size=self.msg_bar.size)
        self.msg_bar.bind(pos=self._update_msg_bg, size=self._update_msg_bg)

        self.lbl_message = Label(
            text='',
            font_size=14,
            color=(1, 1, 1, 1)
        )
        self.msg_bar.add_widget(self.lbl_message)
        self.msg_bar.opacity = 0
        self.layout.add_widget(self.msg_bar)

        # 底部控制栏
        bottom_bar = BoxLayout(size_hint_y=0.05, spacing=10)
        self.btn_audio = Button(
            text='🔊 音频',
            font_size=14,
            size_hint_x=0.3,
            background_color=(0.3, 0.69, 0.31, 1)
        )
        self.btn_audio.bind(on_press=self._toggle_audio)
        bottom_bar.add_widget(self.btn_audio)

        self.btn_disconnect = Button(
            text='断开连接',
            font_size=14,
            size_hint_x=0.3,
            background_color=(0.96, 0.26, 0.21, 1)
        )
        self.btn_disconnect.bind(on_press=self._on_disconnect)
        bottom_bar.add_widget(self.btn_disconnect)

        self.btn_exit = Button(
            text='退出',
            font_size=14,
            size_hint_x=0.3,
            background_color=(0.5, 0.5, 0.5, 1)
        )
        self.btn_exit.bind(on_press=self._on_exit)
        bottom_bar.add_widget(self.btn_exit)

        self.layout.add_widget(bottom_bar)

        self.add_widget(self.layout)

        # 消息隐藏定时器
        self._msg_timer = None

    def _update_bg(self, instance, value):
        self._bg_rect.pos = instance.pos
        self._bg_rect.size = instance.size

    def _update_msg_bg(self, instance, value):
        self._msg_bg.pos = instance.pos
        self._msg_bg.size = instance.size

    def connect(self, server_ip, student_name):
        """连接到服务器"""
        # 创建客户端
        callbacks = {
            'on_connected': self._on_connected,
            'on_disconnected': self._on_disconnected,
            'on_screen_frame': self._on_screen_frame,
            'on_audio_frame': self._on_audio_frame,
            'on_message': self._on_message,
            'on_screen_locked': self._on_screen_locked,
            'on_screen_unlocked': self._on_screen_unlocked,
            'on_log': self._on_log
        }
        self.client = AndroidClientCore(callbacks)
        self.client.connect(server_ip, student_name)

    def _on_connected(self, server_ip, student_id):
        """连接成功"""
        Clock.schedule_once(lambda dt: self._update_connected_ui(server_ip, student_id), 0)

    def _update_connected_ui(self, server_ip, student_id):
        self.lbl_status.text = '● 已连接'
        self.lbl_status.color = (0, 0.8, 0, 1)
        self.lbl_info.text = f'服务器: {server_ip} | ID: {student_id}'

    def _on_disconnected(self, reason):
        """断开连接"""
        Clock.schedule_once(lambda dt: self._update_disconnected_ui(reason), 0)

    def _update_disconnected_ui(self, reason):
        self.lbl_status.text = '● 已断开'
        self.lbl_status.color = (1, 0, 0, 1)
        self.lbl_info.text = f'原因: {reason}'
        self.lbl_broadcast.text = '等待教师广播...'

        # 显示提示
        popup = Popup(
            title='连接断开',
            content=Label(text=f'已断开连接\n{reason}', font_size=16),
            size_hint=(0.8, 0.3)
        )
        popup.open()

    def _on_screen_frame(self, jpeg_data):
        """收到屏幕帧"""
        Clock.schedule_once(lambda dt: self._update_screen(jpeg_data), 0)

    def _update_screen(self, jpeg_data):
        """更新屏幕显示"""
        try:
            # 从 JPEG 数据创建纹理
            img_data = BytesIO(jpeg_data)
            texture = CoreImage(img_data, ext='jpg').texture
            if texture:
                self.screen_image.texture = texture
                self.lbl_broadcast.text = '📺 正在接收教师广播...'
        except Exception as e:
            print(f"[屏幕更新错误] {e}")

    def _on_audio_frame(self, audio_data, sample_rate, channels):
        """收到音频帧"""
        if self._audio_enabled and self.client:
            self.client.audio_player.play_audio(audio_data, sample_rate, channels)

    def _on_message(self, message):
        """收到消息"""
        Clock.schedule_once(lambda dt: self._show_message(message), 0)

    def _show_message(self, message):
        """显示消息"""
        self.lbl_message.text = f'💬 教师消息: {message}'
        self.msg_bar.opacity = 1

        # 取消之前的定时器
        if self._msg_timer:
            Clock.unschedule(self._msg_timer)
        # 8秒后隐藏
        self._msg_timer = Clock.schedule_once(
            lambda dt: setattr(self.msg_bar, 'opacity', 0), 8
        )

    def _on_screen_locked(self):
        """屏幕被锁定"""
        Clock.schedule_once(lambda dt: self._show_lock_overlay(), 0)

    def _show_lock_overlay(self):
        """显示锁定遮罩"""
        self._lock_overlay = ModalView(auto_dismiss=False, size_hint=(1, 1))
        layout = BoxLayout(orientation='vertical', padding=50)

        lock_icon = Label(
            text='🔒',
            font_size=80,
            size_hint_y=0.4
        )
        layout.add_widget(lock_icon)

        lock_text = Label(
            text='屏幕已被教师锁定\n请等待教师解锁...',
            font_size=24,
            size_hint_y=0.4
        )
        layout.add_widget(lock_text)

        self._lock_overlay.add_widget(layout)
        self._lock_overlay.open()

    def _on_screen_unlocked(self):
        """屏幕解锁"""
        Clock.schedule_once(lambda dt: self._hide_lock_overlay(), 0)

    def _hide_lock_overlay(self):
        """隐藏锁定遮罩"""
        if hasattr(self, '_lock_overlay') and self._lock_overlay:
            self._lock_overlay.dismiss()
            self._lock_overlay = None

    def _on_log(self, message):
        """日志"""
        print(f"[LOG] {message}")

    def _toggle_audio(self, instance):
        """切换音频"""
        self._audio_enabled = not self._audio_enabled
        if self._audio_enabled:
            self.btn_audio.text = '🔊 音频'
            self.btn_audio.background_color = (0.3, 0.69, 0.31, 1)
        else:
            self.btn_audio.text = '🔇 静音'
            self.btn_audio.background_color = (0.5, 0.5, 0.5, 1)

    def _on_disconnect(self, instance):
        """断开连接"""
        if self.client:
            self.client.disconnect()
        self.manager.current = 'connect'
        self.lbl_status.text = '● 未连接'
        self.lbl_status.color = (1, 0, 0, 1)
        self.lbl_info.text = ''
        self.screen_image.texture = None

    def _on_exit(self, instance):
        """退出应用"""
        if self.client:
            self.client.disconnect()
        App.get_running_app().stop()


# ==================== 应用入口 ====================

class ScreenCastApp(App):
    """安卓投屏客户端应用"""

    def build(self):
        self.title = '局域网投屏'

        # 创建屏幕管理器
        sm = ScreenManager()
        sm.add_widget(ConnectScreen(name='connect'))
        sm.add_widget(MainScreen(name='main'))

        return sm

    def on_stop(self):
        """应用退出时清理"""
        main_screen = self.root.get_screen('main')
        if main_screen.client:
            main_screen.client.disconnect()


# ==================== 入口 ====================

if __name__ == '__main__':
    ScreenCastApp().run()
 
 