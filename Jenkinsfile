// doc-review-ci: push main → 测试 → 构建 → 打制品 → 自动部署测试服
// Job 由 Poll SCM 每 2 分钟轮询 GitHub 触发(零公网暴露,不用 webhook)
pipeline {
  agent any
  options {
    timestamps()
    disableConcurrentBuilds()
    buildDiscarder(logRotator(numToKeepStr: '20'))
  }
  environment {
    NPM_CONFIG_REGISTRY = 'https://registry.npmmirror.com'
    NODE_OPTIONS = '--max-old-space-size=1024'
    PYTHONIOENCODING = 'utf-8'
    PYTHONUTF8 = '1'
  }
  stages {
    stage('Test') {
      steps { sh 'bash tests/run_ci.sh' }
    }
    stage('Build') {
      steps { sh 'cd frontend && npm ci && npm run build' }
    }
    stage('Package') {
      steps {
        sh 'rm -rf dist-out && mkdir -p dist-out && bash deploy/ci/package.sh . dist-out'
        archiveArtifacts artifacts: 'dist-out/release-*.tar.gz', fingerprint: true, onlyIfSuccessful: true
      }
    }
    stage('Deploy Test') {
      steps {
        sh '''
          PKG=$(ls dist-out/release-*.tar.gz | head -1)
          echo "部署制品: $PKG"
          sudo /bin/bash "$WORKSPACE/deploy/ci/release.sh" --target test --package "$WORKSPACE/$PKG"
        '''
      }
    }
  }
  post {
    failure { echo '构建失败 - 未部署。按阶段名定位: Test=测试红 / Build=前端构建挂 / Deploy=发布脚本失败(服务未动或已自动回滚)。' }
  }
}
