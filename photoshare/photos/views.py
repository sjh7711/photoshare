
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse, HttpResponseRedirect, HttpResponseForbidden, FileResponse, Http404
from django.contrib.auth import login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.views.decorators.http import require_POST, require_http_methods
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.contrib.auth.views import LoginView
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.files.storage import FileSystemStorage
from django.core.cache import caches
from django.conf import settings
from django.db.models import Count, Q, Sum, Case, When, IntegerField, Subquery, OuterRef, Value, Prefetch, F
from django.db.models.functions import Coalesce, TruncDate
from django.contrib.sessions.models import Session
from django.utils import timezone
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.cache import cache_control
from PIL import Image
import imagehash

from .models import Photo, Comment, CustomUser, Notification, DeletedPhoto, Notice, PendingApprovalPhoto, Block
from .forms import MultiPhotoForm, PhotoSearchForm, SignUpForm, EditProfileForm, CommentForm, NoticeForm
from photoprocess.tasks import process_and_save_photos
from photoprocess.tasks import convert_file_to_animation

from django_redis import get_redis_connection

from webpush.models import PushInformation, SubscriptionInfo, Group

import datetime
import os
import shutil
import zipfile
import logging
import json
import hashlib
from datetime import timedelta
import time

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)
User = get_user_model()

##############################################################################################################################
##############################################   메인   페이지   ##############################################################
##############################################################################################################################

redis_conn3 = get_redis_connection("resource_monitor")
redis_conn4 = get_redis_connection("uploaded_photo")

@csrf_exempt
def check_device_id(request):
    if request.method == 'POST':
        data = json.loads(request.body.decode('utf-8'))
        device_id = data.get('device_id')

        if device_id:
            # device_id가 이미 등록되었는지 확인
            exists = PushInformation.objects.filter(device_id=device_id, user_id=request.user.id).exists()
            return JsonResponse({'registered': exists})
        else:
            return JsonResponse({'error': 'device_id not provided'}, status=400)
    return JsonResponse({'error': 'Invalid request method'}, status=405)

@csrf_exempt
@login_required
def save_subscription(request):
    if request.method == 'POST':
        subscription_data = json.loads(request.body.decode('utf-8'))
        device_id = subscription_data.get('device_id')
        subscription = subscription_data.get('subscription')

        if not subscription or not device_id:
            return JsonResponse({'error': 'Invalid data'}, status=400)

        # SubscriptionInfo 객체 생성
        subscription_info = SubscriptionInfo.objects.create(
            endpoint=subscription['endpoint'],
            p256dh=subscription['keys']['p256dh'],
            auth=subscription['keys']['auth']
        )
        
        # 기본 그룹 가져오기 또는 생성
        group, created = Group.objects.get_or_create(name='all_users')
        
        # PushInformation 객체 생성 또는 업데이트
        push_info, created = PushInformation.objects.get_or_create(
            user=request.user,
            device_id=device_id,
            defaults={'subscription': subscription_info, 'group': group}
        )
        
        if not created:
            # 이미 존재하는 경우 업데이트
            push_info.subscription = subscription_info
            push_info.group = group
            push_info.save()

        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error'}, status=405)

@login_required
@require_http_methods("POST")
def toggle_push_notification(request):
    user = request.user
    data = json.loads(request.body.decode('utf-8'))
    device_id = data.get('device_id')
    requestType = data.get('type')
    push_info = PushInformation.objects.filter(device_id=device_id, user=user).first()

    if requestType == "get":
        if push_info:
            return JsonResponse({"subscribed": True})
        else:
            return JsonResponse({"subscribed": False})
    
    elif requestType == "post":
        if push_info:
            push_info.delete()
            return JsonResponse({"blockSubscribed": True, "message": "알림 구독이 해제되었습니다."})
        else:
            return JsonResponse({"blockSubscribed": False, "message": "알림 구독되었습니다."})



@login_required
@require_POST
def block_user(request):
    data = json.loads(request.body)
    blocked_user_id = data.get('blocked_user_id')
    blocked_user = get_object_or_404(User, id=blocked_user_id)
    Block.objects.create(blocker=request.user, blocked=blocked_user)
    blocked_user_data = {
        'id': blocked_user.id,
        'username': blocked_user.username,
        'created_at': datetime.datetime.now().strftime('%Y.%m.%d')
    }
    return JsonResponse({'status': 'success', 'message': f'{blocked_user.username} - 차단 목록에 추가되었습니다.', 'blocked_user': blocked_user_data})

@login_required
@require_POST
def unblock_user(request):
    data = json.loads(request.body)
    blocked_user_id = data.get('blocked_user_id')
    blocked_user = get_object_or_404(User, id=blocked_user_id)
    block = Block.objects.get(blocker=request.user, blocked=blocked_user)
    block.delete()
    return JsonResponse({'status': 'success', 'message': f'{blocked_user.username} - 차단 목록에서 제거되었습니다.'})

@login_required
def block_user_page(request):
    blockedUsers = Block.objects.filter(blocker=request.user)
    users = User.objects.exclude(id=request.user.id).exclude(id__in=blockedUsers.values_list('blocked', flat=True))
    return render(request, 'photos/block_user.html', {'users': users, 'blockedUsers': blockedUsers})

from django.db.models.functions import Cast
from django.db.models import DateField

def get_photo_data(request):
    query_username = request.GET.get('uploader', '')

    referrer = request.META.get('HTTP_REFERER')

    if 'my_photos' in referrer:
        photos = Photo.objects.filter(uploaded_by=request.user)
    elif 'liked_photos' in referrer:
        photos = Photo.objects.filter(liked_by=request.user)
    else:
        photos = Photo.objects.all()
    
    if query_username:
        photos = photos.filter(uploaded_by__username__icontains=query_username)

    # 일단위로 그룹화
    photo_data = photos.annotate(date=Cast(F('uploaded_at') + timedelta(hours=9), output_field=DateField())).values('date').annotate(count=Count('id')).order_by('date')
    
    data = {item['date'].strftime('%Y-%m-%d'): item['count'] for item in photo_data if item['date']}
    
    return JsonResponse(data)

