/**
 * 路由表（hash 模式，部署时不需要 nginx fallback）。
 *
 * meta.public  免登录白名单(登录页)
 * meta.title   页签标题(TagsView)
 * meta.cache   组件名(keep-alive include 查找;与各页面 defineOptions name 一一对应,
 *              异步组件加载前拿不到 name,必须走 meta 不能靠组件内省)
 */
import { createRouter, createWebHashHistory } from 'vue-router'

const TOKEN_KEY = 'doc_review_token'

const routes = [
  { path: '/login', component: () => import('./components/LoginPage.vue'), meta: { public: true } },
  { path: '/', component: () => import('./components/HomePage.vue'), meta: { title: '首页', cache: 'HomePage' } },
  { path: '/clients', component: () => import('./components/ClientListPage.vue'), meta: { title: '客户档案', cache: 'ClientListPage' } },
  // 客户详情是多实例路由(/clients/1、/clients/2 同名组件),keep-alive 按组件名匹配对它
  // 会"刷新一个误杀其他缓存 + 关闭后实例残留",故不设 meta.cache 不参与保活(tab 正常用)
  { path: '/clients/:clientId', component: () => import('./components/ClientDetailPage.vue'), props: route => ({ clientId: Number(route.params.clientId) }), meta: { title: '客户详情' } },
  { path: '/parse', component: () => import('./components/ParseEntryPage.vue'), meta: { title: 'AI 材料解析', cache: 'ParseEntryPage' } },
  { path: '/template', component: () => import('./components/FillEntryPage.vue'), meta: { title: 'AI 填写文件', cache: 'FillEntryPage' } },
  { path: '/split', component: () => import('./components/SplitEntryPage.vue'), meta: { title: '处理超长 PDF', cache: 'SplitEntryPage' } },
  { path: '/summary', component: () => import('./components/SummaryEntryPage.vue'), meta: { title: 'URL 文件摘要', cache: 'SummaryEntryPage' } },
  { path: '/archive-detect', component: () => import('./components/ArchiveDetectEntryPage.vue'), meta: { title: '文件留底检测', cache: 'ArchiveDetectEntryPage' } },
  { path: '/archive-admin', component: () => import('./components/ArchiveAdminPage.vue'), meta: { title: '检测批次管理', cache: 'ArchiveAdminPage' } },
  { path: '/archive-daily-report', component: () => import('./components/ArchiveDailyReportPage.vue'), meta: { title: '每日留底检测报告', cache: 'ArchiveDailyReportPage' } },
  { path: '/file-info', component: () => import('./components/FileInfoPage.vue'), meta: { title: '文件信息查询', cache: 'FileInfoPage' } },
  { path: '/profile', component: () => import('./components/ProfilePage.vue'), meta: { title: '画像任务', cache: 'ProfilePage' } },
  { path: '/expiry-reminders', component: () => import('./components/ExpiryRemindersPage.vue'), meta: { title: '到期提醒', cache: 'ExpiryRemindersPage' } },
  { path: '/file-assign', component: () => import('./components/FileAssignPage.vue'), meta: { title: '文件归属', cache: 'FileAssignPage' } },
  { path: '/events', component: () => import('./components/EventsPage.vue'), meta: { title: '系统事件', cache: 'EventsPage' } },
  { path: '/request-logs', component: () => import('./components/RequestLogsPage.vue'), meta: { title: '外部请求日志', cache: 'RequestLogsPage' } },
  { path: '/external-api-logs', component: () => import('./components/ExternalApiLogsPage.vue'), meta: { title: '调用外部接口记录', cache: 'ExternalApiLogsPage' } },
  { path: '/ai-api-calls', component: () => import('./components/AiApiCallsPage.vue'), meta: { title: 'AI 调用记录', cache: 'AiApiCallsPage' } },
  { path: '/child-age-leads', component: () => import('./components/ChildAgeLeadsPage.vue'), meta: { title: '子女年龄线索', cache: 'ChildAgeLeadsPage' } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

// 全局前置守卫:无 token 跳登录页(白名单路由放行)
router.beforeEach((to, from, next) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (to.meta.public || token) {
    // 已登录却访问登录页 -> 跳首页
    if (to.path === '/login' && token) return next('/')
    return next()
  }
  // 未登录,跳登录页(带 redirect 参数)
  return next({ path: '/login', query: { redirect: to.fullPath } })
})

export default router
