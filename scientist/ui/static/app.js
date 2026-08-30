'use strict';

let snapshot = null;
let selected = null;
let pendingActivity = false;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function unavailable(value) {
  return value === undefined || value === null || value === ''
    ? 'Unavailable' : String(value);
}

function relativeAge(epochSeconds) {
  if (typeof epochSeconds !== 'number') return '无活动时间';
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - epochSeconds));
  if (seconds < 60) return seconds + ' 秒前';
  if (seconds < 3600) return Math.floor(seconds / 60) + ' 分钟前';
  return Math.floor(seconds / 3600) + ' 小时前';
}

function detailButton(label, detailId) {
  const button = element('button', 'detail-button', label);
  button.type = 'button';
  button.addEventListener('click', () => showDetail(detailId));
  return button;
}

function renderTimeline(events) {
  const root = document.getElementById('timeline');
  const nearBottom = root.scrollHeight - root.scrollTop - root.clientHeight < 48;
  const items = (events || []).map((event) => {
    const row = element('li', 'timeline-event');
    const marker = element('span', 'timeline-marker', '•');
    const body = element('div', 'timeline-body');
    body.append(
      element('span', 'event-kind', unavailable(event.kind)),
      element('p', 'event-summary', unavailable(event.summary)),
    );
    if (event.occurred_at) {
      body.append(element('time', '', String(event.occurred_at)));
    } else {
      body.append(element('span', 'sequence', '序列 ' + event.sequence));
    }
    const details = event.detail_refs || [];
    if (details.length) body.append(detailButton('查看原始记录', details[0]));
    row.append(marker, body);
    return row;
  });
  root.replaceChildren(...items);
  if (nearBottom) {
    root.scrollTop = root.scrollHeight;
    pendingActivity = false;
  } else if (items.length) {
    pendingActivity = true;
  }
  document.getElementById('new-activity').hidden = !pendingActivity;
}

function renderSeatActivities(seat, container) {
  const activities = seat.activities || [];
  if (!activities.length) {
    container.append(element('p', 'muted', '尚无可展示活动'));
    return;
  }
  const list = element('ol', 'activity-list');
  for (const activity of activities) {
    const item = element('li', 'activity');
    item.append(
      element('span', 'activity-status', unavailable(activity.status)),
      element('p', '', unavailable(activity.summary)),
    );
    const details = activity.detail_refs || [];
    if (details.length) item.append(detailButton('展开证据', details[0]));
    list.append(item);
  }
  container.append(list);
}

function renderSeats(seats) {
  const root = document.getElementById('seats');
  const cards = Object.values(seats || {}).map((seat) => {
    const card = element('article', 'seat-card');
    const heading = element('div', 'seat-heading');
    heading.append(
      element('strong', '', unavailable(seat.role)),
      element('span', 'seat-status', unavailable(seat.formal_status)),
    );
    card.append(
      heading,
      element('p', 'seat-id', unavailable(seat.collaborator_id)),
      element('p', 'brief', unavailable(seat.brief)),
    );
    if (seat.formal_status === 'started') {
      card.append(element(
        'p', 'muted', '最近输出：' + relativeAge(seat.last_activity_at)));
      const deadline = Number(seat.started) + Number(seat.box_seconds);
      if (Number.isFinite(deadline) && Date.now() / 1000 > deadline) {
        card.append(element(
          'p', 'warning', '已超过 time box，等待运行时回收'));
      }
    }
    if (seat.delivered) card.append(element('p', 'delivered', '✓ Scientist 已收取'));
    const activityRoot = element('div', 'seat-activities');
    renderSeatActivities(seat, activityRoot);
    card.append(activityRoot);
    return card;
  });
  root.replaceChildren(...cards);
  if (!cards.length) root.append(element('p', 'muted', '尚无协作者'));
}

function renderSnapshot(value) {
  snapshot = value;
  const run = value.run || {};
  const metadata = run.metadata || {};
  document.getElementById('run-name').textContent =
    unavailable(metadata.episode_id || 'Scientist Observatory');
  document.getElementById('run-goal').textContent = unavailable(metadata.goal);
  document.getElementById('run-status').replaceChildren(
    element('span', 'status-pill', unavailable(run.formal_status)),
    element('span', '', '当前：' + unavailable(run.current_activity)),
    element('span', '', value.indexing ? '正在整理历史活动' : '历史索引完成'),
  );
  const usage = value.usage || {};
  const seatValues = Object.values(value.seats || {});
  document.getElementById('run-metrics').replaceChildren(
    element('span', '', 'Attempts ' + (value.attempts || []).length),
    element('span', '', 'Model calls ' + unavailable(usage.calls)),
    element('span', '', 'Tokens ' + unavailable(usage.total_tokens)),
    element('span', '', 'Seats ' + seatValues.length),
  );
  document.getElementById('current-state-text').textContent =
    unavailable(run.current_activity);
  const judgment = run.current_judgment;
  document.getElementById('current-judgment').textContent = judgment
    ? unavailable(judgment.judgment) : '尚无 Current Research Judgment';
  renderTimeline(value.timeline || []);
  renderSeats(value.seats || {});
}

async function loadSnapshot() {
  const response = await fetch('/api/snapshot', {cache: 'no-store'});
  if (!response.ok) throw new Error('snapshot ' + response.status);
  renderSnapshot(await response.json());
}

async function showDetail(detailId) {
  selected = detailId;
  const root = document.getElementById('details');
  root.textContent = '读取中';
  try {
    const response = await fetch(
      '/api/details/' + encodeURIComponent(detailId), {cache: 'no-store'});
    if (!response.ok) throw new Error('detail ' + response.status);
    const detail = await response.json();
    if (selected !== detailId) return;
    root.replaceChildren(
      element('p', 'detail-source', unavailable(detail.source)),
      element('pre', '', typeof detail.content === 'string'
        ? detail.content : JSON.stringify(detail.content, null, 2)),
      detail.truncated ? element('p', 'warning', '内容已按响应上限截断') : element('span'),
    );
  } catch (error) {
    root.textContent = '原始证据不可用：' + error.message;
  }
}

async function applyDelta(delta) {
  if (delta.type === 'snapshot_required') {
    await loadSnapshot();
    return;
  }
  await loadSnapshot();
}

function connectStream() {
  const status = document.getElementById('connection-status');
  const stream = new EventSource('/api/stream');
  stream.onopen = () => { status.textContent = '监视已连接'; };
  stream.onerror = () => { status.textContent = '连接中断，正在重连'; };
  for (const type of [
    'event_added', 'seat_updated', 'run_updated', 'observer_warning',
    'snapshot_required',
  ]) {
    stream.addEventListener(type, (event) => {
      applyDelta({type, data: JSON.parse(event.data)}).catch(() => {
        status.textContent = '更新失败，正在重连';
      });
    });
  }
}

document.getElementById('new-activity').addEventListener('click', () => {
  const root = document.getElementById('timeline');
  root.scrollTop = root.scrollHeight;
  pendingActivity = false;
  document.getElementById('new-activity').hidden = true;
});

loadSnapshot().then(connectStream).catch((error) => {
  document.getElementById('connection-status').textContent =
    '无法载入：' + error.message;
});