def photo_list(request):
    sort = request.GET.get('sort', 'latest')
    query_description = request.GET.get('query_description', '')
    query_username = request.GET.get('query_username', '')
    date_range = request.GET.get('date_range', '')
    photos = Photo.objects.all()
    
    if request.user.is_authenticated:
        notifications = Notification.objects.filter(recipient=request.user, is_read=False)
        active_users = get_active_users(request)
        blocked_users = Block.objects.filter(blocker=request.user).values_list('blocked', flat=True)
        photos = photos.exclude(uploaded_by__in=blocked_users)
        
        # Update user's last IP
        User.objects.filter(id=request.user.id).update(lastip=request.META.get('HTTP_X_FORWARDED_FOR'))
    else:
        notifications = []
        active_users = []
    
    uploaders = User.objects.filter(photo__in=photos).annotate(photo_count=Count('photo')).order_by('-photo_count').distinct()

    # Apply search filters
    if query_description:
        photos = photos.filter(description__icontains=query_description)
    if query_username:
        photos = photos.filter(uploaded_by__username__icontains=query_username)
    if date_range:
        if 'to' in date_range:
            start_date, end_date = date_range.split(' to ')
        else:
            start_date = end_date = date_range
        start_date = datetime.datetime.strptime(start_date, '%Y-%m-%d') - datetime.timedelta(hours=9)
        end_date = datetime.datetime.strptime(end_date, '%Y-%m-%d') - datetime.timedelta(hours=9) + timedelta(days=1)
        photos = photos.filter(uploaded_at__range=(start_date, end_date))

    # Apply sorting
    if sort == 'likes':
        photos = photos.order_by('-likes')
    elif sort == 'latest':
        photos = photos.order_by('-uploaded_at')
    elif sort == 'oldest':
        photos = photos.order_by('uploaded_at')

    # Prefetch related data
    photos = photos.prefetch_related(
        Prefetch('comments', queryset=Comment.objects.order_by('created_at')[:3], to_attr='first_three_comments'),
        'liked_by',
        'disliked_by'
    )

    # Count total photos for pagination
    total_photos = photos.count()

    # Paginate
    paginator = Paginator(photos, 20)
    page_number = request.GET.get('page')
    photos_page = paginator.get_page(page_number)

    # Prepare photo data
    for photo in photos_page:
        photo.comment_count = photo.comments.count()  # This might trigger a query, consider adding it to prefetch if needed frequently
        photo.file_size_mb = photo.image.size / (1024 * 1024)
        photo.uploaded = (photo.uploaded_at + timedelta(hours=9)).strftime('%y.%m.%d')
        photo.is_liked_by_user = request.user in photo.liked_by.all() if request.user.is_authenticated else False
        photo.is_disliked_by_user = request.user in photo.disliked_by.all() if request.user.is_authenticated else False
        photo.comments_data = [{'user': comment.user.username, 'text': comment.text} for comment in photo.first_three_comments]

    # Handle AJAX requests
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' and total_photos > 20:
        photo_list_html = render_to_string('photo_cards_template.html', {'photos': photos_page, 'user': request.user, 'request': request})
        return JsonResponse({
            'photos': photo_list_html,
            'has_next': photos_page.has_next()
        })

    search_form = PhotoSearchForm(initial={
        'query_description': query_description,
        'query_username': query_username,
        'date_range': date_range,
    })

    return render(request, 'photos/photo_list.html', {
        'photos': photos_page,
        'has_next': photos_page.has_next(),
        'search_form': search_form,
        'uploaders': uploaders,
        'active_users': active_users,
        'notifications': notifications
    })

@login_required
def liked_photos(request):
    sort = request.GET.get('sort', 'latest')
    query_description = request.GET.get('query_description', '')
    query_username = request.GET.get('query_username', '')
    date_range = request.GET.get('date_range', '')
    
    notifications = Notification.objects.filter(recipient=request.user, is_read=False)
    
    # Base queryset
    photos = Photo.objects.filter(liked_by=request.user)
    
    if query_description:
        photos = photos.filter(description__icontains=query_description)
    if query_username:
        photos = photos.filter(uploaded_by__username__icontains=query_username)
    if date_range:
        if 'to' in date_range:
            start_date, end_date = date_range.split(' to ')
        else:
            start_date = end_date = date_range
        start_date = datetime.datetime.strptime(start_date, '%Y-%m-%d') - datetime.timedelta(hours=9)
        end_date = datetime.datetime.strptime(end_date, '%Y-%m-%d') - datetime.timedelta(hours=9) + timedelta(days=1)
        photos = photos.filter(uploaded_at__range=(start_date, end_date))
        
    uploaders = User.objects.filter(photo__in=photos).annotate(photo_count=Count('photo')).order_by('-photo_count').distinct()

    # Apply sorting
    if sort == 'likes':
        photos = photos.order_by('-likes')
    elif sort == 'latest':
        photos = photos.order_by('-uploaded_at')
    elif sort == 'oldest':
        photos = photos.order_by('uploaded_at')

    # Prefetch related data
    photos = photos.prefetch_related(
        Prefetch('comments', queryset=Comment.objects.order_by('created_at')[:3], to_attr='first_three_comments'),
        'liked_by'
    ).select_related('uploaded_by')

    # Count total photos for pagination
    total_photos = photos.count()

    # Paginate
    paginator = Paginator(photos, 20)
    page_number = request.GET.get('page')
    photos_page = paginator.get_page(page_number)

    # Prepare photo data
    for photo in photos_page:
        photo.comment_count = photo.comments.count()  # This might trigger a query, consider adding it to prefetch if needed frequently
        photo.file_size_mb = photo.image.size / (1024 * 1024)
        photo.uploaded = (photo.uploaded_at + timedelta(hours=9)).strftime('%y.%m.%d')
        photo.is_liked_by_user = True  # Since this is the liked_photos view, all photos are liked by the user
        photo.comments_data = [{'user': comment.user.username, 'text': comment.text} for comment in photo.first_three_comments]

    # Handle AJAX requests
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' and total_photos > 20:
        photo_list_html = render_to_string('photo_cards_template.html', {'photos': photos_page, 'user': request.user, 'request': request})
        return JsonResponse({
            'photos': photo_list_html,
            'has_next': photos_page.has_next()
        })

    search_form = PhotoSearchForm(initial={
        'query_description': query_description,
        'query_username': query_username,
        'date_range': date_range,
    })

    return render(request, 'photos/photo_list.html', {
        'lenphotos': total_photos,
        'photos': photos_page,
        'has_next': photos_page.has_next(),
        'search_form': search_form,
        'uploaders': uploaders,
        'notifications': notifications
    })

@login_required
def my_photos(request):
    sort = request.GET.get('sort', 'latest')
    query_description = request.GET.get('query_description', '')
    date_range = request.GET.get('date_range', '')
    
    # Base query
    photos = Photo.objects.filter(uploaded_by=request.user)
    
    # Apply search filter
    if query_description:
        photos = photos.filter(description__icontains=query_description)
    if date_range:
        if 'to' in date_range:
            start_date, end_date = date_range.split(' to ')
        else:
            start_date = end_date = date_range
        start_date = datetime.datetime.strptime(start_date, '%Y-%m-%d') - datetime.timedelta(hours=9) #db날짜는 UTC 기준이라 9시간 빼야함
        end_date = datetime.datetime.strptime(end_date, '%Y-%m-%d') - datetime.timedelta(hours=9) + timedelta(days=1)
        photos = photos.filter(uploaded_at__range=(start_date, end_date))
    
    # Apply sorting
    if sort == 'likes':
        photos = photos.order_by('-likes')
    elif sort == 'dislikes':
        photos = photos.order_by('-dislikes')
    elif sort == 'latest':
        photos = photos.order_by('-uploaded_at')
    elif sort == 'oldest':
        photos = photos.order_by('uploaded_at')
    
    # Count total photos
    total_photos = photos.count()
    
    # Prefetch related data
    photos = photos.prefetch_related(
        Prefetch('comments', queryset=Comment.objects.order_by('created_at')[:3], to_attr='first_three_comments'),
        'liked_by'
    )
    
    # Get pending photos in a single query
    pending_photos = dict(PendingApprovalPhoto.objects.filter(
        user_id=request.user.id, 
        is_rejected=False
    ).values_list('pending_photo_path', 'similar_photo_path'))
    
    # Get all related original photos in a single query
    original_photos = dict(Photo.objects.filter(
        image__in=pending_photos.values()
    ).values_list('image', 'id'))
    
    # Paginate
    paginator = Paginator(photos, 20)
    page_number = request.GET.get('page')
    photos_page = paginator.get_page(page_number)
    
    # Prepare photo data
    for photo in photos_page:
        if photo.image in pending_photos:
            sim_path = pending_photos[photo.image]
            photo.is_pending = f"https://192.168.0.225/photo/{original_photos.get(sim_path, '')}"
        else:
            photo.is_pending = False
        photo.comment_count = photo.comments.count()  # This might trigger a query, consider adding it to prefetch if needed frequently
        photo.file_size_mb = photo.image.size / (1024 * 1024)
        photo.uploaded = (photo.uploaded_at + timedelta(hours=9)).strftime('%y.%m.%d')
        photo.is_liked_by_user = request.user in photo.liked_by.all()
        photo.comments_data = [{'user': comment.user.username, 'text': comment.text} for comment in photo.first_three_comments]
    
    # Handle AJAX requests
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' and total_photos > 20:
        photo_list_html = render_to_string('photo_cards_template.html', {'photos': photos_page, 'user': request.user, 'request': request})
        return JsonResponse({
            'photos': photo_list_html,
            'has_next': photos_page.has_next()
        })
    
    notifications = Notification.objects.filter(recipient=request.user, is_read=False)
    search_form = PhotoSearchForm(initial={
        'query_description': query_description,
        'query_username': request.user.username,
        'date_range': date_range,
    })
    
    return render(request, 'photos/photo_list.html', {
        'lenphotos': total_photos,
        'photos': photos_page,
        'has_next': photos_page.has_next(),
        'search_form': search_form,
        'notifications': notifications
    })
    
