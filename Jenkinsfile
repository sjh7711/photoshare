pipeline {
    agent any

    environment {
        DOCKER_REGISTRY = "192.168.0.223"
        DOCKER_CREDENTIALS_ID = "harbor" // Jenkins에 저장된 Docker 자격 증명 ID
        IMAGE_NAME1 = "photoshare/photoshare"
        IMAGE_NAME2 = "photosharecelery/photosharecelery"
    }
    
    stages {
        stage('Checkout') {
            steps {
                git branch: 'master',
                    url: 'https://github.com/sjh7711/photoshare.git'
            }
        }
        
        stage('photoshare Build and Push to Harbor') {
            steps {
                script {
                    docker.withRegistry("http://${DOCKER_REGISTRY}", DOCKER_CREDENTIALS_ID) {
                        def app = docker.build("${DOCKER_REGISTRY}/${IMAGE_NAME1}:${BUILD_NUMBER}")
                        app.push()
                    }
                }
            }
        }

        stage('celery Build and Push to Harbor') {
            steps {
                script {
                    docker.withRegistry("http://${DOCKER_REGISTRY}", DOCKER_CREDENTIALS_ID) {
                        def app = docker.build("${DOCKER_REGISTRY}/${IMAGE_NAME2}:${BUILD_NUMBER}", "photoshare")
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