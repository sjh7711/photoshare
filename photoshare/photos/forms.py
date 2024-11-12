# photos/forms.py

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import get_user_model
from .models import Photo, Comment, Notice
import re
from django.core.exceptions import ValidationError
import hashlib
import logging
logger = logging.getLogger(__name__)

class SignUpForm(UserCreationForm):
    terms = forms.BooleanField(required=True, error_messages={'required': '약관에 동의해야 합니다.'})
    security_question = forms.CharField(max_length=255, min_length=1, required=True, error_messages={'min_length': '질문은 최소 1글자 이상이어야 합니다.'})
    security_answer = forms.CharField(max_length=255, min_length=1, required=True, error_messages={'min_length': '답변은 최소 1글자 이상이어야 합니다.'})

    class Meta:
        model = get_user_model()
        fields = ['username', 'phone', 'password1', 'password2', 'terms', 'security_question', 'security_answer']
    
    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        if not re.match(r'^010\d{8}$', phone):
            raise ValidationError('전화번호는 010XXXXXXXX 형식이어야 합니다.')
        return phone

    def save(self, commit=True):
        user = super().save(commit=False)
        user.phone = self.cleaned_data['phone']
        user.security_question = self.cleaned_data.get('security_question')
        security_answer = self.cleaned_data.get('security_answer')
        if security_answer:
            user.security_answer = hashlib.sha256(security_answer.strip().encode('utf-8')).hexdigest()
        if commit:
            user.save()
            # 약관 동의 여부를 기록
            user.terms_accepted = True
            user.save()
        return user

# class PhotoForm(forms.ModelForm):
#     class Meta:
#         model = Photo
#         fields = ['image', 'description']

class EditProfileForm(forms.ModelForm):
    password1 = forms.CharField(label='비밀번호', widget=forms.PasswordInput, required=False)
    password2 = forms.CharField(label='비밀번호 확인', widget=forms.PasswordInput, required=False)
    security_question = forms.CharField(max_length=255, min_length=1, required=True, error_messages={'min_length': '질문은 최소 1글자 이상이어야 합니다.'})
    security_answer = forms.CharField(max_length=255, min_length=1, required=True, error_messages={'min_length': '질문은 최소 1글자 이상이어야 합니다.'})

    class Meta:
        model = get_user_model()
        fields = ['username', 'phone', 'password1', 'password2', 'security_question', 'security_answer']

    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        if not re.match(r'^010\d{8}$', phone):
            raise ValidationError('전화번호는 010XXXXXXXX 형식이어야 합니다.')
        return phone

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")

        if password1 and password1 != password2:
            raise ValidationError("비밀번호가 일치하지 않습니다.")

        return cleaned_data

    def save(self, commit=True):        
        user = super().save(commit=False)
        user.phone = self.cleaned_data['phone']
        password = self.cleaned_data.get('password1')
        user.security_question = self.cleaned_data.get('security_question')
        security_answer = self.cleaned_data.get('security_answer').strip()
        
        current_user = get_user_model().objects.get(pk=user.pk)
        db_security_answer = current_user.security_answer
        
        if security_answer != db_security_answer:
            hashed_security_answer = hashlib.sha256(security_answer.strip().encode('utf-8')).hexdigest()
            user.security_answer = hashed_security_answer
        if password:
            user.set_password(password)
        if commit:
            user.save()
        return user

class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            result = [single_file_clean(d, initial) for d in data]
        else:
            result = single_file_clean(data, initial)
        return result

class MultiPhotoForm(forms.Form):
    images = MultipleFileField(label='Images', required=False)
    descriptions = forms.CharField(widget=forms.Textarea, required=False)
    
    def clean_images(self):
        images = self.files.getlist('images')
        if len(images) > 20:
            raise forms.ValidationError("You can upload a maximum of 20 files.")
        return images
    
class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ['text', 'parent']
        widgets = {
            'parent': forms.HiddenInput()
        }

class PhotoSearchForm(forms.Form):
    query_description = forms.CharField(label='query_description', max_length=255, required=False)
    query_username = forms.CharField(label='query_username', max_length=255, required=False)
    date_range = forms.CharField(label='date_range', max_length=255, required=False)
    
from django import forms
from .models import Notice

class NoticeForm(forms.ModelForm):
    class Meta:
        model = Notice
        fields = ['title', 'content', 'is_important']
        labels = {
            'title': '제목',
            'content': '내용',
            'is_important': '중요 공지',
        }
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'mt-1 p-2 block w-full rounded-md border-gray-300 dark:border-gray-700 shadow-sm focus:border-indigo-300 focus:ring focus:ring-indigo-200 focus:ring-opacity-50 dark:bg-gray-700 dark:text-white'
            }),
            'content': forms.Textarea(attrs={
                'class': 'mt-1 p-4 block w-full rounded-md border-gray-300 dark:border-gray-700 shadow-sm focus:border-indigo-300 focus:ring focus:ring-indigo-200 focus:ring-opacity-50 dark:bg-gray-700 dark:text-white',
                'rows': 35,
                'style': 'overflow: hidden;'
            }),
            'is_important': forms.CheckboxInput(attrs={
                'class': 'rounded border-gray-300 dark:border-gray-600 text-indigo-600 shadow-sm focus:border-indigo-300 focus:ring focus:ring-offset-0 focus:ring-indigo-200 focus:ring-opacity-50 dark:bg-gray-700 dark:focus:ring-offset-gray-800'
            }),
        }