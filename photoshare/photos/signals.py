from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import Comment, Notification, Notice
from webpush import send_user_notification

User = get_user_model()

@receiver(post_save, sender=Comment)
def create_notification(sender, instance, created, **kwargs):
    if created:
        if instance.parent:
            recipient = instance.parent.user
            message = f"{instance.user.username}님의 대댓"
        else:
            recipient = instance.photo.uploaded_by
            message = f"{instance.user.username}님의 댓글"

        # 기존의 읽지 않은 알림이 있는지 확인
        existing_notification = Notification.objects.filter(
            recipient=recipient,
            photo=instance.photo,
            is_read=False
        ).first()

        if existing_notification:
            # 기존 알림이 있으면 메시지를 업데이트
            existing_notification.message = f"{message} 외 {existing_notification.count}건"
            existing_notification.count += 1
            existing_notification.save()
        else:
            # 기존 알림이 없으면 새로운 알림 생성
            Notification.objects.create(
                recipient=recipient,
                message=message,
                photo=instance.photo,
                count=1,
                is_notice=False
            )
        
        # push notification
        payload = {
            "head": f"{instance.user.username}님의 댓글",
            "body": instance.text,
            "badge": "https://192.168.0.203/static/favicon/badge.png",
            "icon": "https://192.168.0.203/static/favicon/android-icon-96x96.png",
            "url": f"https://192.168.0.203/photo/{instance.photo.id}",
            "tag": "comment"
        }
    
        # 모든 PushInformation 객체를 가져옵니다.
        send_user_notification(
            user=recipient,
            payload=payload, 
            ttl=1000,
        )
            
@receiver(post_save, sender=Notice)
def create_notice_notification(sender, instance, created, **kwargs):
    if created:
        users = User.objects.all()
        for user in users:
            Notification.objects.create(
                recipient=user,
                message=f"새 공지: {instance.title}",
                count=1,
                is_notice=True,
                notice=instance  # 공지 정보를 포함
            )