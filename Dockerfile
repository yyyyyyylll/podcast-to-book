# Stage 1: Build frontend
FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ .
RUN npm run build

# Stage 2: Production
FROM python:3.12-slim
WORKDIR /app

# 换成腾讯云公网 Debian 镜像源，避免在腾讯云 CVM 上访问 deb.debian.org 超时
# 同时兼容 Debian 12 (bookworm, /etc/apt/sources.list) 和 Debian 13 (trixie, /etc/apt/sources.list.d/debian.sources, DEB822 格式)
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|http://deb.debian.org|http://mirrors.cloud.tencent.com|g; s|http://security.debian.org|http://mirrors.cloud.tencent.com/debian-security|g' /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|http://deb.debian.org|http://mirrors.cloud.tencent.com|g; s|http://security.debian.org|http://mirrors.cloud.tencent.com/debian-security|g' /etc/apt/sources.list; \
    fi

# Install system dependencies (Typst for PDF generation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates xz-utils \
    && (curl -fsSL -o /tmp/typst.tar.xz https://ghfast.top/https://github.com/typst/typst/releases/download/v0.13.1/typst-x86_64-unknown-linux-musl.tar.xz \
        || curl -fsSL -o /tmp/typst.tar.xz https://github.com/typst/typst/releases/download/v0.13.1/typst-x86_64-unknown-linux-musl.tar.xz) \
    && tar -xf /tmp/typst.tar.xz -C /tmp \
    && mv /tmp/typst-x86_64-unknown-linux-musl/typst /usr/local/bin/typst \
    && chmod +x /usr/local/bin/typst \
    && rm -rf /tmp/typst* \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple/ --trusted-host pypi.tuna.tsinghua.edu.cn

# Install Chromium for cover rendering (use system package to avoid CDN issues in China)
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium fonts-noto-cjk fonts-noto-color-emoji \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

# Copy backend code
COPY backend/ .

# Copy built frontend
COPY --from=frontend-builder /app/frontend/dist /app/frontend_dist

# Create storage directory
RUN mkdir -p /app/storage

ENV FRONTEND_DIR=/app/frontend_dist

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
