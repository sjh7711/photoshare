# photos/models.py

from django.db import models
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.utils import timezone


class CustomUser(AbstractUser):
    first_name = None
    last_name = None
    email = None
    username = models.CharField(max_length=6, unique=True)
    phone = models.CharField(max_length=11, unique=True)
    lastip = models.GenericIPAddressField(blank=True, null=True)
    is_approved = models.BooleanField(default=False)
    terms_accepted = models.BooleanField(default=False)
    last_activity = models.DateTimeField(default=timezone.now)
    security_question = models.CharField(max_length=255, blank=True, null=True)
    security_answer = models.CharField(max_length=255, blank=True, null=True)

class Photo(models.Model):
    user = get_user_model()
    image = models.ImageField(upload_to='photos/uploads/')
    description = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(user, on_delete=models.CASCADE)
    uploaded_at = models.DateTimeField(default=timezone.now)
    likes = models.PositiveIntegerField(default=0)
    liked_by = models.ManyToManyField(user, related_name='liked_photos', blank=True)
    dislikes = models.PositiveIntegerField(default=0)
    disliked_by = models.ManyToManyField(user, related_name='disliked_photos', blank=True) 
        
    def save(self, *args, **kwargs):
        self.image.upload_to = 'photos/uploads/'
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.description[:20]} - {self.uploaded_by.username}"

class Comment(models.Model):
    user = get_user_model()
    photo = models.ForeignKey(Photo, related_name='comments', on_delete=models.CASCADE)
    user = models.ForeignKey(user, on_delete=models.CASCADE)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    parent = models.ForeignKey('self', null=True, blank=True, related_name='replies', on_delete=models.CASCADE)
    is_deleted = models.BooleanField(default=False)  # is_deleted 필드 추가

    def __str__(self):
        return f"Comment by {self.user.username} on {self.photo.description[:20]}"

class Notice(models.Model):
    user = get_user_model()
    title = models.CharField(max_length=200)
    content = models.TextField()
    author = models.ForeignKey(user, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    is_important = models.BooleanField(default=False)

    def __str__(self):
        return self.title
    
class Notification(models.Model):
    user = get_user_model()
    recipient = models.ForeignKey(user, on_delete=models.CASCADE)
    message = models.CharField(max_length=255)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    photo = models.ForeignKey(Photo, on_delete=models.CASCADE, null=True, blank=True)
    notice = models.ForeignKey(Notice, on_delete=models.CASCADE, null=True, blank=True)
    count = models.IntegerField(default=1)
    is_notice = models.BooleanField(default=False)
    is_pending = models.BooleanField(default=False)
    
class DeletedPhoto(models.Model):
    user = get_user_model()
    deleted_at = models.DateTimeField(auto_now_add=True)
    filename = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(user, on_delete=models.CASCADE)
    img_info = models.TextField(blank=True)
    img_info2 = models.TextField(blank=True)

    def __str__(self):
        return self.filename
    
class PendingApprovalPhoto(models.Model):
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE)
    similar_photo_path = models.CharField(max_length=255, blank=True)  # 유사한 사진 경로
    pending_photo_path = models.CharField(max_length=255, blank=True)  # 업로드 보류된 사진 경로
    description = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_rejected = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.description[:20]} - {self.user.username}"
    
class Block(models.Model):
    blocker = models.ForeignKey(get_user_model(), related_name='blocking', on_delete=models.CASCADE)
    blocked = models.ForeignKey(get_user_model(), related_name='blocked_by', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.blocker.username} blocked {self.blocked.username}"