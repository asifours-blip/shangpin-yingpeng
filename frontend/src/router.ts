import { createRouter, createWebHistory } from 'vue-router'
import { api } from './api'
import Login from './views/Login.vue'
import Workbench from './views/Workbench.vue'
import History from './views/History.vue'
import AdminUsers from './views/AdminUsers.vue'
import AdminRecords from './views/AdminRecords.vue'
import Restricted from './views/Restricted.vue'
import SocialAccounts from './views/SocialAccounts.vue'
import Contacts from './views/Contacts.vue'
import Trends from './views/Trends.vue'
import Products from './views/Products.vue'
import CampaignReview from './views/CampaignReview.vue'
import PublishAccounts from './views/PublishAccounts.vue'
import Operations from './views/Operations.vue'
import CampaignCreate from './views/CampaignCreate.vue'
import CampaignDetail from './views/CampaignDetail.vue'
import OperationPlans from './views/OperationPlans.vue'
import OperationRun from './views/OperationRun.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: Login, meta: { public: true } },
    { path: '/', redirect: '/trends' },
    { path: '/trends', component: Trends },
    { path: '/operations', component: Operations },
    { path: '/campaigns/new', component: CampaignCreate },
    { path: '/campaigns/:id', component: CampaignDetail },
    { path: '/operation-plans', component: OperationPlans },
    { path: '/operation-runs/:id', component: OperationRun },
    { path: '/products', component: Products },
    { path: '/review', component: CampaignReview },
    { path: '/workbench', component: Workbench },
    { path: '/history', component: History },
    { path: '/restricted', component: Restricted },
    { path: '/social', component: SocialAccounts },
    { path: '/publish-accounts', component: PublishAccounts },
    { path: '/contacts', component: Contacts },
    { path: '/admin/users', component: AdminUsers, meta: { admin: true } },
    { path: '/admin/records', component: AdminRecords, meta: { admin: true } },
  ],
})

router.beforeEach(async (to) => {
  if (to.meta.public) return true
  try {
    const me = await api.me()
    if (to.meta.admin && me.role !== 'admin') {
      return { path: '/workbench' }
    }
    return true
  } catch {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
})

export default router
