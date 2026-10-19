# 项目内 HackRF Windows x64 依赖

以下 DLL 复制自本机 `C:\Software\hackrf\windows\bin`，无需运行时访问原目录：

- `libhackrf.dll`：运行时报告版本 0.9.2、发布号 2026.01.3。
- `libusb-1.0.dll`：与本机 MSYS2 `mingw-w64-ucrt-x86_64-libusb 1.0.28-1` 文件哈希一致。
- `libwinpthread-1.dll`：与本机 MSYS2 `mingw-w64-ucrt-x86_64-winpthreads 14.0.0.r92.g818fa6510-1` 文件哈希一致。

三者均为 PE x86-64，需要 64 位 Python。系统 UCRT / Windows DLL 使用操作系统提供的版本。`manifest.json` 保存来源、大小和 SHA-256；`hackrf.h` 为随本地库安装的 ABI 头文件。

后端通过 `os.add_dll_directory` 保持此目录的依赖搜索句柄有效，再使用 cdecl `ctypes.CDLL` 加载绝对路径。`--dll` 或 `HackRFLibrary(path)` 可以替换为自定义动态库，并从其所在目录查找依赖。包构建会将当前目录一并包含在 wheel 中。

许可证分别见 `LICENSE-libhackrf.txt`（BSD）、`LICENSE-libusb.txt`（LGPL-2.1-or-later）和 `LICENSE-winpthread.txt`。对应项目及源码：

- [HackRF](https://github.com/greatscottgadgets/hackrf)
- [libusb 1.0.28](https://github.com/libusb/libusb/tree/v1.0.28)，[MSYS2 libusb 构建脚本](https://github.com/msys2/MINGW-packages/tree/master/mingw-w64-libusb)
- [mingw-w64 winpthreads](https://github.com/mingw-w64/mingw-w64/tree/master/mingw-w64-libraries/winpthreads)

DLL 为独立动态库，允许通过自定义路径替换和重新链接；Python 后端不内嵌或修改这些库的二进制代码。
