from __future__ import absolute_import, unicode_literals
import os
from celery import Celery
from django.conf import settings

# Django 설정 모듈을 Celery의 기본값으로 설정
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'photoshare.settings')

# Celery 앱 생성
app = Celery('photos')

# namespace='CELERY'는 모든 celery 관련 설정 키가 'CELERY_' 접두어를 가져야 한다고 알려줍니다.
app.config_from_object('django.conf:settings', namespace='CELERY')

# 등록된 Django 앱 설정에서 task를 자동으로 불러옵니다.
app.autodiscover_tasks()