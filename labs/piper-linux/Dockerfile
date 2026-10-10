# labs/piper-linux — Linux 容器里的本地模型 TTS 全链路验证镜像
# 用途与复现步骤见同目录 README.md；定位：labs 一次性实验区，不进任何层契约。
FROM python:3.12-slim

# 国内加速：Debian 源换清华（实验环境默认国内网络）
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
 && apt-get update && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# piper CLI（rhasspy/piper 的 PyPI 包）
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple piper-tts
