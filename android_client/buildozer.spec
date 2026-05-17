[app]

# (str) Title of your application
title = 局域网投屏

# (str) Package name
package.name = screencast

# (str) Package domain (needed for android/ios packaging)
package.domain = org.screencast

# (str) Source code where the main.py live
source.dir = .

# (list) Source files to include (let empty to include all the files)
source.include_exts = py,png,jpg,kv,atlas,json,ttf,otf

# (str) Application versioning
version = 1.0.0

# (list) Application requirements
requirements = python3,kivy,hostpython3

# (str) Presplash of the application
#presplash.filename = %(source.dir)s/presplash.png

# (str) Icon of the application
#icon.filename = %(source.dir)s/icon.png

# (str) Supported orientation (landscape, portrait or all)
orientation = all

# (bool) Indicate if the application should be fullscreen or not
fullscreen = 0

# (list) Permissions
android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,CHANGE_WIFI_STATE

# (int) Target Android API, should be as high as possible.
android.api = 33

# (int) Minimum API your APK will support.
android.minapi = 21

# (str) Android NDK version to use
android.ndk = 25b

# (bool) If True, automatically skip files that don't exist (instead of raising an error)
android.skip_update = False

# (str) Use a local copy of python-for-android instead of cloning
p4a.branch = master
p4a.source_dir = /opt/p4a

# (bool) If True, then automatically accept SDK license
android.accept_sdk_license = True

# (str) The Android arch to build for, choices: armeabi-v7a, arm64-v8a, x86, x86_64
android.archs = arm64-v8a, armeabi-v7a

# (bool) enables Android auto backup feature (Android API >=23)
android.allow_backup = True

# (str) The output directory for the APK
android.entrypoint = org.kivy.android.PythonActivity

# (str) The Android logcat filters to use
android.logcat_filters = *:S python:D

# (bool) enables the use of the wakelock to prevent sleep
android.wakelock = False

# (list) Android additionnal jars to add
#android.add_jars = foo.jar,bar.jar

# (list) Android gradle dependencies to add
#android.gradle_dependencies = com.android.support:appcompat-v7:28.0.0

# (bool) If True, the service will be started as a foreground service
#android.foreground_service = False

# (str) The path to the keystore file for signing the APK
#android.release_keystore = /path/to/keystore

# (str) The alias for the keystore
#android.release_keyalias = mykey

# (str) The password for the keystore
#android.release_keyalias_password = password

# (str) The password for the keystore alias
#android.release_keystore_password = password

# (bool) If True, then the APK will be signed with the debug key
android.debug_sign = True

# (str) The path to the ant directory
#android.ant_path = /path/to/ant

# (str) The path to the gradle directory
#android.gradle_path = /path/to/gradle

# (str) The path to the maven directory
#android.maven_path = /path/to/maven

# (bool) If True, then the APK will be built with the release configuration
android.release = False

# [buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 0