@login_required
def my_statistics(request):
    user = request.user

    # Subqueries
    receive_like_subquery = Photo.objects.filter(
        liked_by=OuterRef('pk'),
        uploaded_by=user
    ).values('liked_by').annotate(count=Count('id')).values('count')

    send_like_subquery = Photo.objects.filter(
        uploaded_by=OuterRef('pk'),
        liked_by=user
    ).values('uploaded_by').annotate(count=Count('id')).values('count')

    receive_comment_subquery = Comment.objects.filter(
        user=OuterRef('pk'),
        photo__uploaded_by=user
    ).values('user').annotate(count=Count('id')).values('count')
    
    send_comment_subquery = Comment.objects.filter(
        photo__uploaded_by=OuterRef('pk'),
        user=user
    ).values('photo__uploaded_by').annotate(count=Count('id')).values('count')

    # User statistics calculation
    relation_users = User.objects.annotate(
        receive_like_count=Coalesce(Subquery(receive_like_subquery), 0, output_field=IntegerField()),
        send_like_count=Coalesce(Subquery(send_like_subquery), 0, output_field=IntegerField()),
        receive_comment_count=Coalesce(Subquery(receive_comment_subquery), 0, output_field=IntegerField()),
        send_comment_count=Coalesce(Subquery(send_comment_subquery), 0, output_field=IntegerField()),
        total_interaction=F('receive_like_count') + F('send_like_count') + F('receive_comment_count') + F('send_comment_count')
    ).filter(total_interaction__gt=0).order_by('-total_interaction')[:50]  # Limit to top 50 users

    # Calculate totals
    totals = relation_users.aggregate(
        total_receive_like_count=Sum('receive_like_count'),
        total_send_like_count=Sum('send_like_count'),
        total_receive_comment_count=Sum('receive_comment_count'),
        total_send_comment_count=Sum('send_comment_count')
    )

    # Fetch liked photos and photos that received likes
    like_photos = Photo.objects.filter(liked_by=user).annotate(
        like_count=Count('liked_by')
    ).order_by('-uploaded_at')[:40]

    liked_photos = Photo.objects.filter(uploaded_by=user).annotate(
        like_count=Count('liked_by')
    ).filter(like_count__gt=0).order_by('-uploaded_at')[:40]

    # Prefetch liked_by users for both querysets
    like_photos = like_photos.prefetch_related('liked_by')
    liked_photos = liked_photos.prefetch_related('liked_by')

    # Prepare photo data
    like_photos_data = [
        {
            'photo_id': photo.id,
            'photo_url': photo.image.url,
            'liked_by': [user.username for user in photo.liked_by.all()]
        }
        for photo in like_photos
    ]
    
    liked_photos_data = [
        {
            'photo_id': photo.id,
            'photo_url': photo.image.url,
            'liked_by': [user.username for user in photo.liked_by.all()]
        }
        for photo in liked_photos
    ]

    context = {
        'relation_users': relation_users,
        'totals': totals,
        'like_photos': like_photos_data,
        'liked_photos': liked_photos_data,
    }

    return render(request, 'photos/my_statistics.html', context)

def common_user_activity(request, user_id):
    user = get_object_or_404(User, id=user_id)
    like_photos = Photo.objects.annotate(like_count=Count('liked_by')).filter(uploaded_by=user).filter(liked_by=request.user).order_by('-uploaded_at')[:20]
    liked_photos = Photo.objects.annotate(like_count=Count('liked_by')).filter(uploaded_by=request.user).filter(liked_by=user).order_by('-uploaded_at')[:20]

    like_photos_data = [
        {
            'photo_id': photo.id,
            'photo_url': photo.image.url,
            'liked_by': [user.username for user in photo.liked_by.all()]
        }
        for photo in like_photos
    ]
    
    liked_photos_data = [
        {
            'photo_id': photo.id,
            'photo_url': photo.image.url,
            'liked_by': [user.username for user in photo.liked_by.all()]
        }
        for photo in liked_photos
    ]

    return JsonResponse({
        'like_photos': like_photos_data,
        'liked_photos': liked_photos_data,
    })


def photo_detail(request, photo_id):
    photo = get_object_or_404(Photo, id=photo_id)
    form = CommentForm()    
    comments = photo.comments.filter(parent__isnull=True).prefetch_related('replies')
    referer = request.META.get('HTTP_REFERER', '')
    
    for comment in comments:
        if comment.is_deleted:
            comment.text = "<삭제된 메세지입니다>"

    return render(request, 'photos/photo_detail.html', {
        'photo': photo,
        'form': form,
        'comments': comments,
        'referer': referer
    })

@login_required
def upload_photo(request):
    if request.method == 'POST':
        form = MultiPhotoForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                files = request.FILES.getlist('images')
                descriptions = request.POST.getlist('descriptions')
                preserve_order = request.POST.get('preserve_order')
                temp_dir = './photos/temp'
                #projectfoler ./~
                if not os.path.exists(temp_dir):
                    os.makedirs(temp_dir)
                
                file_paths = []
                file_descriptions = []
                logger.info(f"request: {request.user.username} is uploading {len(files)} photos")
                for i, f in enumerate(files):
                    description = descriptions[i].strip() if i < len(descriptions) else ''
                    fs = FileSystemStorage(location=temp_dir)
                    filename = fs.save(f.name, f)
                    file_path = os.path.join(temp_dir, filename)
                    file_paths.append(file_path)
                    file_descriptions.append(description)
                
                try:
                    process_and_save_photos.delay(file_paths, file_descriptions, request.user.id, preserve_order)
                except Exception as e:
                    logger.error(f"Error sending task to Celery: {e}", exc_info=True)
                    return render(request, 'photos/photo_upload.html', {'form': form, 'error': f"Could not start processing: {e}"})
                return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/'))
            except Exception as e:
                logger.error(f"Error during file upload: {e}")
                return render(request, 'photos/photo_upload.html', {'form': form, 'error': str(e)})
        else:
            logger.warning("Form is not valid")
    else:
        form = MultiPhotoForm()
    return render(request, 'photos/photo_upload.html', {'form': form})

def get_upload_progress(request):
    user_id = request.user.id
    redis_conn = get_redis_connection("default")
    progress = redis_conn.get(f"photo_upload_progress:{user_id}")
    total = redis_conn.get(f"photo_upload_total:{user_id}")
    
    if progress is None or total is None:
        return JsonResponse({'progress': 100})  # 업로드가 완료되었거나 시작되지 않았을 때
    
    progress = int(progress)
    total = int(total)
    percentage = (progress / total) * 100 if total > 0 else 0
    
    return JsonResponse({'progress': round(percentage)})

