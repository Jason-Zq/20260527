/**
 * 路由表（hash 模式，部署时不需要 nginx fallback）。
 *
 * - /                  HomePage
 * - /parse             AI 材料解析
 * - /template          AI 填写文件
 * - /split             处理超长 PDF
 * - /summary           文件解析（URL → 摘要，旧入口保留向后兼容）
 * - /archive-detect    文件留底检测（新）
 */
import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/', component: () => import('./components/HomePage.vue') },
  { path: '/clients', component: () => import('./components/ClientListPage.vue') },
  { path: '/clients/:clientId', component: () => import('./components/ClientDetailPage.vue'), props: route => ({ clientId: Number(route.params.clientId) }) },
  { path: '/parse', component: () => import('./components/ParseEntryPage.vue') },
  { path: '/template', component: () => import('./components/FillEntryPage.vue') },
  { path: '/split', component: () => import('./components/SplitEntryPage.vue') },
  { path: '/summary', component: () => import('./components/SummaryEntryPage.vue') },
  { path: '/archive-detect', component: () => import('./components/ArchiveDetectEntryPage.vue') },
  { path: '/archive-admin', component: () => import('./components/ArchiveAdminPage.vue') },
  { path: '/events', component: () => import('./components/EventsPage.vue') },
  { path: '/child-age-leads', component: () => import('./components/ChildAgeLeadsPage.vue') },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
