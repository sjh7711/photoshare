import os
import io
import subprocess

from PIL import Image, ImageOps, ImageSequence
from io import BytesIO
import imagehash
import pillow_avif

import re

from celery import shared_task,  group, chord
from django.core.files.base import ContentFile
from django.contrib.auth import get_user_model
from webpush import send_group_notification
from django_redis import get_redis_connection

from photos.models import Photo, PendingApprovalPhoto, Block, Notification


def get_image_hash(image_path):
    with Image.open(image_path) as img:
        return str(str(imagehash.phash(img, hash_size=16)))
    
def save_image_hash_to_redis(filename, image_hash):
    redis_conn = get_redis_connection("uploaded_photo")
    redis_conn.set(filename, image_hash)

def check_similarity_with_redis(image_hash, min_distance_threshold=15):
    redis_conn = get_redis_connection("uploaded_photo")
    
    min_distance = float('inf')
    min_distance_filename = None
    
    cursor = '0'
    while cursor != 0:
        cursor, keys = redis_conn.scan(cursor=cursor, match='*', count=100)
        for hash_key in keys:
            stored_hash = redis_conn.get(hash_key)
            if stored_hash:
                image_diff = imagehash.hex_to_hash(stored_hash.decode('utf-8')) - imagehash.hex_to_hash(image_hash)
                if image_diff < min_distance:
                    min_distance = image_diff
                    min_distance_filename = hash_key.decode('utf-8')
    
    if min_distance <= min_distance_threshold:
        return min_distance_filename
    else:
        return None

@shared_task
def process_and_save_photos(file_paths, descriptions, user_id, preserve_order):
    results = []
    
    try:
        User = get_user_model()
        user = User.objects.get(id=user_id)
        photoscount = Photo.objects.filter(uploaded_by_id=user_id).count()
        total_files = len(file_paths)

        redis_conn = get_redis_connection("default")
        redis_conn.set(f"photo_upload_progress:{user_id}", 0)
        redis_conn.set(f"photo_upload_total:{user_id}", total_files)
    except Exception as e:
        results.append(f"Error initializing processing: {e}, {file_paths}, userid: {user_id}")

    for file_path, description in zip(file_paths, descriptions):
        try:
            if not os.path.exists(file_path):
                results.append(f"File not found: {file_path}")
                continue

            file_extension = os.path.splitext(file_path)[1].lower()

            # 영상 파일 처리
            if file_extension in ['.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.mpeg', '.mpg', '.3gp', '.webm', '.ogg']:
                avif_file_path = f"{os.path.splitext(file_path)[0]}.avif"
                try:
                    command = [
                        'ffmpeg', '-i', file_path, '-c:v', 'libsvtav1', '-crf', '23', '-b:v', '0',
                        '-cpu-used', '4', '-tile-columns', '2', '-tile-rows', '2',
                        '-loop', '0', '-an',
                        '-r', '20', '-threads', '0', '-vf', "scale='min(800,iw)':-2,pad=iw:ceil(ih/2)*2",
                        '-frames:v', '600', avif_file_path
                    ]
                    subprocess.run(command, check=True)
                    os.remove(file_path)
                except Exception as e:
                    results.append(f"Error converting video to AVIF: {e}, {file_path}, userid: {user_id}")
                    continue
                process_path = avif_file_path
            else:
                process_path = file_path

            # 이미지 해시 계산
            try:
                image_hash = get_image_hash(process_path)
                similar_filename = check_similarity_with_redis(image_hash)
            except Exception as e:
                results.append(f"Error calculating image hash: {e}, {file_path}, userid: {user_id}")
                continue

            # 이미지 저장
            try:
                with open(process_path, 'rb') as file:
                    file_content = file.read()

                if file_extension in ['.gif', '.avif', '.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.mpeg', '.mpg', '.3gp', '.webm', '.ogg'] or (file_extension == '.webp' and Image.open(process_path).n_frames > 1):
                    image_file = ContentFile(file_content, name=os.path.basename(process_path))
                else:
                    image = Image.open(io.BytesIO(file_content))
                    if image.mode != 'RGB':
                        image = image.convert('RGB')
                    jpeg_image_io = io.BytesIO()
                    image.save(jpeg_image_io, format='JPEG', quality=85)
                    image_file = ContentFile(jpeg_image_io.getvalue(), name=f"{os.path.splitext(os.path.basename(process_path))[0]}.jpeg")

                photo = Photo(image=image_file, description=description, uploaded_by=user)
            except Exception as e:
                results.append(f"Error reading file: {e}, {file_path}, userid: {user_id}")
                continue
            
            try:
                if similar_filename:
                    pending_photo = PendingApprovalPhoto(
                        description=description,
                        user=user,
                        is_rejected=False,
                        similar_photo_path=similar_filename,
                    )
                    if "uploads/deleted/" in similar_filename:
                        pending_photo.pending_photo_path = 'photos/temp/' + os.path.basename(process_path)
                    else:
                        photo.save()
                        pending_photo.pending_photo_path = 'photos/uploads/' + os.path.basename(photo.image.path)
                        os.remove(process_path)

                    pending_photo.save()
                else:
                    # 비슷한 파일이 없는 경우
                    photo.save()
                    os.remove(process_path)
                    # 이미지 해시를 Redis에 저장
                    save_image_hash_to_redis('photos/uploads/' + os.path.basename(photo.image.path), image_hash)
            except Exception as e:
                results.append(f"Error saving photo: {e}, {file_path}, userid: {user_id}")
                continue

        except Exception as e:
            results.append(f"Error processing file: {e}, {file_path}, userid: {user_id}")
        finally:
            try:
                redis_conn.incr(f"photo_upload_progress:{user_id}")
            except Exception as e:
                results.append(f"Error updating progress: {e}, {file_path}, userid: {user_id}")

    # 처리 완료 후 작업
    try:
        try:
            pendingPhotosCount = PendingApprovalPhoto.objects.filter(user_id=user_id, is_rejected=False).count()
            if pendingPhotosCount > 0:
                Notification.objects.create(
                    recipient_id=1,
                    message=f"{user.username}의 {pendingPhotosCount}개의 사진이 이미 업로드 된 사진과 유사합니다.",
                    count=1,
                    is_pending=True
                )
                pendingNotification = Notification.objects.filter(recipient_id=user_id, is_read=False, is_pending=True).first()
                if pendingNotification:
                    pendingNotification.message = f"{pendingPhotosCount}개의 사진이 이미 업로드 된 사진과 유사합니다."
                    pendingNotification.save()
                else:
                    Notification.objects.create(
                        recipient_id=user_id,
                        message=f"{pendingPhotosCount}개의 사진이 이미 업로드 된 사진과 유사합니다.",
                        count=1,
                        is_pending=True
                    )
            photoscountafter = Photo.objects.filter(uploaded_by_id=user_id).count()
            uploadedPhotoscount = photoscountafter - photoscount
        except Exception as e:
            results.append(f"Error checking pending photos: {e}, {file_paths}, userid: {user_id}")
        
        try:
            if photoscountafter > photoscount:
                send_push_message_to_all(user_id, uploadedPhotoscount)
        except Exception as e:
            results.append(f"Error sending push message: {e}, {file_paths}, userid: {user_id}")

        redis_conn.delete(f"photo_upload_progress:{user_id}")
        redis_conn.delete(f"photo_upload_total:{user_id}")
        
        results.append(f"Processing completed: {file_paths} photos uploaded")

    except Exception as e:
        results.append(f"Error finalizing processing: {e}, {file_paths}, userid: {user_id}")
    
    return results

