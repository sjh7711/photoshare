pipeline {
    agent any

    environment {
        DOCKER_REGISTRY = "192.168.0.223"
        DOCKER_CREDENTIALS_ID = "harbor" // Jenkins에 저장된 Docker 자격 증명 ID
        IMAGE_NAME = "photoshare/photoshare"
    }
    
    stages {
        stage('Checkout') {
            steps {
                git branch: 'master',
                    url: 'https://github.com/sjh7711/photoshare.git'
            }
        }
        
        stage('Build and Push to Harbor') {
            steps {
                script {
                    docker.withRegistry("http://${DOCKER_REGISTRY}", DOCKER_CREDENTIALS_ID) {
                        def app = docker.build("${DOCKER_REGISTRY}/${IMAGE_NAME}:${BUILD_NUMBER}")
                        app.push()
                    }
                }
            }
        }
    }
    
    post {
        always {
            cleanWs()
        }
    }
}