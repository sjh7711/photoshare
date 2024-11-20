#!/bin/bash

# 업데이트할 YAML 파일 목록
YAML_FILES=(
    "argo/celery_deployment.yaml"
    "argo/photoshare_deployment.yaml"
)

for FILE in "${YAML_FILES[@]}"
do
    # 현재 이미지 버전 추출
    CURRENT_VERSION=$(grep 'image: ' "$FILE" | sed -E 's/.*:(.*)/\1/' | head -n 1)
    
    # 버전 번호에 +1
    NEW_VERSION=$((CURRENT_VERSION + 1))
    
    # 이미지 버전 업데이트
    sed -i "s/\(image:.*:\).*/\1$NEW_VERSION/" "$FILE"
done