import os
import io
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
import multiprocessing

from PIL import Image, ImageOps, ImageSequence
from io import BytesIO
import imagehash
import pillow_avif

import re
import tempfile

from celery import shared_task,  group, chord
from django.core.files.base import ContentFile
from django.contrib.auth import get_user_model
from webpush import send_group_notification
from django_redis import get_redis_connection

from photos.models import Photo, PendingApprovalPhoto, Block, Notification

# 로깅 설정
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

# 전역 변수로 ThreadPoolExecutor 생성
executor = ThreadPoolExecutor(max_workers=multiprocessing.cpu_count())

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
    
    # SCAN 명령어를 사용하여 큰 데이터셋에서도 효율적으로 작동하도록 합니다.
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


import socket
def get_server_ip():
    hostname = socket.gethostname()
    ip_address = socket.gethostbyname(hostname)
    return ip_address

@shared_task
def process_file(file_path, description, user_id):
    User = get_user_model()
    user = User.objects.get(id=user_id)
    
    try:
        if not os.path.exists(file_path):
            logger.info(f"File not found: {file_path}")
            return
        
        file_extension = os.path.splitext(file_path)[1].lower()
        
        # 영상 파일 처리
        if file_extension in ['.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.mpeg', '.mpg', '.3gp', '.webm', '.ogg']:
            avif_file_path = f"{os.path.splitext(file_path)[0]}.avif" # ex) /path/to/video.mp4 -> /path/to/video.webp
            try:
                command = [
                    'ffmpeg', '-i', file_path, '-c:v', 'libsvtav1', '-crf', '23', '-b:v', '0',
                    '-cpu-used', '4', '-tile-columns', '2', '-tile-rows', '2',
                    '-loop', '0', '-an',
                    '-r', '20', '-threads', '0', '-vf', 'scale=\'min(800,iw)\':-2,pad=iw:ceil(ih/2)*2',
                    '-frames:v', '600', avif_file_path
                ]
                subprocess.run(command, check=True)
                os.remove(file_path)
            except Exception as e:
                logger.error(f"Error while converting video to WebP: {e}")
                return None
            process_path = avif_file_path
        else:
            process_path = file_path
        
        # 이미지 해시 계산
        image_hash = get_image_hash(process_path)
        
        similar_filename = check_similarity_with_redis(image_hash)

        # 이미지 저장
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
                # 기존 Photo 객체가 있는 경우
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
        logger.error(f"Error processing file {file_path}: {str(e)}")
    
    finally:
        try:
            redis_conn = get_redis_connection("default")
            redis_conn.incr(f"photo_upload_progress:{user_id}")
        except Exception as e:
            logger.error(f"Error updating Redis: {str(e)}")

@shared_task
def finalize_processing(user_id, photoscount, results):
    User = get_user_model()
    user = User.objects.get(id=user_id)
    
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
        logger.info(f"User {user.username} uploaded {uploadedPhotoscount} photos.")
        if photoscountafter > photoscount:
            send_push_message_to_all(user_id, uploadedPhotoscount)
        
        try:
            redis_conn = get_redis_connection("default")
            redis_conn.delete(f"photo_upload_progress:{user_id}")
            redis_conn.delete(f"photo_upload_total:{user_id}")
        except Exception as e:
            logger.error(f"Error deleting Redis keys: {e}")
        
    except Exception as e:
        logger.error(f"Error processing files: {e}")

@shared_task
def process_and_save_photos(file_paths, descriptions, user_id, preserve_order):
    photoscount = Photo.objects.filter(uploaded_by_id=user_id).count()
    total_files = len(file_paths)
    
    redis_conn = get_redis_connection("default")
    
    redis_conn.set(f"photo_upload_progress:{user_id}", 0)
    redis_conn.set(f"photo_upload_total:{user_id}", total_files)

    if preserve_order == 'true':
        tasks = [process_file.s(file_path, description, user_id) 
                 for file_path, description in zip(file_paths, descriptions)]
        chord(tasks)(finalize_processing.s(user_id, photoscount))
    else:
        tasks = [process_file.s(file_path, description, user_id,) 
                 for file_path, description in zip(file_paths, descriptions)]
        chord(group(tasks))(finalize_processing.s(user_id, photoscount))

    return "Processing started"

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

