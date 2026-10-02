GL loader stubs for MediaPipe on Render
=======================================

MediaPipe's Linux library links against `libGLESv2.so.2` and `libEGL.so.1`
even when it runs on the CPU, and Render's native Python runtime doesn't
have them (nor allows installing system packages). These are the
vendor-neutral GL dispatch libraries from Ubuntu 20.04's `libglvnd`
packages (1.3.2-1~ubuntu0.20.04.2: libglvnd0, libegl1, libgles2), copied
unmodified. `analysis/swing_analysis.py` preloads them before MediaPipe.

They contain no GPU driver: GL/EGL calls simply fail, so MediaPipe stays
on the CPU. License: MIT-style (libglvnd).