def upload_photo_without_login(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        form = MultiPhotoForm(request.POST, request.FILES)
        if form.is_valid() and username:
            # IP 주소와 User-Agent 로깅
            ip_address = request.META.get('HTTP_X_FORWARDED_FOR')
            if ip_address:
                ip_address = ip_address.split(',')[0].strip()
            else:
                ip_address = request.META.get('REMOTE_ADDR')
            user_agent = request.META.get('HTTP_USER_AGENT')
            logger.info(f"File upload attempt from IP: {ip_address}, User-Agent: {user_agent}")
            
            try:
                user = get_object_or_404(get_user_model(), username=username)
                files = request.FILES.getlist('images')
                descriptions = request.POST.getlist('descriptions')
                temp_dir = './photos/temp'
                if not os.path.exists(temp_dir):
                    os.makedirs(temp_dir)
                
                file_paths = []
                file_descriptions = []
                for i, f in enumerate(files):
                    description = descriptions[i].strip() if i < len(descriptions) else ''
                    fs = FileSystemStorage(location=temp_dir)
                    filename = fs.save(f.name, f)
                    file_path = os.path.join(temp_dir, filename)
                    file_paths.append(file_path)
                    file_descriptions.append(description)
                    
                process_and_save_photos.delay(file_paths, file_descriptions, user.id)
                return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/'))
            except Exception as e:
                return render(request, 'photos/upload_photo_without_login.html', {'form': form, 'error': str(e)})
        else:
            logger.warning("Form is not valid or username is missing")
    else:
        form = MultiPhotoForm()
    return render(request, 'photos/upload_photo_without_login.html', {'form': form})

ALLOWED_EXTENSIONS = ['.webp', '.avif', '.gif', '.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.mpeg', '.mpg', '.3gp', '.webm', '.ogg']

def is_valid_file(filename):
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXTENSIONS

@require_http_methods(["GET", "POST"])
def convert_video(request):
    if request.method == 'POST':
        try:
            file_info = json.loads(request.POST.get('file_info', '[]'))
            
            base_temp_dir = './photos/temp'
            convert_dir = './photos/converts'
            os.makedirs(convert_dir, exist_ok=True)

            task_info = []

            for i, info in enumerate(file_info):
                file = request.FILES.get(f'file_{i}')
                if not file or not is_valid_file(file.name):
                    return JsonResponse({'success': False, 'error': f'Invalid file type for file {i}'})
                
                file_uuid = info['fileUuid']
                
                temp_dir = os.path.join(base_temp_dir, file_uuid)
                os.makedirs(temp_dir, exist_ok=True)

                fs = FileSystemStorage(location=temp_dir)
                filename = fs.save(file.name, file)
                file_path = os.path.join(temp_dir, filename)

                start_time = float(info.get('startTime', 0))
                end_time = float(info.get('endTime', 0))
                frame_count = int(info.get('frameCount', 10))
                file_format = info['format']
                selectquality = info['quality']
                size = info['size']
                speed = info['speed']
                
                if file_format == 'gif':
                    if selectquality == 'low':
                        quality = 'bayer:bayer_scale=5'
                    elif selectquality == 'med':
                        quality = 'bayer:bayer_scale=4'
                    elif selectquality == 'high':
                        quality = 'floyd_steinberg'
                elif file_format == 'webp':
                    if selectquality == 'low':
                        quality = 50
                    elif selectquality == 'med':
                        quality = 57
                    elif selectquality == 'high':
                        quality = 65
                elif file_format == 'avif':
                    if selectquality == 'low':
                        quality = 43
                    elif selectquality == 'med':
                        quality = 35
                    elif selectquality == 'high':
                        quality = 23
                
                task = convert_file_to_animation.delay(file_path, temp_dir, file_uuid, start_time, end_time, frame_count, file_format, quality, size, speed)
                task_info.append({
                    'task_id': str(task.id),
                    'file_uuid': file_uuid,
                    'original_filename': file.name
                })

            return JsonResponse({'success': True, 'task_info': task_info})

        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    else:
        return render(request, 'photos/photo_convert.html')

@require_http_methods(["GET"])
def check_task_status(request, task_id):
    task = convert_file_to_animation.AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {
            'state': task.state,
            'status': 'Pending...'
        }
    elif task.state == 'PROGRESS':
        response = {
            'state': task.state,
            'progress': task.info.get('progress', 0)
        }
    elif task.state == 'SUCCESS':
        response = {
            'state': task.state,
            'result': task.result,
        }
    else:
        response = {
            'state': task.state,
            'status': str(task.info),
        }
    return JsonResponse(response)
##############################################################################################################################
#############################################  다운로드, 태그 수정 #############################################################
##############################################################################################################################

@login_required
def download_liked_photos(request):
    if not request.user.is_authenticated:
        return redirect('login')

    photos = Photo.objects.filter(liked_by=request.user)
    
    search_type = request.GET.get('search_type')
    query = request.GET.get('query')
    
    if search_type and query:
        if search_type == 'description':
            photos = photos.filter(description__icontains=query)
        elif search_type == 'username':
            photos = photos.filter(uploaded_by__username__icontains=query)
    
    zip_filename = "liked_photos.zip"
    zip_filepath = os.path.join(settings.MEDIA_ROOT, zip_filename)

    with zipfile.ZipFile(zip_filepath, 'w') as zip_file:
        for photo in photos:
            file_path = photo.image.path
            zip_file.write(file_path, os.path.basename(file_path))

    with open(zip_filepath, 'rb') as zip_file:
        response = HttpResponse(zip_file.read(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename={zip_filename}'
        return response
    
@login_required
def update_descriptions(request):
    if request.method == 'POST':
        photo_ids = request.POST.getlist('des_photo_ids')
        descriptions = request.POST.getlist('descriptions')

        for photo_id, description in zip(photo_ids, descriptions):
            try:
                photo = Photo.objects.get(id=photo_id, uploaded_by=request.user)
                if photo.description != description:
                    photo.description = description
                    photo.save()
            except Photo.DoesNotExist:
                continue
    return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/'))

@login_required
@require_POST
def update_description(request, photo_id):
    photo = get_object_or_404(Photo, id=photo_id)
    data = json.loads(request.body)
    new_description = data.get('description', '').strip()

    if request.user == photo.uploaded_by :
        photo.description = new_description
    elif request.user.id == 2: #그비 차단
        return JsonResponse({'success': False})
    else:
        if not new_description.startswith(photo.description):
            return JsonResponse({'success': False})
        Notification.objects.create(
        recipient=photo.uploaded_by,
        message=f"{request.user.username}님의 태그 추가({new_description[len(photo.description):]})",
        photo=photo,
        count=1,
        is_notice=False
        )
        photo.description = f"{photo.description} {new_description[len(photo.description):]}".strip()

    photo.save()
    
    return JsonResponse({'success': True})

##############################################################################################################################
##############################################   좋아요 싫어요   ##############################################################
##############################################################################################################################

@login_required
@require_POST
def like_photo(request, photo_id):
    if request.method == 'POST':
        try:
            photo = Photo.objects.get(id=photo_id)
            if request.user in photo.liked_by.all():
                photo.liked_by.remove(request.user)
                liked = False
            else:
                photo.liked_by.add(request.user)
                liked = True
            photo.likes = photo.liked_by.count()
            photo.save()
            liked_by = [{'username': u.username} for u in photo.liked_by.all()]
            return JsonResponse({'success': True, 'liked': liked, 'likes': photo.likes, 'liked_by': liked_by})
        except Photo.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Photo not found'})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
@require_POST
def dislike_photo(request, photo_id):
    photo = get_object_or_404(Photo, id=photo_id)
    user = request.user

    if user in photo.disliked_by.all():
        photo.disliked_by.remove(user)
        photo.dislikes -= 1
        disliked = False
    else:
        photo.disliked_by.add(user)
        photo.dislikes += 1
        disliked = True

    photo.save()
    
    owner_likes = 1 if photo.uploaded_by in photo.liked_by.all() else 0
    effective_likes = photo.likes - owner_likes
    hideContent = False

    if photo.dislikes - effective_likes >= 3:
        current_path = photo.image.path
        hideContent = True
        deleted_folder = os.path.join(os.path.dirname(current_path), 'deleted')
        if not os.path.exists(deleted_folder):
            os.makedirs(deleted_folder)
        new_path = os.path.join(deleted_folder, os.path.basename(current_path))

        shutil.move(current_path, new_path)
        redis_conn4.set(new_path, redis_conn4.get(current_path))
        redis_conn4.delete(current_path)
                        
        logger.info(f"Photo deleted: Time={timezone.now()}, Owner={photo.uploaded_by}, Filename={new_path}\nDislikes={photo.dislikes}|{[user.username for user in photo.disliked_by.all()]}, Likes={photo.likes}|{[user.username for user in photo.liked_by.all()]}, effectiveLikes={effective_likes}")
        photo.delete()
    
    return JsonResponse({'success': True, 'disliked': disliked, 'hideContent': hideContent})

##############################################################################################################################
##############################################   사진     삭제   ##############################################################
##############################################################################################################################

def get_image_hash(image_path):
    with Image.open(image_path) as img:
        return str(str(imagehash.phash(img, hash_size=16)))
    
@login_required
def delete_photos(request):
    if request.method == 'POST':
        photo_ids = request.POST.getlist('photos')
        photo_ids = [id.strip() for id in photo_ids[0].split(',') if id.strip()] if len(photo_ids) == 1 else [id.strip() for id in photo_ids if id.strip()]
        photos = Photo.objects.filter(id__in=photo_ids, uploaded_by=request.user)
        for photo in photos:
            # 파일 경로 가져오기
            file_path = os.path.join(settings.MEDIA_ROOT, photo.image.path)
            deleteFilePath = os.path.join(settings.MEDIA_ROOT, 'photos/trashcan') + "/" + os.path.basename(file_path)

            if os.path.exists(file_path):
                shutil.move(file_path, deleteFilePath)
                
            photo.delete()
            
            try:
                redis_key = photo.image.path
                checkname = redis_conn4.get(redis_key)
                if checkname is not None:
                    redis_conn4.delete(redis_key)
                    #logger.info(f"delete redis key\nredisKey: {checkname}\nPhoto: {photo.image.path}")
                else:
                    pendingPhoto = PendingApprovalPhoto.objects.get(pending_photo_path=redis_key)
                    pendingPhoto.delete()
                    #logger.info("similar photo is deleted")
            except Exception as e:
                logger.info(f"deleteRedisError Checkname: {checkname}\nPhoto: {photo.image.path}")
            
            #logger.info(f'Username: {request.user.username}, Deleting file: {deleteFilePath}')
            
        # Photo.objects.filter(id__in=photo_ids, uploaded_by=request.user).delete()
    return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/'))

@login_required
@require_POST
def delete_photo(request, photo_id):
    photo = get_object_or_404(Photo, id=photo_id)
    # previous_page = request.POST.get('previous_page', '/')
    
    if request.user == photo.uploaded_by or request.user.is_superuser:
        file_path = os.path.join(settings.MEDIA_ROOT, photo.image.path)
        
        deleteFilePath = os.path.join(settings.MEDIA_ROOT, 'photos/trashcan') + "/" + os.path.basename(file_path)
        if os.path.exists(file_path):
            shutil.move(file_path, deleteFilePath)
            
        photo.delete()
            
        try:
            redis_key = photo.image.path
            checkname = redis_conn4.get(redis_key)
            if checkname is not None:
                redis_conn4.delete(redis_key)
                #logger.info(f"delete redis key\nredisKey: {checkname}\nPhoto: {photo.image.path}")
            else:
                pendingPhoto = PendingApprovalPhoto.objects.get(pending_photo_path=redis_key)
                pendingPhoto.delete()
                #logger.info("similar photo is deleted")
        except Exception as e:
            logger.info(f"deleteRedisError Checkname: {checkname}\nPhoto: {photo.image.path}")
            
        #logger.info(f'Username: {request.user.username}, Deleting file: {deleteFilePath}')
            
        return JsonResponse({'success': True})
    else:
        return JsonResponse({'success': False, 'error': 'Not authorized'})
    
##############################################################################################################################
##############################################   댓글     관련   ##############################################################
##############################################################################################################################

@login_required
def add_comment(request, photo_id):
    if request.method == 'POST' and request.headers.get('x-requested-with') == 'XMLHttpRequest':
        photo = get_object_or_404(Photo, id=photo_id)
        form = CommentForm(request.POST)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.photo = photo
            comment.user = request.user
            parent_id = request.POST.get('parent')
            if parent_id:
                comment.parent = get_object_or_404(Comment, id=parent_id)
            comment.save()
            return JsonResponse({
                'success': True,
                'comment_html': render_to_string('comment.html', {'comment': comment}, request=request)
            })
    return JsonResponse({'success': False})
    
@login_required
@require_http_methods(["DELETE"])
def delete_comment(request, comment_id):
    comment = get_object_or_404(Comment, id=comment_id, user=request.user)
    comment.is_deleted = True
    comment.save()
    return JsonResponse({'success': True})

def my_comments(request):
    my_photo_comments = request.GET.get('my_photo_comments', 'false').lower() == 'true'
    comments_list = Comment.objects.filter(user=request.user).select_related('photo').order_by('-created_at')
    
    if my_photo_comments:
        someone_comments_list = Comment.objects.filter(photo__uploaded_by=request.user, parent__isnull=True).select_related('photo').order_by('-created_at')
        replies_to_my_comments = Comment.objects.filter(parent__in=comments_list).select_related('photo').order_by('-created_at')
        
        # Add type to each comment
        for comment in someone_comments_list:
            comment.type = "댓글"
        for comment in replies_to_my_comments:
            comment.type = "답글"
        
        comments_list = list(someone_comments_list) + list(replies_to_my_comments)
        comments_list = sorted(comments_list, key=lambda x: x.created_at, reverse=True)
    
    paginator = Paginator(comments_list, 10)

    page_number = request.GET.get('page')
    comments = paginator.get_page(page_number)
    
    for comment in comments:
        if comment.is_deleted:
            comment.text = "<삭제된 메세지입니다>"
    
    return render(request, 'photos/my_comments.html', {'comments': comments})

@login_required
def my_notifications(request):
    notifications = Notification.objects.filter(recipient_id=request.user.id).order_by('-created_at')
    
    paginator = Paginator(notifications, 10)
    page_number = request.GET.get('page')
    notifications = paginator.get_page(page_number)
    
    for notification in notifications:
        if notification.is_notice:
            notification.type = "공지"
            notification.url = "https://192.168.0.225/notice/"+ str(notification.notice_id)
        elif notification.is_pending:
            notification.type = "보류"
            notification.url = False
        else:
            notification.type = "알림"
            notification.url = "https://192.168.0.225/photo/" + str(Photo.objects.get(id=notification.photo_id).id)
    
    return render(request, 'photos/my_notifications.html', {'notifications': notifications})

@login_required
def delete_all_notifications(request):
    Notification.objects.filter(recipient=request.user).delete()
    return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/'))

##############################################################################################################################
##############################################   알림     기능   ##############################################################
##############################################################################################################################

@login_required
def mark_notification_as_read(request, notification_id):
    notification = get_object_or_404(Notification, id=notification_id, recipient=request.user)
    notification.is_read = True
    notification.save()
    
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        if notification.is_notice == True:
            redirect_url = f'https://192.168.0.225/notice/{notification.notice.id}'
            return JsonResponse({'status': 'success', 'redirect_url': redirect_url})
        elif notification.is_pending:
            return JsonResponse({'status': 'success', 'redirect_url': request.META.get('HTTP_REFERER', '/')})
        else:
            redirect_url = f'https://192.168.0.225/photo/{notification.photo.id}'
            return JsonResponse({'status': 'success', 'redirect_url': redirect_url})
            

@login_required
def mark_all_notifications_as_read(request):
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'status': 'success'})

##############################################################################################################################
##############################################   회원     관리   ##############################################################
##############################################################################################################################

def signup(request):
    if request.method == 'POST':
        form = SignUpForm(request.POST)
        ipFilter = User.objects.filter(lastip=request.META.get('HTTP_X_FORWARDED_FOR'))
        if ipFilter.count() > 0:
            return render(request, 'users/signup.html', {'form': form, 'errors': {'message': ['가입이 불가능합니다.']}})
        elif form.is_valid():
            user = form.save(commit=False)
            user.is_approved = False  # 승인 대기 상태로 설정
            user.terms_accepted = form.cleaned_data['terms']
            user.lastip = request.META.get('HTTP_X_FORWARDED_FOR')
            user.save()
            messages.success(request, '회원가입이 완료되었습니다. 관리자의 승인을 기다려주세요.')
            return redirect('login')
        else:
            errors = form.errors
    else:
        form = SignUpForm()
        errors = None
    return render(request, 'users/signup.html', {'form': form, 'errors': errors})

@method_decorator(ensure_csrf_cookie, name='dispatch')
class CustomLoginView(LoginView):
    template_name = 'users/login.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['terms_required'] = False
        return context

    def form_valid(self, form):
        user = form.get_user()
        if user.is_approved:
            if not self.request.POST.get('terms') and not user.terms_accepted:
                error_message = '약관에 동의해야 로그인할 수 있습니다.'
                return self.handle_error(error_message, terms_required=True)
            else:
                if not user.terms_accepted:
                    user.terms_accepted = True
                    user.save()
                login(self.request, user)
                return self.handle_success()
        else:
            error_message = '계정이 승인되지 않았습니다. 관리자에게 문의하세요.'
            return self.handle_error(error_message)

    def form_invalid(self, form):
        error_message = '로그인에 실패했습니다. 사용자 이름 또는 비밀번호를 확인하세요.'
        return self.handle_error(error_message)

    def handle_error(self, error_message, terms_required=False):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'error': error_message, 'csrfToken': get_token(self.request)}, status=400)
        messages.error(self.request, error_message)
        context = self.get_context_data()
        context['terms_required'] = terms_required
        return self.render_to_response(context)

    def handle_success(self):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'redirectUrl': self.get_success_url()})
        return redirect(self.get_success_url())

    def get(self, request, *args, **kwargs):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'csrfToken': get_token(request)})
        return super().get(request, *args, **kwargs)