def extract_frames(file_uuid, file_path, temp_dir):
    with Image.open(file_path) as img:
        frame_count = getattr(img, 'n_frames', 1)
        duration = img.info.get('duration')
        haveDuration = True
        if duration is None:
            duration = 50
            haveDuration = False
            
        
        frames = []
        for i in range(frame_count):
            img.seek(i)
            frame_path = os.path.join(temp_dir, f"{file_uuid}_{i:03d}.png")
            img.save(frame_path, "PNG")
            frames.append((f"{file_uuid}_{i:03d}.png", duration))
        
        return frames, haveDuration

@shared_task(bind=True)
def convert_file_to_animation(self, file_path, temp_dir, file_uuid, start_time, end_time, frame_count, file_format, quality, size, speed):
    try:
        filename = os.path.basename(file_path)
        output_filename = f"{file_uuid}.{file_format}"
        output_path = os.path.join('./photos/converts', output_filename)
        
        start_time = float(start_time)
        end_time = float(end_time)
        frame_count = int(frame_count)
        speed = float(speed)
        
        is_video = any(file_path.lower().endswith(ext) for ext in ['.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.mpeg', '.mpg', '.3gp', '.webm', '.ogg'])
        
        if is_video:
            duration = end_time - start_time if end_time > start_time else 0
            command = ['ffmpeg', '-i', file_path, '-ss', str(start_time)]
            if duration:
                command.extend(['-t', str(duration/speed)])
            
            command.extend(['-filter:v', f'setpts={1/speed}*PTS'])
        elif file_path.lower().endswith('webp') and file_format == 'avif':
            frames, haveDuration = extract_frames(file_uuid, file_path, temp_dir)
            temp_file_path = os.path.join(temp_dir, f"{file_uuid}_frames.txt")
            
            with open(temp_file_path, 'w') as temp_file_info:
                for frame_path, duration in frames:
                    temp_file_info.write(f"file '{frame_path}'\n")
                    temp_file_info.write(f"duration {duration/(1000*speed)}\n")
            
            command = [
                'ffmpeg', '-f', 'concat', '-safe', '0', '-i', temp_file_path
            ]
            
            if not haveDuration:
                command.extend(['-r', str(20)])  # 기본 프레임 레이트 설정
        else:
            command = ['ffmpeg', '-i', file_path]
            command.extend(['-filter:v', f'setpts={1/speed}*PTS'])

        if file_format == 'webp':
            command.extend([
                '-c:v', 'libwebp', '-lossless', '0', 
                '-q:v', str(quality), '-preset', 'picture', '-loop', '0', '-an',
                '-vf', f'scale=\'if(gt(iw,{size}),{size},iw)\':-1,setpts={1/speed}*PTS'
            ])
            if is_video:
                command.extend(['-r', str(frame_count)])
        elif file_format == 'avif':
            command.extend([
                '-c:v', 'libsvtav1', '-crf', str(quality), '-b:v', '0',
                '-cpu-used', '4', '-tile-columns', '2', '-tile-rows', '2',
                '-loop', '0', '-an',
                '-vf', f'scale=\'min({size},iw)\':-2:eval=frame:force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2,setpts={1/speed}*PTS'
            ])
            if is_video:
                command.extend(['-r', str(frame_count)])
        elif file_format == 'gif':
            filter_complex = f'[0:v] fps={frame_count},setpts={1/speed}*PTS,scale=\'if(gt(iw,{size}),{size},iw)\':-1:flags=bilinear,split [a][b];[a] palettegen=reserve_transparent=on:transparency_color=ffffff [p];[b][p] paletteuse=dither={quality}'
            command.extend([
                '-filter_complex', filter_complex,
                '-loop', '0'
            ])
        else:
            raise ValueError(f"Unsupported format: {file_format}")

        command.extend(['-progress', 'pipe:1', output_path])
        
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        
        pattern = re.compile(r'frame=\s*(\d+)')
        total_frames = frame_count * (end_time - start_time) if is_video else 1

        for line in process.stdout:
            matches = pattern.search(line)
            if matches:
                current_frame = int(matches.group(1))
                progress = min(100, int((current_frame / total_frames) * 100))
                self.update_state(state='PROGRESS', meta={'progress': progress})

        process.wait()

        if process.returncode != 0:
            raise Exception(f"FFmpeg command failed with return code {process.returncode}")
        
        return {
            'success': True,
            'original': f"/photos/temp/{filename}",
            'converted': f"/photos/converts/{output_filename}",
            'file_uuid': file_uuid,
            'file_size': os.path.getsize(output_path)
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'file_uuid': file_uuid
        }
    finally:
        # Clean up temporary directory
        if os.path.exists(temp_dir):
            for file in os.listdir(temp_dir):
                os.remove(os.path.join(temp_dir, file))
            os.rmdir(temp_dir)