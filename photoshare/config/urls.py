from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from photos import views as photo_views
from django.contrib.auth import views as auth_views
from photos.views import CustomLoginView
from django.views.generic import TemplateView
from django.contrib.staticfiles.storage import staticfiles_storage
from django.views.generic.base import RedirectView

urlpatterns = [    
    path('robots.txt', TemplateView.as_view(template_name="robots.txt", content_type="text/plain")),
    path('googlec0e3679d0a117faa.html', TemplateView.as_view(template_name="googlec0e3679d0a117faa.html", content_type="text/plain")),
    path('favicon.ico', RedirectView.as_view(url=staticfiles_storage.url('favicon/favicon.ico'))),
    
    path('webpush/', include('webpush.urls')),
    path('check_device_id/', photo_views.check_device_id, name='check_device_id'),
    path('toggle_push_notification/', photo_views.toggle_push_notification, name='toggle_push_notification'),

    # Service Worker
    path('static/js/service-worker.js', TemplateView.as_view(template_name="static/js/service-worker.js", content_type='application/javascript'), name='service-worker.js'),

    # Subscription 저장
    path('save_subscription/', photo_views.save_subscription, name='save_subscription'),
    
    path('photoshare_proxy_image/', photo_views.photoshare_proxy_image, name='photoshare_proxy_image'),
    
    path('get_upload_progress/', photo_views.get_upload_progress, name='get_upload_progress'),
    path('photos/temp/<path:path>', photo_views.protected_temp_media, name='protected_temp_media'),
    path('approve_photo/', photo_views.approve_photo, name='approve_photo'),
    path('reject_photo/', photo_views.reject_photo, name='reject_photo'),
    
    path('admin_page/', photo_views.admin_page, name='admin_page'),
    path('approve_user/', photo_views.approve_user, name='approve_user'),
    path('logout_all_users/', photo_views.logout_all_users, name='logout_all_users'),
    path('notices/', photo_views.notice_list, name='notice_list'),
    path('notices/create/', photo_views.notice_create, name='notice_create'),
    path('notice/<int:notice_id>/edit/', photo_views.notice_edit, name='notice_edit'),
    path('notice/<int:id>/', photo_views.notice_detail, name='notice_detail'),
    path('user_activity/<int:user_id>/', photo_views.user_activity, name='user_activity'),
    
    path('api/resource-data/', photo_views.get_resource_data, name='resource_data'),
    path('api/high-cpu-times/', photo_views.get_high_cpu_times, name='high_cpu_times'),

    path('signup/', photo_views.signup, name='signup'),
    path('login/', CustomLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    path('block_user/', photo_views.block_user, name='block_user'),
    path('unblock_user/', photo_views.unblock_user, name='unblock_user'),
    path('block_user_page/', photo_views.block_user_page, name='block_user_page'),
    path('', photo_views.photo_list, name='photo_list'),
    path('my_photos/', photo_views.my_photos, name='my_photos'),
    # path('today/', photo_views.today_photos, name='today_photos'),
    path('get_photo_data/', photo_views.get_photo_data, name='get_photo_data'),
    path('liked_photos/', photo_views.liked_photos, name='liked_photos'),
    path('photo/<int:photo_id>/', photo_views.photo_detail, name='photo_detail'),
    path('dislike_photo/<int:photo_id>/', photo_views.dislike_photo, name='dislike_photo'),
    path('my_comments/', photo_views.my_comments, name='my_comments'),
    path('my_notifications/', photo_views.my_notifications, name='my_notifications'),
    path('delete_all_notifications/', photo_views.delete_all_notifications, name='delete_all_notifications'),
    path('my_statistics/', photo_views.my_statistics, name='my_statistics'),
    path('common_user_activity/<int:user_id>/', photo_views.common_user_activity, name='common_user_activity'),
    
    path('upload/', photo_views.upload_photo, name='upload_photo'),
    path('download_liked_photos/', photo_views.download_liked_photos, name='download_liked_photos'),
    
    path('update_descriptions/', photo_views.update_descriptions, name='update_descriptions'),
    path('update_description/<int:photo_id>/', photo_views.update_description, name='update_description'),
    path('edit_profile/', photo_views.edit_profile, name='edit_profile'),
    path('reset_password/', photo_views.reset_password, name='reset_password'),

    path('like_photo/<int:photo_id>/', photo_views.like_photo, name='like_photo'),
    path('add_comment/<int:photo_id>/', photo_views.add_comment, name='add_comment'),
    path('delete_comment/<int:comment_id>/', photo_views.delete_comment, name='delete_comment'),
    path('delete_photos/', photo_views.delete_photos, name='delete_photos'),
    path('photo/<int:photo_id>/delete/', photo_views.delete_photo, name='delete_photo'),
    
    path('notification/read/<int:notification_id>/', photo_views.mark_notification_as_read, name='mark_notification_as_read'),
    path('notifications/read_all/', photo_views.mark_all_notifications_as_read, name='mark_all_notifications_as_read'),
    
    path('cleanup_files/', photo_views.cleanup_files, name='cleanup_files'),
    path('clear-log/', photo_views.clear_log, name='clear-log'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