def logout_view(request):
    logout(request)
    return redirect('photo_list')

def reset_password(request):
    if request.method == 'POST':
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            data = json.loads(request.body)
            username = data.get('username')
            phone = data.get('phone')
            logger.info(f"Reset password request: username={username}, phone={phone}")
            user = get_user_model().objects.filter(username=username, phone=phone).first()
            is_security = data.get('is_security')
            
            if is_security:
                security_answer = data.get('security_answer')
                hashed_answer = hashlib.sha256(security_answer.strip().encode('utf-8')).hexdigest()
                if user.security_answer == hashed_answer:
                    return JsonResponse({'success': True})
                else:
                    return JsonResponse({'success': False, 'message': '가입 시 답과 일치하지 않습니다. 답이 생각나지 않는 경우 관리자에게 문의하세요.'})
                
            if user:
                return JsonResponse({'success': True, 'security_question': user.security_question})
            else:
                return JsonResponse({'success': False, 'message': '일치하는 정보가 없습니다.'})

        username = request.POST.get('username')
        phone = request.POST.get('phone')
        user = get_user_model().objects.filter(username=username, phone=phone).first()
        security_answer = request.POST.get('security_answer')
        hashed_answer = hashlib.sha256(security_answer.encode()).hexdigest()
        new_password = request.POST.get('new_password')

        if user and user.security_answer == hashed_answer:
            user.set_password(new_password)
            user.save()
            messages.success(request, '비밀번호가 성공적으로 변경되었습니다. 로그인 해주세요.')
            return redirect('login')
        else:
            return redirect('reset_password')
        
    return render(request, 'users/reset_password.html')

