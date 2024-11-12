from django.apps import AppConfig


class PhotosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'photos'

class PhotosConfig(AppConfig):
    name = 'photos'

    def ready(self):
        import photos.signals