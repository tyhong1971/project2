# 安卓投屏客户端

局域网投屏系统的安卓客户端，用于接收服务器的投屏。

## 功能特性

- ✅ 接收教师屏幕广播
- ✅ 接收教师音频广播
- ✅ 接收教师消息通知
- ✅ 屏幕锁定功能

## 文件结构

```
android_client/
├── main.py              # Kivy UI 主程序
├── android_client.py    # 客户端核心逻辑
├── buildozer.spec       # 打包配置文件
└── README.md            # 本说明文档
```

## 开发环境

### 1. 安装依赖（PC 测试用）

```bash
pip install kivy
```

### 2. PC 上测试运行

```bash
cd android_client
python main.py
```

## 打包 APK

### 方法一：使用 Linux + Buildozer（推荐）

Buildozer 只能在 Linux 上运行，推荐使用 WSL2 或 Linux 虚拟机。

#### 1. 安装 Buildozer

```bash
pip install buildozer
pip install cython
```

#### 2. 初始化项目（首次）

```bash
cd android_client
buildozer init  # 如果 buildozer.spec 已存在可跳过
```

#### 3. 打包 APK

```bash
# Debug 版本（快速打包，用于测试）
buildozer android debug

# Release 版本（用于发布）
buildozer android release
```

打包完成后，APK 文件位于 `bin/` 目录下。

### 方法二：使用 GitHub Actions 自动打包

创建 `.github/workflows/build.yml`：

```yaml
name: Build Android APK

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      
      - name: Install dependencies
        run: |
          pip install buildozer
          pip install cython
      
      - name: Build APK
        run: |
          cd android_client
          buildozer android debug
      
      - name: Upload APK
        uses: actions/upload-artifact@v3
        with:
          name: screencast-android
          path: android_client/bin/*.apk
```

### 方法三：使用 Docker

```bash
docker pull kivy/buildozer

docker run --rm -v "$PWD":/home/user/hostcwd kivy/buildozer \
    --workdir /home/user/hostcwd/android_client \
    android debug
```

## 安装和使用

### 1. 安装 APK

将打包好的 APK 传输到安卓设备并安装：
- 通过 USB 数据线复制
- 通过微信/QQ 文件传输
- 通过网络共享

### 2. 使用步骤

1. 打开应用
2. 输入教师端（服务器）的 IP 地址
3. 输入学生姓名
4. 点击"连接"
5. 等待教师开始广播

### 3. 界面说明

| 按钮 | 功能 |
|------|------|
| 🔊 音频 | 切换音频接收开关 |
| 断开连接 | 断开与服务器的连接 |
| 退出 | 退出应用 |

## 网络要求

- 安卓设备与教师端必须在同一局域网
- 需要以下网络权限：
  - `INTERNET` - 网络访问
  - `ACCESS_NETWORK_STATE` - 网络状态
  - `ACCESS_WIFI_STATE` - WiFi 状态
  - `CHANGE_WIFI_STATE` - WiFi 控制

## 端口说明

| 端口 | 协议 | 用途 |
|------|------|------|
| 9901 | TCP | 控制信令（注册、心跳等） |
| 9902 | UDP | 服务器数据端口 |
| 9910+ | UDP | 客户端数据端口（动态分配） |

## 常见问题

### Q: 无法连接到服务器？

1. 确认 IP 地址输入正确
2. 确认安卓设备和服务器在同一局域网
3. 检查服务器防火墙是否开放 9901 和 9902 端口
4. 尝试 ping 服务器 IP 测试网络连通性

### Q: 连接成功但看不到画面？

1. 确认教师端已开始广播
2. 检查 UDP 端口是否被防火墙阻止
3. 查看应用日志（logcat）排查问题

### Q: 音频没有声音？

1. 点击"🔊 音频"按钮确认音频已开启
2. 检查安卓设备音量设置
3. 确认教师端已开启音频广播

### Q: APK 打包失败？

1. 确保在 Linux 环境下运行 Buildozer
2. 检查 `buildozer.spec` 配置是否正确
3. 尝试删除 `.buildozer` 目录重新打包

## 技术架构

```
┌─────────────────────────────────────┐
│         安卓客户端 (Kivy)            │
├─────────────────────────────────────┤
│  main.py (UI)                       │
│  ├── 连接界面                        │
│  └── 主界面 (屏幕显示、消息、控制)     │
├─────────────────────────────────────┤
│  android_client.py (核心)           │
│  ├── TCP 连接 (控制信令)             │
│  ├── UDP 接收 (屏幕/音频数据)         │
│  └── 协议处理 (复用现有协议)          │
└─────────────────────────────────────┘
              ↕ TCP/UDP
┌─────────────────────────────────────┐
│         服务器 (Python)              │
└─────────────────────────────────────┘
```

## 版本历史

- v1.0.0 - 初始版本
  - 支持屏幕接收
  - 支持音频接收
  - 支持消息通知
  - 支持屏幕锁定