@login_required
def edit_profile(request):
    if request.method == 'POST':
        form = EditProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, '프로필이 성공적으로 수정되었습니다.')
            if form.cleaned_data.get('password1'):
                messages.success(request, '비밀번호가 성공적으로 변경되었습니다. 다시 로그인 해주세요.')
                return redirect('login')  # 로그인 페이지로 리디렉션
            return redirect('edit_profile')  # 프로필 페이지로 리디렉션
    else:
        form = EditProfileForm(instance=request.user)
    
    return render(request, 'users/edit_profile.html', {'form': form})


##############################################################################################################################
##############################################   어드민 페이지   ##############################################################
##############################################################################################################################

def superuser_required(view_func):
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_superuser:
            return HttpResponseForbidden("이 페이지에 접근할 권한이 없습니다.")
        return view_func(request, *args, **kwargs)
    return _wrapped_view

@superuser_required
def admin_page(request):
    recent_photos = Photo.objects.order_by('-uploaded_at')[:10]
    recent_users = CustomUser.objects.order_by('-last_activity')[:30]
    recent_comments = Comment.objects.order_by('-created_at')[:10]
    hated_photos = Photo.objects.filter(dislikes__gt=0).order_by('-dislikes')
    pending_photos = PendingApprovalPhoto.objects.filter(is_rejected=False).order_by('-uploaded_at')
    unapproved_users = CustomUser.objects.filter(is_approved=False)
    
    for pending_photo in pending_photos:
        similarPhotoId = Photo.objects.get(image=pending_photo.similar_photo_path)
        if similarPhotoId:
            pending_photo.similar_photo_url = "photo/" + str(similarPhotoId.id) 
        else:
            pending_photo.similar_photo_url = "deleted"
        pendingPhotoId = Photo.objects.get(image=pending_photo.pending_photo_path)
        pending_photo.pendig_photo_url = "photo/" + str(pendingPhotoId.id)
        
    
    log_file_path = 'debug.log'
    
    for user in recent_users:
        user.photos = Photo.objects.filter(uploaded_by=user)
        user.uploaded_photos_count = user.photos.count()
        user.deleted_photos_count = DeletedPhoto.objects.filter(uploaded_by=user).count()
        user.total_likes = Photo.objects.filter(uploaded_by=user).aggregate(total_likes=Sum('likes'))['total_likes'] or 0
        user.total_dislikes = (Photo.objects.filter(uploaded_by=user).aggregate(total_dislikes=Sum('dislikes'))['total_dislikes'] or 0) + (user.deleted_photos_count * 3)
        user.like_ratio = "{:.2f}".format(user.total_likes / (user.uploaded_photos_count + user.deleted_photos_count)) if (user.uploaded_photos_count + user.deleted_photos_count) > 0 else 0
        user.liked_photos_count = user.liked_photos.count()
        user.comments_count = Comment.objects.filter(user=user).count()
        user.unread_notifications_count = Notification.objects.filter(recipient=user, is_read=False).count()
        
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        try:
            with open(log_file_path, 'r') as file:
                log_content = file.read()
        except FileNotFoundError:
            log_content = "로그 파일을 찾을 수 없습니다."
        
        response = JsonResponse({'log_content': log_content})
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        return response
    
    try:
        with open(log_file_path, 'r') as file:
            log_content = file.read()
    except FileNotFoundError:
        log_content = "로그 파일을 찾을 수 없습니다."
    
    context = {
        'recent_photos': recent_photos,
        'recent_users': recent_users,
        'recent_comments': recent_comments,
        'hated_photos': hated_photos,
        'pending_photos': pending_photos,
        'unapproved_users': unapproved_users,
        'log_content': log_content
    }
    
    return render(request, 'photos/admin_page.html', context)

