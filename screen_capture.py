# -*- coding: utf-8 -*-
"""
屏幕采集模块
使用 PIL + mss 进行高效屏幕截图
"""

import io
import time
import threading
from PIL import Image


class ScreenCapture:
    """屏幕采集器"""

    def __init__(self, target_width=1280, target_height=720, quality=60):
        """
        Args:
            target_width:  目标宽度（缩放后）
            target_height: 目标高度（缩放后）
            quality:       JPEG 压缩质量 (1-100)
        """
        self.target_width = target_width
        self.target_height = target_height
        self.quality = quality
        self._running = False
        self._capture_thread = None
        self._callback = None
        self._fps = 0
        self._frame_count = 0
        self._last_fps_time = time.time()

        # 尝试导入 mss（更快），回退到 PIL
        try:
            import mss
            self._mss = mss.mss()
            self._use_mss = True
        except ImportError:
            self._mss = None
            self._use_mss = False

    def capture_frame(self):
        """捕获一帧屏幕，返回 JPEG 字节流"""
        if self._use_mss:
            return self._capture_mss()
        else:
            return self._capture_pil()

    def _capture_mss(self):
        """使用 mss 捕获屏幕（性能更好）"""
        monitor = self._mss.monitors[1]  # 主显示器
        img = self._mss.grab(monitor)
        image = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
        return self._resize_and_encode(image)

    def _capture_pil(self):
        """使用 PIL ImageGrab 捕获屏幕"""
        from PIL import ImageGrab
        image = ImageGrab.grab()
        return self._resize_and_encode(image)

    def _resize_and_encode(self, image: Image.Image) -> bytes:
        """缩放并编码为 JPEG"""
        # 计算缩放比例，保持宽高比
        w, h = image.size
        ratio = min(self.target_width / w, self.target_height / h)
        if ratio < 1:
            new_w = int(w * ratio)
            new_h = int(h * ratio)
            image = image.resize((new_w, new_h), Image.LANCZOS)

        # 编码为 JPEG
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=self.quality)
        return buffer.getvalue()

    def get_screen_size(self):
        """获取当前屏幕分辨率"""
        if self._use_mss:
            monitor = self._mss.monitors[1]
            return monitor["width"], monitor["height"]
        else:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            return img.size

    def start_streaming(self, callback, target_fps=15):
        """
        启动持续屏幕采集线程

        Args:
            callback: 回调函数 callback(jpeg_bytes, seq, width, height)
            target_fps: 目标帧率
        """
        self._running = True
        self._callback = callback
        self._capture_thread = threading.Thread(target=self._stream_loop,
                                                args=(target_fps,),
                                                daemon=True)
        self._capture_thread.start()

    def _stream_loop(self, target_fps):
        """采集循环"""
        seq = 0
        interval = 1.0 / target_fps
        width, height = self.get_screen_size()

        while self._running:
            start = time.time()
            try:
                frame_data = self.capture_frame()
                seq += 1
                if self._callback:
                    self._callback(frame_data, seq, width, height)

                # 计算 FPS
                self._frame_count += 1
                now = time.time()
                if now - self._last_fps_time >= 1.0:
                    self._fps = self._frame_count
                    self._frame_count = 0
                    self._last_fps_time = now
            except Exception as e:
                print(f"[屏幕采集错误] {e}")

            # 帧率控制
            elapsed = time.time() - start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def stop_streaming(self):
        """停止采集"""
        self._running = False
        if self._capture_thread:
            self._capture_thread.join(timeout=3)

    @property
    def fps(self):
        return self._fps

    def cleanup(self):
        """清理资源"""
        self.stop_streaming()
        if self._mss:
            self._mss.close()
