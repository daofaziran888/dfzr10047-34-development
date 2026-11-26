import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import QuarantineReviewView from '@/views/QuarantineReviewView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/quarantine-review', name: 'quarantine-review', component: QuarantineReviewView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
