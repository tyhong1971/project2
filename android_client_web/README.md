# 安卓投屏客户端 - Web UI 版本

轻量级安卓客户端，使用 Python + Web UI 实现。

## 特点

- ✅ **轻量级** - 不依赖 Kivy，打包更简单
- ✅ **Web UI** - 使用 HTML/CSS/JS 界面，易于定制
- ✅ **复用协议** - 与现有系统完全兼容
- ✅ **跨平台** - 可在 PC 测试，也可打包为 APK

## 文件结构

```
android_client_web/
├── index.html        # Web UI 界面
├── server.py         # Python 后端服务器
├── requirements.txt  # Python 依赖
├── buildozer.spec    # APK 打包配置
└── README.md         # 本说明文档
```

## PC 测试运行

### 1. 安装依赖

```bash
pip install websockets
```

### 2. 启动服务器

```bash
cd f:\trae\投屏\android_client_web
python server.py
```

### 3. 打开浏览器

在浏览器中打开显示的地址，例如：`http://192.168.1.100:8080`

## 安卓设备使用

### 方式一：直接访问（推荐）

1. 在 PC 上启动服务器
2. 在安卓设备浏览器中打开 `http://<PC的IP>:8080`
3. 输入教师 IP 和学生姓名，点击连接

### 方式二：打包为 APK

使用 buildozer 打包（需要 Linux 环境）：

```bash
# 在 WSL 中
cd /mnt/f/trae/投屏/android_client_web
pip install buildozer cython
buildozer android debug
```

## 功能支持

| 功能 | 状态 |
|------|------|
| 屏幕接收 | ✅ |
| 音频接收 | ✅ |
| 消息通知 | ✅ |
| 屏幕锁定 | ✅ |

## 端口说明

| 端口 | 用途 |
|------|------|
| 8080 | HTTP 服务器 |
| 8765 | WebSocket 通信 |
| 9901 | TCP 控制通道（连接服务器） |
| 9902 | UDP 数据通道（接收投屏） |

## 与原客户端对比

| 特性 | Kivy 版本 | Web UI 版本 |
|------|-----------|-------------|
| 打包难度 | 高（依赖 SDL2） | 低（无图形依赖） |
| APK 大小 | ~50MB | ~20MB |
| 性能 | 原生渲染 | 浏览器渲染 |
| 定制性 | 需修改 Python | 修改 HTML/CSS |

## 自定义界面

修改 `index.html` 即可自定义界面样式，无需重新打包 Python 代码。
