pipeline {
  agent any

  options {
    ansiColor('xterm')
    timestamps()
  }

  parameters {
    booleanParam(name: 'DEPLOY_VALIDATION', defaultValue: false, description: 'Run the seeded smoke E2E gate for deploy validation.')
    booleanParam(name: 'RUN_SMOKE_E2E', defaultValue: false, description: 'Run the smoke E2E gate on demand.')
    booleanParam(name: 'RUN_FULL_CERTIFICATION', defaultValue: false, description: 'Run the full certification stack on demand.')
  }

  triggers {
    cron('H 2 * * 1-5')
  }

  environment {
    CI = 'true'
    FORCE_COLOR = '1'
  }

  stages {
    stage('Checkout') {
      steps {
        checkout scm
      }
    }

    stage('Bootstrap Test Runtime') {
      steps {
        sh '''#!/usr/bin/env bash
set -euo pipefail

./scripts/bootstrap-validation-runtime.sh

cd ui-web
npm ci
npx playwright install chromium
'''
      }
    }

    stage('Fast Checks') {
      steps {
        sh '''#!/usr/bin/env bash
set -euo pipefail
./scripts/run-fast-checks.sh
'''
      }
    }

    stage('Truth Parity Gate') {
      steps {
        sh '''#!/usr/bin/env bash
set -euo pipefail
./scripts/run-truth-parity-gate.sh
'''
      }
    }

    stage('Smoke E2E') {
      when {
        anyOf {
          expression { return params.RUN_SMOKE_E2E }
          expression { return params.DEPLOY_VALIDATION }
        }
      }
      steps {
        sh '''#!/usr/bin/env bash
set -euo pipefail
./scripts/run-smoke-e2e.sh
'''
      }
    }

    stage('Full Certification') {
      when {
        anyOf {
          expression { return params.RUN_FULL_CERTIFICATION }
          triggeredBy 'TimerTrigger'
        }
      }
      steps {
        sh '''#!/usr/bin/env bash
set -euo pipefail
./scripts/run-full-certification.sh
'''
      }
    }
  }

  post {
    always {
      archiveArtifacts artifacts: 'artifacts/**/*,test-results/**/*,ui-web/test-results/**/*,ui-web/playwright-report/**/*', allowEmptyArchive: true
    }
  }
}
