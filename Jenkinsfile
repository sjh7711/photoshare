pipeline {
    agent any
    
    environment {
        DOCKER_IMAGE = 'photoshare'
        HARBOR_REGISTRY = '192.168.0.223'
        HARBOR_PROJECT = 'photoshare'
        HARBOR_CREDENTIALS = credentials('harbor')
    }
    
    stages {
        stage('Checkout') {
            steps {
                git branch: 'master',
                    url: 'https://github.com/sjh7711/photoshare.git'
            }
        }
        
        stage('Build Docker Image') {
            steps {
                script {
                    def dockerImage = docker.build("${HARBOR_REGISTRY}/${HARBOR_PROJECT}/${DOCKER_IMAGE}:${BUILD_NUMBER}")
                }
            }
        }
        
        stage('Push to Harbor') {
            steps {
                script {
                    docker.withRegistry("https://${HARBOR_REGISTRY}", 'harbor-credentials') {
                        dockerImage.push()
                        dockerImage.push('latest')
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