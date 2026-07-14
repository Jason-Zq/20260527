<template>
  <div class="login-page">
    <!-- 动态背景光斑 -->
    <div class="bg-orb orb-1"></div>
    <div class="bg-orb orb-2"></div>
    <div class="bg-orb orb-3"></div>
    <div class="bg-grid"></div>

    <div class="login-card">
      <div class="ai-avatar">
        <div class="ai-ring"></div>
        <div class="ai-core">AI</div>
      </div>
      <h2 class="login-title">智能文档审核工作台</h2>
      <p class="login-sub">Intelligent Document Review · AI Powered</p>

      <div class="form-group">
        <div class="input-wrap">
          <span class="input-icon">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
              <circle cx="12" cy="7" r="4"/>
            </svg>
          </span>
          <input
            v-model="username"
            type="text"
            placeholder="账号"
            class="ai-input"
            @keyup.enter="onLogin"
          />
        </div>

        <div class="input-wrap">
          <span class="input-icon">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="11" width="18" height="11" rx="2"/>
              <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
            </svg>
          </span>
          <input
            v-model="password"
            type="password"
            placeholder="密码"
            class="ai-input"
            @keyup.enter="onLogin"
          />
        </div>
      </div>

      <button class="ai-btn" :disabled="loading" @click="onLogin">
        <span v-if="!loading" class="btn-content">
          进入工作台
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5">
            <path d="M5 12h14M12 5l7 7-7 7"/>
          </svg>
        </span>
        <span v-else class="btn-loading">
          <span class="spinner"></span> 验证中...
        </span>
      </button>

      <transition name="fade">
        <p v-if="errMsg" class="login-err">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>
          </svg>
          {{ errMsg }}
        </p>
      </transition>

      <p class="login-foot">© 2026 Globevisa · Document AI Platform</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { login, setToken } from '../api'

const router = useRouter()
const route = useRoute()
const username = ref('')
const password = ref('')
const loading = ref(false)
const errMsg = ref('')

async function onLogin() {
  if (!username.value.trim() || !password.value.trim()) {
    errMsg.value = '请输入账号和密码'
    return
  }
  loading.value = true
  errMsg.value = ''
  try {
    const res = await login(username.value.trim(), password.value.trim())
    if (res.ok && res.token) {
      setToken(res.token)
      ElMessage.success('登录成功')
      const redirect = route.query.redirect || '/'
      router.replace(redirect)
    } else {
      errMsg.value = res.detail || '登录失败'
    }
  } catch (e) {
    errMsg.value = '登录请求失败:' + (e.message || '网络错误')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  position: relative;
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background: radial-gradient(ellipse at top, #0f172a 0%, #020617 60%, #000 100%);
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

/* 动态光斑 */
.bg-orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(80px);
  opacity: 0.5;
  animation: float 12s ease-in-out infinite;
}
.orb-1 { width: 480px; height: 480px; background: #6366f1; top: -120px; left: -80px; }
.orb-2 { width: 400px; height: 400px; background: #8b5cf6; bottom: -100px; right: -60px; animation-delay: -4s; }
.orb-3 { width: 300px; height: 300px; background: #06b6d4; top: 40%; left: 50%; animation-delay: -8s; }

@keyframes float {
  0%, 100% { transform: translate(0, 0) scale(1); }
  33% { transform: translate(40px, -30px) scale(1.08); }
  66% { transform: translate(-30px, 40px) scale(0.95); }
}

/* 网格背景 */
.bg-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(99, 102, 241, 0.06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(99, 102, 241, 0.06) 1px, transparent 1px);
  background-size: 44px 44px;
  mask-image: radial-gradient(ellipse at center, black 30%, transparent 75%);
}

/* 玻璃拟态卡片 */
.login-card {
  position: relative;
  width: 380px;
  background: rgba(15, 23, 42, 0.55);
  backdrop-filter: blur(24px) saturate(160%);
  -webkit-backdrop-filter: blur(24px) saturate(160%);
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 24px;
  padding: 44px 36px 32px;
  box-shadow: 0 30px 80px rgba(0, 0, 0, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.08);
  text-align: center;
  z-index: 1;
}

/* AI 头像 */
.ai-avatar {
  position: relative;
  width: 72px;
  height: 72px;
  margin: 0 auto 20px;
}
.ai-core {
  position: absolute;
  inset: 12px;
  background: linear-gradient(135deg, #6366f1, #8b5cf6, #06b6d4);
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 22px;
  font-weight: 800;
  letter-spacing: 1px;
  box-shadow: 0 0 30px rgba(99, 102, 241, 0.6);
}
.ai-ring {
  position: absolute;
  inset: 0;
  border: 2px solid transparent;
  border-radius: 20px;
  background: linear-gradient(135deg, #6366f1, #06b6d4) border-box;
  -webkit-mask: linear-gradient(#fff 0 0) padding-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  animation: spin 6s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.login-title {
  font-size: 21px;
  font-weight: 700;
  color: #f1f5f9;
  margin-bottom: 6px;
  letter-spacing: 0.5px;
}
.login-sub {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 28px;
  letter-spacing: 1.5px;
  text-transform: uppercase;
}

.form-group { display: flex; flex-direction: column; gap: 14px; }
.input-wrap {
  position: relative;
  display: flex;
  align-items: center;
}
.input-icon {
  position: absolute;
  left: 14px;
  color: #64748b;
  display: flex;
  pointer-events: none;
}
.ai-input {
  width: 100%;
  padding: 14px 14px 14px 44px;
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 12px;
  color: #f1f5f9;
  font-size: 14px;
  outline: none;
  transition: all 0.2s;
  box-sizing: border-box;
}
.ai-input::placeholder { color: #475569; }
.ai-input:focus {
  border-color: #6366f1;
  background: rgba(15, 23, 42, 0.85);
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15);
}

/* AI 按钮 */
.ai-btn {
  width: 100%;
  margin-top: 22px;
  padding: 14px;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  border: none;
  border-radius: 12px;
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.25s;
  box-shadow: 0 8px 24px rgba(99, 102, 241, 0.35);
}
.ai-btn:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 12px 32px rgba(99, 102, 241, 0.5);
}
.ai-btn:disabled { opacity: 0.7; cursor: not-allowed; }
.btn-content { display: inline-flex; align-items: center; gap: 8px; }
.btn-loading { display: inline-flex; align-items: center; gap: 8px; }
.spinner {
  width: 16px; height: 16px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  display: inline-block;
}

.login-err {
  margin-top: 16px;
  font-size: 13px;
  color: #fca5a5;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}
.fade-enter-active, .fade-leave-active { transition: opacity 0.3s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

.login-foot {
  margin-top: 28px;
  font-size: 11px;
  color: #334155;
  letter-spacing: 0.5px;
}
</style>
