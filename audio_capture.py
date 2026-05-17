# -*- coding: utf-8 -*-
"""
音频捕获和播放模块
使用 sounddevice 和 soundcard 进行跨平台音频捕获和播放
"""

import io
import time
import threading
import queue
import numpy as np


class AudioCapture:
    """音频捕获器 - 从系统输出捕获音频"""

    def __init__(self, sample_rate=44100, channels=2, chunk_size=4096):
        """
        Args:
            sample_rate: 采样率 (Hz)
            channels: 声道数 (1=单声道, 2=立体声)
            chunk_size: 每次捕获的样本数
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self._running = False
        self._capture_thread = None
        self._callback = None
        self._audio_queue = queue.Queue(maxsize=10)
        self._sd = None
        self._sc = None
        self._virtual_mic = None
        self._capture_method = "none"
        self._initialize_audio()

    def _initialize_audio(self):
        """初始化音频设备 - 优先尝试系统音频捕获"""
        try:
            import soundcard as sc
            self._sc = sc
            print(f"[音频捕获] 已加载 soundcard 库")
            
            # 获取所有扬声器设备
            speakers = sc.all_speakers()
            print(f"[音频捕获] 发现 {len(speakers)} 个扬声器设备")
            for i, spk in enumerate(speakers):
                print(f"  [{i}] {spk.name}")
            
            # 获取默认扬声器
            speaker = sc.default_speaker()
            if speaker:
                print(f"[音频捕获] 默认扬声器: {speaker.name}")
                
                # 创建虚拟麦克风来捕获扬声器输出（Loopback）
                try:
                    self._virtual_mic = speaker.recorder(self.sample_rate, channels=self.channels)
                    if self._virtual_mic:
                        print(f"[音频捕获] 已启用系统音频捕获 (WASAPI Loopback)")
                        self._capture_method = "system"
                        return
                except Exception as e:
                    print(f"[音频捕获] WASAPI Loopback 创建失败: {e}")
                    
        except ImportError:
            print(f"[音频捕获] soundcard 库未安装，请运行: pip install soundcard")
        except Exception as e:
            print(f"[音频捕获] 系统音频捕获初始化错误: {e}")
        
        print(f"[音频捕获] 回退到麦克风输入")
        try:
            import sounddevice as sd
            self._sd = sd
            print(f"[音频捕获] 已加载 sounddevice 库")
            
            # 检查可用输入设备
            devices = sd.query_devices()
            print(f"[音频捕获] 可用输入设备:")
            if isinstance(devices, dict):
                devices = [devices]
            for i, dev in enumerate(devices):
                if dev.get('max_input_channels', 0) > 0:
                    print(f"  [{i}] {dev.get('name', 'Unknown')}")
            
            self._capture_method = "microphone"
            
        except Exception as e:
            print(f"[音频捕获] 音频库加载错误: {e}")

    def capture_frame(self):
        """捕获一帧音频数据 - 优先捕获系统音频"""
        if self._virtual_mic is not None:
            try:
                audio_data = self._virtual_mic.record(self.chunk_size)
                if audio_data is not None and len(audio_data) > 0:
                    audio_data = audio_data.astype(np.float32)
                    volume = np.abs(audio_data).mean()
                    if volume > 0.001:
                        print(f"[音频捕获] 系统音频，音量: {volume:.6f}")
                    return audio_data.tobytes()
            except Exception as e:
                print(f"[音频捕获] 系统音频捕获错误: {e}")
                return self._get_test_tone()
        
        if self._sd is not None:
            try:
                audio_data = self._sd.rec(self.chunk_size, samplerate=self.sample_rate, 
                                        channels=self.channels, dtype='float32')
                self._sd.wait()
                volume = np.abs(audio_data).mean()
                if volume > 0.001:
                    print(f"[音频捕获] 麦克风输入，音量: {volume:.6f}")
                elif volume < 0.00001:
                    print(f"[音频捕获] 麦克风静音，使用测试音")
                    return self._get_test_tone()
                return audio_data.tobytes()
            except Exception as e:
                print(f"[音频捕获] 麦克风捕获错误: {e}")
                return self._get_test_tone()
        
        return self._get_test_tone()
    
    def _get_test_tone(self):
        """生成测试音（当没有音频输入时）"""
        t = np.linspace(0, self.chunk_size/self.sample_rate, self.chunk_size, endpoint=False)
        test_tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        if self.channels == 2:
            test_tone = np.column_stack((test_tone, test_tone))
        print("[音频捕获] 输出测试音 (440Hz)")
        return test_tone.tobytes()

    def start_streaming(self, callback, target_fps=30):
        """
        启动持续音频采集线程
        
        Args:
            callback: 回调函数 callback(audio_bytes, timestamp)
            target_fps: 目标帧率（每秒多少次回调）
        """
        self._running = True
        self._callback = callback
        self._capture_thread = threading.Thread(target=self._stream_loop,
                                               args=(target_fps,),
                                               daemon=True)
        self._capture_thread.start()
        print(f"[音频捕获] 音频流已启动，采样率: {self.sample_rate}Hz, 声道: {self.channels}")

    def _stream_loop(self, target_fps):
        """采集循环"""
        interval = 1.0 / target_fps
        timestamp = 0
        
        while self._running:
            start = time.time()
            try:
                audio_data = self.capture_frame()
                if audio_data and self._callback:
                    timestamp += len(audio_data) / (self.sample_rate * self.channels * 4)
                    self._callback(audio_data, timestamp)
                    
            except Exception as e:
                print(f"[音频捕获错误] {e}")
                time.sleep(0.1)
            
            elapsed = time.time() - start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def stop_streaming(self):
        """停止采集"""
        self._running = False
        if self._capture_thread:
            self._capture_thread.join(timeout=2)
        print("[音频捕获] 音频流已停止")

    @property
    def is_running(self):
        return self._running


class AudioPlayer:
    """音频播放器 - 播放接收到的音频"""

    def __init__(self, sample_rate=44100, channels=2, buffer_size=4096):
        """
        Args:
            sample_rate: 采样率 (Hz)
            channels: 声道数
            buffer_size: 缓冲区大小
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self.buffer_size = buffer_size
        self._running = False
        self._play_thread = None
        self._audio_buffer = []
        self._buffer_lock = threading.Lock()
        self._sd = None
        self._stream = None
        self._volume = 1.0
        self._initialize_player()

    def _initialize_player(self):
        """初始化音频播放器"""
        try:
            import sounddevice as sd
            self._sd = sd
            
            print(f"[音频播放] 已加载 sounddevice 库")
            
            devices = sd.query_devices()
            print(f"[音频播放] 可用的输出设备:")
            if isinstance(devices, dict):
                devices = [devices]
            for i, dev in enumerate(devices):
                if dev.get('max_output_channels', 0) > 0:
                    print(f"  [{i}] {dev.get('name', 'Unknown')} - 输出通道: {dev.get('max_output_channels', 0)}")
            
            self._running = True
            self._play_thread = threading.Thread(target=self._play_loop, daemon=True)
            self._play_thread.start()
            
        except ImportError:
            print("[音频播放] sounddevice 未安装，音频播放不可用")
            self._sd = None
        except Exception as e:
            print(f"[音频播放] 播放器初始化错误: {e}")
            self._sd = None

    def _play_loop(self):
        """持续播放循环"""
        while self._running:
            if self._sd is None:
                time.sleep(0.1)
                continue
                
            with self._buffer_lock:
                if len(self._audio_buffer) == 0:
                    time.sleep(0.01)
                    continue
                
                audio_array = np.concatenate(self._audio_buffer)
                self._audio_buffer = []
                
            try:
                print(f"[音频播放] 播放 {len(audio_array)} 个样本")
                if self.channels == 2:
                    audio_array = audio_array.reshape(-1, 2)
                else:
                    audio_array = audio_array.reshape(-1, 1)
                    
                self._sd.play(audio_array, samplerate=self.sample_rate)
                self._sd.wait()
                
            except Exception as e:
                print(f"[音频播放] 播放错误: {e}")

    def start(self):
        """启动音频播放"""
        self._running = True
        print("[音频播放] 音频播放已启动")

    def stop(self):
        """停止音频播放"""
        self._running = False
        print("[音频播放] 音频播放已停止")

    def play_audio(self, audio_data, sample_rate=None, channels=None):
        """播放一段音频数据"""
        if self._sd is None:
            return
            
        try:
            audio_array = np.frombuffer(audio_data, dtype=np.float32)
            
            if self._volume != 1.0:
                audio_array = audio_array * self._volume
            
            volume = np.abs(audio_array).mean()
            if volume > 0.001:
                print(f"[音频播放] 收到音频，音量: {volume:.6f}")
            
            with self._buffer_lock:
                self._audio_buffer.append(audio_array)
                
        except Exception as e:
            print(f"[音频播放] 处理音频错误: {e}")

    def set_volume(self, volume):
        """设置音量 (0.0 - 1.0)"""
        self._volume = max(0.0, min(1.0, volume))
        print(f"[音频播放] 音量设置为: {self._volume * 100}%")

    @property
    def volume(self):
        return self._volume