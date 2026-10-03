// 应用外壳：路由、主导航、数据订阅与视图装载。
import { parseHash, records, statusCounts, park } from './data.js';
import { mountStaticIcons, closeDrawerUnless } from './ui.js';
import * as orders from './views/orders.js';
import * as detail from './views/detail.js';
import * as devices from './views/devices.js';
import * as team from './views/team.js';

const VIEWS = { orders, detail, devices, team };
const TITLES = {
  orders: '报修管理',
  devices: '设备档案',
  team: '维修人员',
};
const SITE = '园里报修工作台';

const content = document.getElementById('content');
const navItems = [...document.querySelectorAll('.nav-item')];
const badge = document.getElementById('nav-pending');
const badgeText = document.getElementById('nav-pending-text');

const ctx = {
  ordersListHash: '#orders',
  route: { name: 'orders', id: '', query: {} },
  navigate(hash, { replace = false } = {}) {
    if (replace) {
      history.replaceState(null, '', hash);
      render();
      return;
    }
    if (location.hash === hash) {
      render();
      return;
    }
    location.hash = hash;
  },
};

let renderedKey = '';

function render() {
  const route = parseHash(location.hash);
  const key = `${route.name}/${route.id}?${JSON.stringify(route.query)}`;
  const routeChanged = key !== renderedKey;
  renderedKey = key;
  ctx.route = route;
  if (route.name === 'orders') ctx.ordersListHash = location.hash.startsWith('#orders') ? location.hash : '#orders';

  const activeName = route.name === 'detail' ? 'orders' : route.name;
  navItems.forEach(item => {
    const active = item.dataset.nav === activeName;
    item.classList.toggle('is-active', active);
    if (active) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  });

  const counts = statusCounts(records());
  badge.textContent = String(counts.pending);
  badge.hidden = counts.pending === 0;
  badgeText.textContent = counts.pending ? `，${counts.pending} 条待派单` : '';

  closeDrawerUnless(route.name === 'detail' ? `detail:${route.id}` : route.name);

  const view = VIEWS[route.name] || VIEWS.orders;
  view.mount(content, ctx);
  document.title = route.name === 'detail'
    ? `${route.id || '报修详情'} · ${SITE}`
    : `${TITLES[route.name] || TITLES.orders} · ${SITE}`;
  if (routeChanged) content.scrollTop = 0;
}

window.addEventListener('hashchange', render);
park.subscribe(render);

// 指向当前地址的链接（重置、当前页、当前页签）也要刷新视图。
document.addEventListener('click', event => {
  const link = event.target.closest('a[href^="#"]');
  if (link && link.getAttribute('href') === location.hash) {
    event.preventDefault();
    render();
  }
});

mountStaticIcons();
render();