@superuser_required
def user_activity(request, user_id):
    user = get_object_or_404(User, id=user_id)
    recent_comments = Comment.objects.filter(user=user).order_by('-created_at')[:20]
    hated_photos = Photo.objects.filter(uploaded_by=user).annotate(dislike_count=Count('disliked_by')).filter(dislike_count__gt=0).order_by('-dislike_count')[:20]
    like_photos = Photo.objects.annotate(like_count=Count('liked_by')).filter(liked_by=user).order_by('-uploaded_at')[:20]
    hate_photos = Photo.objects.annotate(dislike_count=Count('disliked_by')).filter(disliked_by=user).order_by('-dislike_count')[:20]

    recent_comments_data = [
        {
            'photo_id': comment.photo.id,
            'photo_url': comment.photo.image.url,
            'photo_description': comment.photo.description,
            'text': comment.text,
            'username': comment.user.username,
            'created_at': comment.created_at.strftime('%Y.%m.%d %H:%M')
        }
        for comment in recent_comments
    ]

    hated_photos_data = [
        {
            'photo_id': photo.id,
            'photo_url': photo.image.url,
            'uploaded_by': photo.uploaded_by.username,
            'disliked_by': [user.username for user in photo.disliked_by.all()]
        }
        for photo in hated_photos
    ]
    
    hate_photos_data = [
        {
            'photo_id': photo.id,
            'photo_url': photo.image.url,
            'uploaded_by': photo.uploaded_by.username,
            'disliked_by': [user.username for user in photo.disliked_by.all()]
        }
        for photo in hate_photos
    ]
    
    like_photos_data = [
        {
            'photo_id': photo.id,
            'photo_url': photo.image.url,
            'uploaded_by': photo.uploaded_by.username,
            'liked_by': [user.username for user in photo.liked_by.all()]
        }
        for photo in like_photos
    ]

    return JsonResponse({
        'recent_comments': recent_comments_data,
        'hated_photos': hated_photos_data,
        'hate_photos': hate_photos_data,
        'like_photos': like_photos_data
    })

@superuser_required
def approve_user(request):
    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        user = CustomUser.objects.get(id=user_id)
        user.is_approved = True
        user.save()
        return redirect('admin_page')

@superuser_required
def logout_all_users(request):
    sessions = Session.objects.all()
    logger.info(f"총 {sessions.count()}개의 세션이 발견되었습니다.")
    
    CustomUser = get_user_model()

    for session in sessions:
        session_data = session.get_decoded()
        user_id = session_data.get('_auth_user_id')
        
        if user_id:
            try:
                user = CustomUser.objects.get(id=user_id)
                
                if not user.is_superuser:
                    session.delete()
                    logger.info(f"유저 {user.username} (ID: {user_id})의 세션 삭제")
            except CustomUser.DoesNotExist:
                logger.warning(f"ID가 {user_id}인 유저를 찾을 수 없음")
                
    logger.info("모든 유저 로그아웃 완료")
    return JsonResponse({'message': '모든 유저가 로그아웃되었습니다.'})

@require_POST
@superuser_required
def cleanup_files(request):
    deleted_files_count = 0
    roots = ['photos/uploads', 'photos/converts']
    
    for directory in roots:
        uploads_dir = os.path.join(settings.MEDIA_ROOT, directory)
        uploaded_files = set(os.path.join(directory, file) for file in os.listdir(uploads_dir))
        db_files = set(Photo.objects.values_list('image', flat=True))
        files_to_delete = uploaded_files - db_files
        files_to_delete.discard(os.path.join(directory, '.gitkeep'))
        files_to_delete.discard(os.path.join(directory, 'x.png'))

        for file_name in files_to_delete:
            if os.path.isfile(file_name):
                shutil.move(file_name, os.path.join(settings.MEDIA_ROOT, 'photos/trashcan'))
                logger.info(f'이동된 파일: {file_name}\n이동된 경로: {os.path.abspath(os.path.join(settings.MEDIA_ROOT, "photos/trashcan/" + os.path.basename(file_name)))}')
                deleted_files_count += 1

    return JsonResponse({'message': f'파일 정리가 완료되었습니다. {deleted_files_count}개의 파일이 정리되었습니다.'})

from PIL import Image

@require_POST
@superuser_required
def convert_images(request):
    logger.info('이미지 변환 시작')
    converted_files_count = 0
    roots = ['photos/uploads']
    
    for root in roots:
        uploads_dir = os.path.join(settings.MEDIA_ROOT, root)
        for file_name in os.listdir(uploads_dir):
            file_path = os.path.join(uploads_dir, file_name)
            # jpeg, gif 파일이 아닌 경우 변환
            if file_name.lower().endswith(('.jpg', '.png', '.webp', '.bmp')):
                with Image.open(file_path) as img:
                    if file_name.lower().endswith('.webp') and img.n_frames > 1:
                        continue # Skip animated webp files
                    else:
                        logger.info(f'파일 변환 중: {file_name}')
                        rgb_img = img.convert('RGB')
                        new_file_name = os.path.splitext(file_name)[0] + '.jpeg'
                        new_file_path = os.path.join(uploads_dir, new_file_name)
                        rgb_img.save(new_file_path, 'JPEG', quality=85)
                        os.remove(file_path)
                        Photo.objects.filter(image=root + '/' + file_name).update(image=root + '/' + new_file_name)
                        converted_files_count += 1
                        logger.info(f'파일 변환 완료: {new_file_name}')

    logger.info(f'총 {converted_files_count}개의 파일이 변환되었습니다.')
    return JsonResponse({'message': f'파일 변환이 완료되었습니다. {converted_files_count}개의 파일이 변환되었습니다.'})

@superuser_required
def clear_log(request):
    log_file_path = 'debug.log'
    if request.method == 'POST':
        try:
            with open(log_file_path, 'w') as log_file:
                log_file.truncate(0)
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
        
def notice_list(request):
    if request.method == 'POST':
        notice_ids = request.POST.get('notice_id')
        if notice_ids:
            notice_ids_list = notice_ids.split(',')
            for notice_id in notice_ids_list:
                try:
                    notice = Notice.objects.get(id=notice_id)
                    notice.delete()
                except Notice.DoesNotExist:
                    # Handle the case where the notice does not exist
                    pass
            return redirect('notice_list')

    search_query = request.GET.get('q', '')
    if search_query:
        notices = Notice.objects.filter(
            Q(title__icontains=search_query) | Q(content__icontains=search_query)
        ).order_by('-created_at')
    else:
        notices = Notice.objects.all().order_by('-created_at')

    important_notices = notices.filter(is_important=True)
    regular_notices = notices.filter(is_important=False)

    paginator = Paginator(regular_notices, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'important_notices': important_notices,
        'page_obj': page_obj,
        'search_query': search_query,
    }
    return render(request, 'notices/notice_list.html', context)

def notice_detail(request, id):
    notice = get_object_or_404(Notice, id=id)
    
    if request.method == 'POST':
        if request.user == notice.author:
            notice.delete()
            return redirect('notice_list')
        else:
            return HttpResponseForbidden("You are not allowed to delete this notice.")
    
    return render(request, 'notices/notice_detail.html', {'notice': notice})

