FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 필요한 시스템 패키지 설치
RUN apt-get update && \
    apt-get install -y python3-dev default-libmysqlclient-dev build-essential pkg-config ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# 필요한 파이썬 패키지 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# /usr/local/lib/python3.11/site-packages/webpush/models.py 경로의 PushInformation 함수 수정 
RUN sed -i "24i \ \ \ \ device_id = models.CharField(max_length=255, default='default_device_id')" /usr/local/lib/python3.11/site-packages/webpush/models.py


# 애플리케이션 코드 복사
COPY . .

# temp 및 uploads 디렉토리 생성
RUN mkdir -p /app/photoshare/photos/temp /app/photoshare/photos/uploads /app/photoshare/photos/trashcan /app/photoshare/photos/converts /app/photoshare/static /app/photoshare/log 

# Gunicorn 실행
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--chdir", "/app/photoshare", "config.wsgi:application"]
