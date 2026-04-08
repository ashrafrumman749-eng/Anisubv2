FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    fontconfig \
    wget \
    gcc \
    fonts-noto-core \
    fonts-beng \
    fonts-beng-extra \
    fonts-lohit-beng-bengali \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# yt-dlp (ব্যাকআপ)
RUN wget -q https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -O /usr/local/bin/yt-dlp && chmod +x /usr/local/bin/yt-dlp

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN fc-cache -fv

CMD gunicorn --timeout 3600 --workers 1 --bind 0.0.0.0:$PORT app:app