@superuser_required
def notice_create(request):
    if request.method == 'POST':
        form = NoticeForm(request.POST)
        if form.is_valid():
            notice = form.save(commit=False)
            notice.author = request.user
            notice.save()
            return redirect('notice_list')
    else:
        form = NoticeForm()
    return render(request, 'notices/notice_form.html', {'form': form})

@superuser_required
def notice_edit(request, notice_id):
    notice = get_object_or_404(Notice, id=notice_id)
    if request.user != notice.author:
        return HttpResponseForbidden("You are not allowed to edit this notice.")
    
    if request.method == 'POST':
        form = NoticeForm(request.POST, instance=notice)
        if form.is_valid():
            form.save()
            return redirect(reverse('notice_detail', args=[notice.id]))
    else:
        form = NoticeForm(instance=notice)
    
    return render(request, 'notices/notice_form.html', {'form': form, 'notice': notice})

#모두에게 보이는 사용자 목록을 가져오는 함수
def get_active_users(request):
    one_minute_ago = timezone.now() - timedelta(minutes=1)
    active_users = CustomUser.objects.filter(is_approved=True, last_activity__gte=one_minute_ago).exclude(id=request.user.id).order_by('-last_activity')
    return [request.user.username] + list(active_users.values_list('username', flat=True))

@require_http_methods(["GET"])
def get_resource_data(request):
    duration = int(request.GET.get('duration', 300))  # 기본 1시간
    end_time = int(time.time())
    start_time = end_time - duration
    
    index = redis_conn3.lrange('resource_index', 0, -1)
    
    data = []
    prev_net_upload = None
    prev_net_download = None
    prev_timestamp = None
    
    for timestamp in index:
        timestamp = int(timestamp.decode())
        if start_time <= timestamp <= end_time:
            cached_data = redis_conn3.get(f'resource:{timestamp}')
            if cached_data:
                resource_data = json.loads(cached_data)
                if prev_net_upload is not None and prev_net_download is not None and prev_timestamp is not None:
                    time_diff = timestamp - prev_timestamp
                    net_upload_rate = max(0, (resource_data['net_upload'] - prev_net_upload) / time_diff / 1024 / 1024)  # MB/s
                    net_download_rate = max(0, (resource_data['net_download'] - prev_net_download) / time_diff / 1024 / 1024)  # MB/s
                else:
                    net_upload_rate = net_download_rate = 0
                
                data.append({
                    'timestamp': timestamp,
                    'cpu_percent': resource_data['cpu'],
                    'ram_used': resource_data['ram'],
                    'net_upload': round(net_upload_rate, 4),
                    'net_download': round(net_download_rate, 4)
                })
                
                prev_net_upload = resource_data['net_upload']
                prev_net_download = resource_data['net_download']
                prev_timestamp = timestamp
    
    return JsonResponse({'data': data[-1:0:-1]})

@require_http_methods(["GET"])
def get_high_cpu_times(request):
    threshold = float(request.GET.get('threshold', 80.0))
    duration = int(request.GET.get('duration', 86400))  # 기본 24시간
    end_time = int(time.time())
    start_time = end_time - duration
    
    index = redis_conn3.lrange('resource_index', 0, -1)
    
    high_cpu_data = []
    prev_net_upload = None
    prev_net_download = None
    prev_timestamp = None
    
    for timestamp in index:
        timestamp = int(timestamp.decode())
        if start_time <= timestamp <= end_time:
            cached_data = redis_conn3.get(f'resource:{timestamp}')
            if cached_data:
                resource_data = json.loads(cached_data)
                if resource_data['cpu'] > threshold:
                    if prev_net_upload is not None and prev_net_download is not None and prev_timestamp is not None:
                        time_diff = timestamp - prev_timestamp
                        net_upload_rate = max(0, (resource_data['net_upload'] - prev_net_upload) / time_diff / 1024 / 1024)  # MB/s
                        net_download_rate = max(0, (resource_data['net_download'] - prev_net_download) / time_diff / 1024 / 1024)  # MB/s
                    else:
                        net_upload_rate = net_download_rate = 0
                    
                    high_cpu_data.append({
                        'timestamp': timestamp,
                        'cpu_percent': resource_data['cpu'],
                        'ram_used': resource_data['ram'],
                        'net_upload': round(net_upload_rate, 4),
                        'net_download': round(net_download_rate, 4)
                    })
                
                prev_net_upload = resource_data['net_upload']
                prev_net_download = resource_data['net_download']
                prev_timestamp = timestamp
    
    return JsonResponse({'data': high_cpu_data[::-1]})

@superuser_required
def protected_temp_media(request, path):
    file_path = os.path.join('./photos/temp', path)
    return FileResponse(open(file_path, 'rb'))

import io
from django.core.files.base import ContentFile

@superuser_required
@require_POST
def approve_photo(request):
    data = json.loads(request.body)
    photo_id = data.get('photo_id')
    photo_status = data.get('photo_status')
    
    if photo_status == 'deleted':
        pending_photo = PendingApprovalPhoto.objects.get(id=photo_id)

        file_path = pending_photo.pending_photo_path
        image_hash = get_image_hash(file_path)
        
        with open(file_path, 'rb') as file:
            file_content = file.read()
            file_extension = os.path.splitext(file_path)[1].lower()
            
            image = Image.open(io.BytesIO(file_content))
            if file_extension == '.gif' or (file_extension == '.webp' and image.n_frames > 1):
                original_file = ContentFile(file_content, name=os.path.basename(file_path))
                photo = Photo(image=original_file, description=pending_photo.description, uploaded_by=pending_photo.user, uploaded_at=pending_photo.uploaded_at)
                
            else:
                if image.mode is not 'RGB':
                    image = image.convert('RGB')
                jpeg_image_io = io.BytesIO()
                image.save(jpeg_image_io, format='JPEG', quality=85)
                jpeg_image = ContentFile(jpeg_image_io.getvalue(), name=f"{os.path.splitext(os.path.basename(file_path))[0]}.jpeg")
                
                photo = Photo(image=jpeg_image, description=pending_photo.description, uploaded_by=pending_photo.user, uploaded_at=pending_photo.uploaded_at)
            
            photo.save()
            
            redis_conn4.set(f"{'photo/uploads/'}{os.path.basename(file_path)}", image_hash)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError as e:
            logger.error(f"Error deleting file {file_path}: {e}")
    
    # PendingApprovalPhoto 객체 삭제
    pending_photo.delete()
    return JsonResponse({'success': True})

#deleted -> pendingphoto 객체만 삭제
#similarlink -> 사진 삭제
@superuser_required
@require_POST
def reject_photo(request):
    data = json.loads(request.body)
    photo_id = data.get('photo_id')
    photo_status = data.get('photo_status')
    
    if photo_status == 'deleted':
        pending_photo = PendingApprovalPhoto.objects.get(id=photo_id)
        pending_photo.delete()
    else:
        pending_photo = PendingApprovalPhoto.objects.get(id=photo_id)
        photo = Photo.objects.get(image=pending_photo.pending_photo_path)
        
        file_path = os.path.join(settings.MEDIA_ROOT, photo.image.path)
        deleteFilePath = os.path.join(settings.MEDIA_ROOT, 'photos/trashcan') + "/" + os.path.basename(file_path)

        if os.path.exists(file_path):
            shutil.move(file_path, deleteFilePath)
            
        photo.delete()
        
        redis_conn4.delete(file_path)
        
        pending_photo.delete()
    
    return JsonResponse({'success': True})


import requests
def photoshare_proxy_image(request):
    url = request.GET.get('url')
    if not url:
        return HttpResponse(status=400, content="URL parameter is missing")

    response = requests.get(url)
    if response.status_code == 200:
        return HttpResponse(response.content, content_type=response.headers['Content-Type'])
    else:
        return HttpResponse(status=response.status_code, content="Failed to fetch the image")