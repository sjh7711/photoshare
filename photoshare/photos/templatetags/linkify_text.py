from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe
import re

register = template.Library()

@register.filter
def linkify(text):
    # URL을 감지하기 위한 간단한 정규표현식
    url_pattern = re.compile(r'(https?://\S+)')
    
    # URL을 찾아 <a> 태그로 감싸기
    def replace_url(match):
        url = match.group(1)
        return f'<a href="{url}" target="_blank" rel="noopener noreferrer" class="text-blue-600 hover:underline">{url}</a>'
    
    linked_text = url_pattern.sub(replace_url, text)
    return mark_safe(linked_text)