def send_push_message_to_all(user_id, uploadedPhotoscount):
    User = get_user_model()
    user = User.objects.get(id=user_id)
    lastPhoto = Photo.objects.filter(uploaded_by=user).order_by('-uploaded_at').first()
    
    if lastPhoto:
        image_path = lastPhoto.image.path
        image = Image.open(image_path)
        
        if image.format == 'GIF':
            image = image.convert('RGBA')
            image = next(ImageSequence.Iterator(image))
        elif image.format == 'WEBP':
            image = next(ImageSequence.Iterator(image))
        
        original_width, original_height = image.size
        new_width = 300
        new_height = int((new_width / original_width) * original_height)
        image = image.resize((new_width, new_height), Image.LANCZOS)

        thumbnail_size = (300, 170)
        image = ImageOps.pad(image, thumbnail_size, method=Image.LANCZOS, color=(0, 0, 0, 0), centering=(0.5, 0.5))

        image_io = BytesIO()
        image.save(image_io, format='PNG')
        image_io.seek(0)

        resized_icon_folder = os.path.join(os.path.dirname(__file__), 'resized_icon')
        os.makedirs(resized_icon_folder, exist_ok=True)
        resized_image_path = os.path.join(resized_icon_folder, f'{lastPhoto.id}.png')
        with open(resized_image_path, 'wb') as f:
            f.write(image_io.getvalue())

        #WEB서버 주소가 필요.
        payload = {
            "head": "새 사진",
            "body": f"{user.username}님의 사진{uploadedPhotoscount}개 업로드",
            "badge": "https://hoegiphoto.shop/static/favicon/badge.png",
            "icon": "https://hoegiphoto.shop/static/favicon/android-icon-96x96.png",
            "image": f"https://hoegiphoto.shop/resized_icon/{lastPhoto.id}.png",
            "url": "/",
            "tag": "photo_upload"
        }
        
        excludeUser = Block.objects.filter(blocked=user).values_list('blocker_id', flat=True)
        
        send_group_notification(
            group_name='all_users',
            payload=payload, 
            ttl=1000,
            exclude_user_id=list(excludeUser)
        )