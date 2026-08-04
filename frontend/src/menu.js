/**
 * 侧边栏菜单配置（与路由分离：路由在 router.js，这里只管展示分组/图标/文案）。
 * 新增页面 = router.js 加路由 + 这里对应分组加一项；
 * 不进菜单的页面（如 /clients、/child-age-leads）只加路由即可。
 * 分组 key 是 el-sub-menu 的 index（稳定标识，改 title 不影响展开逻辑）。
 */
import {
  Files, User, Monitor, Tools,
  DocumentChecked, Tickets, Document, DataAnalysis,
  UserFilled, FolderOpened, AlarmClock,
  Cpu, List, Connection, Bell,
  MagicStick, EditPen, Scissor, Link,
} from '@element-plus/icons-vue'

export const menuGroups = [
  {
    key: 'archive',
    title: '文件留底',
    icon: Files,
    items: [
      { path: '/archive-daily-report', title: '每日留底检测报告', icon: DataAnalysis },
      { path: '/archive-admin', title: '检测批次管理', icon: Tickets },
      { path: '/file-info', title: '文件信息查询', icon: Document },
      { path: '/archive-detect', title: '文件留底检测', icon: DocumentChecked },
    ],
  },
  {
    key: 'profile',
    title: '客户画像',
    icon: User,
    items: [
      { path: '/profile', title: '画像任务', icon: UserFilled },
      { path: '/file-assign', title: '文件归属', icon: FolderOpened },
      { path: '/expiry-reminders', title: '到期提醒', icon: AlarmClock },
    ],
  },
  {
    key: 'logs',
    title: '日志监控',
    icon: Monitor,
    items: [
      { path: '/ai-api-calls', title: 'AI 调用记录', icon: Cpu },
      { path: '/request-logs', title: '外部请求日志', icon: List },
      { path: '/external-api-logs', title: '调用外部接口记录', icon: Connection },
      { path: '/events', title: '系统事件', icon: Bell },
    ],
  },
  {
    key: 'tools',
    title: '工具箱',
    icon: Tools,
    items: [
      { path: '/parse', title: 'AI 材料解析', icon: MagicStick },
      { path: '/template', title: 'AI 填写文件', icon: EditPen },
      { path: '/split', title: '处理超长 PDF', icon: Scissor },
      { path: '/summary', title: 'URL 文件摘要', icon: Link },
    ],
  },
]
