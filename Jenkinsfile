/* groovylint-disable LineLength */
def majorVersion = ''
def minorVersion = ''
def patchVersion = ''
def buildSkipped = false


pipeline {
    agent {
        label 'ubuntu22-vm'
    }
    options {
        disableConcurrentBuilds(abortPrevious: false)
    }
    environment {
        DOCKER_REGISTRY = 'nexus-registry.decian.net'
        IMAGE_NAME = 'misp-provisioner'
        GIT_URL = 'git@github.com:Decian-Inc/misp-provisioner-py.git'
    }

    stages {
        stage('Skip?') {
        agent any
        steps {
            script {
                if (sh(script: "git log -1 --pretty=%B | fgrep -ie '[skip ci]' -e '[ci skip]'", returnStatus: true) == 0) {
                    def isManualTrigger = currentBuild.rawBuild.getCauses()[0].toString().contains('UserIdCause')
                    if (!isManualTrigger) {
                        currentBuild.result = 'SUCCESS'
                        currentBuild.description = 'Build skipped due to commit message'
                        buildSkipped = true
                        return
                    }
                }
            }
        }
        }
        stage('Checkout') {
            when {
                expression { return !buildSkipped }
            }
            steps {
                // checkout scm
                checkout changelog: false,
                    scm: scmGit(
                        branches: [[name: env.BRANCH_NAME]],
                        userRemoteConfigs: [[
                            credentialsId: 'jenkins-github-ssh-key',
                            url: env.GIT_URL ]]
                        )
            }
        }

        stage('Version Management') {

            steps {
                script {
                    def version = readFile("${env.WORKSPACE}/VERSION").trim()
                    (majorVersion, minorVersion, patchVersion) = version.tokenize('.')

                   // display version info
                    echo "Current Version: ${majorVersion}.${minorVersion}.${patchVersion}"

                    if (env.BRANCH_NAME == 'main' && !buildSkipped) {
                        // Bump Patch Version, commit
                        patchVersion = patchVersion.toInteger() + 1
                        echo "New Version: ${majorVersion}.${minorVersion}.${patchVersion}"
                        sh "echo ${majorVersion}.${minorVersion}.${patchVersion} > VERSION"
                    }
                    currentBuild.displayName = "# ${majorVersion}.${minorVersion}.${patchVersion}.${env.BUILD_NUMBER} | ${BRANCH_NAME}"

                }
            }
        }

        stage('Build Push Docker image') {
            when {
                expression { return !buildSkipped }
            }
            steps {
                script {
                    def version = "${majorVersion}.${minorVersion}.${patchVersion}"
                    def dockerTags = [
                        "${version}-${env.BRANCH_NAME.replaceAll("/", "-")}-${env.BUILD_NUMBER}",
                        "${version}-${env.BRANCH_NAME.replaceAll("/", "-")}"
                    ]

                    if (env.BRANCH_NAME == 'main') {
                        dockerTags.add("${version}")
                        dockerTags.add("${majorVersion}.${minorVersion}")
                        dockerTags.add("${majorVersion}")
                    }

                    def dockerBuildCommandTags = dockerTags.collect { tag -> "-t $DOCKER_REGISTRY/$IMAGE_NAME:${tag}" }.join(' ')
                    
                    docker.withRegistry('https://nexus-registry.decian.net', 'nexus-docker-writer-username-password') {
                          sh """
                            docker build --build-arg VERSION=$version --push $dockerBuildCommandTags .
                          """                        
                    }
                }
            }
        }

        stage('Re-Commit Version Management') {
            when {
                expression { return !buildSkipped }
            }
            steps {
                script {
                    if (env.BRANCH_NAME == 'main') {
                        sh "git add VERSION"
                        sh "git commit -m '[skip ci] Update VERSION'"
                        withCredentials([sshUserPrivateKey(credentialsId: 'jenkins-github-ssh-key', keyFileVariable: 'SSH_KEY')]) {
                            sh """
                                GIT_SSH_COMMAND='ssh -i \$SSH_KEY' git push ${GIT_URL.replace('git@github.com:', 'git@github.com:')} HEAD:main
                            """
                        }
                    }
                }
            }
        }


    }
}