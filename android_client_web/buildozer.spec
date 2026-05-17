[app]

# (str) Title of your application
title = 局域网投屏

# (str) Package name
package.name = screencast

# (str) Package domain
package.domain = org.screencast

# (str) Source code where the main.py live
source.dir = .

# (list) Source files to include
source.include_exts = py,png,jpg,html,css,js,json

# (str) Application versioning
version = 1.0.0

# (list) Application requirements
# 使用 webview bootstrap 而不是 sdl2，这样不需要编译 SDL2
requirements = python3,websockets,android

# (str) Bootstrap type - 使用 webview 而不是 sdl2
android.bootstrap = webview

# (str) Supported orientation
orientation = all

# (bool) Fullscreen
fullscreen = 0

# (list) Permissions
android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE

# (int) Target Android API
android.api = 33

# (int) Minimum API
android.minapi = 21

# (str) Android NDK version
android.ndk = 25b

# (bool) Skip update
android.skip_update = False

# (bool) Accept SDK license
android.accept_sdk_license = True

# (str) Android archs
android.archs = arm64-v8a, armeabi-v7a

# (bool) Allow backup
android.allow_backup = True

# (bool) Debug sign
android.debug_sign = True

# (bool) Release
android.release = False

# [buildozer]

# (int) Log level
log_level = 2

# (int) Warn on root
warn_on_root = 0
