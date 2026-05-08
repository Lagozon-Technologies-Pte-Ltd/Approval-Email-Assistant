/**
 * Approval AI Dashboard — Main Application v2
 * New features:
 *  - Persistent status (SQLite backend)
 *  - Thread / trail mail panel
 *  - Per-attachment AI summarize button
 *  - Time-accurate dashboard stats
 *  - AI-enhanced HTML comments before sending
 */

const App = (() => {

  // ── State ─────────────────────────────────────────────────
  let state = {
    currentSection: 'approval',
    currentFilter: { preset: '24h' },
    currentQueue: 'pending',
    currentEmailId: null,
    currentEmail: null,
    emails: [],
    grouped: {},
    urgentCount: 0,
    threadVisible: false,
    _autoRefreshInterval: null,
    _lastEmailIds: new Set(),
  };

  // ── Init ──────────────────────────────────────────────────
  async function init() {
    checkAuthFromUrl();
    try {
      const status = await ApiClient.getAuthStatus();
      if (status.authenticated) showApp(); else showLogin();
    } catch { showLogin(); }
  }

  function checkAuthFromUrl() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('error')) {
      document.getElementById('loginError').textContent = 'Authentication failed: ' + params.get('error');
      document.getElementById('loginError').classList.remove('hidden');
    }
    if (params.has('authenticated')) history.replaceState({}, '', '/');
  }

  async function showApp() {
    document.getElementById('loginScreen').classList.add('hidden');
    document.getElementById('app').classList.remove('hidden');
    await loadUser();
    await loadStats();
    await loadApprovalEmails();
    startAutoRefresh();
  }

  function showLogin() {
    document.getElementById('loginScreen').classList.remove('hidden');
    document.getElementById('app').classList.add('hidden');
  }

  // ── Auth ──────────────────────────────────────────────────
  async function login() {
    try {
      const { auth_url } = await ApiClient.getLoginUrl();
      window.location.href = auth_url;
    } catch (e) { showToast('Failed to initiate login: ' + e.message, 'error'); }
  }

  async function logout() {
    stopAutoRefresh();
    try { await ApiClient.logout(); } catch {}
    showLogin();
  }

  async function loadUser() {
    try {
      const user = await ApiClient.getMe();
      document.getElementById('userName').textContent = user.displayName || user.mail;
      document.getElementById('userEmail').textContent = user.mail || '';
      const initial = (user.displayName || user.mail || 'U')[0].toUpperCase();
      document.getElementById('userAvatar').textContent = initial;
    } catch {}
  }

  async function loadStats() {
    try {
      const stats = await ApiClient.getStats(state.currentFilter);
      document.getElementById('statPending').textContent   = stats.pending   ?? '0';
      document.getElementById('statApproved').textContent  = stats.approved  ?? '0';
      document.getElementById('statRejected').textContent  = stats.rejected  ?? '0';
      const needsEl = document.getElementById('statNeedsInfo');
      if (needsEl) needsEl.textContent = stats.needs_info ?? '0';

      // Update sidebar approval badge to show total pending count
      document.getElementById('approvalCount').textContent = stats.pending ?? '0';
    } catch (err) {
      console.error('[loadStats] Failed to load stats:', err);
      ['statPending','statApproved','statRejected','statNeedsInfo'].forEach(id => {
        const el = document.getElementById(id);
        if (el && el.textContent === '—') el.textContent = '0';
      });
    }
  }

  // ── Navigation ────────────────────────────────────────────
  function showSection(section) {
    state.currentSection = section;
    document.querySelectorAll('.nav-item').forEach(el =>
      el.classList.toggle('active', el.dataset.section === section));
    document.getElementById('sectionApproval').classList.toggle('hidden', section !== 'approval');
    document.getElementById('sectionOther').classList.toggle('hidden', section !== 'other');
    document.getElementById('filterBar').classList.toggle('hidden', section !== 'approval');
    const queueLabels = { pending: 'Pending Approvals', approved: 'Approved', rejected: 'Rejected', needs_info: 'Needs Info' };
    document.getElementById('pageTitle').textContent = section === 'approval'
      ? (queueLabels[state.currentQueue] || 'Approval Emails')
      : 'Other Emails';
    document.getElementById('breadcrumb').textContent = '';
    if (section === 'other') loadOtherEmails();
    if (section === 'approval') loadApprovalEmails();
  }

  // ── Time Filters ──────────────────────────────────────────
  function setPreset(btn, preset) {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('customRangePanel').classList.add('hidden');
    if (preset === 'custom') return;
    state.currentFilter = { preset };
    loadApprovalEmails();
  }

  function toggleCustomRange(btn) {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('customRangePanel').classList.toggle('hidden');
  }

  function applyCustomRange() {
    const start = document.getElementById('startDt').value;
    const end   = document.getElementById('endDt').value;
    if (!start || !end) { showToast('Please select start and end date/time.', 'error'); return; }
    state.currentFilter = { start_dt: new Date(start).toISOString(), end_dt: new Date(end).toISOString() };
    loadApprovalEmails();
  }

  function applyDuration() {
    const val  = parseInt(document.getElementById('durationValue').value, 10);
    const unit = document.getElementById('durationUnit').value;
    if (!val || val < 1) { showToast('Please enter a valid duration.', 'error'); return; }
    state.currentFilter = { duration_value: val, duration_unit: unit };
    loadApprovalEmails();
  }

  // ── Email Loading ─────────────────────────────────────────
  async function loadApprovalEmails(silent = false) {
    if (!silent) setLoadingState(true);
    try {
      const params = { ...state.currentFilter, queue: state.currentQueue };
      const data = await ApiClient.getApprovalEmails(params);

      // Detect new pending emails for toast notification
      if (state.currentQueue === 'pending' && state._lastEmailIds.size > 0) {
        const newIds = data.emails.filter(e => !state._lastEmailIds.has(e.id));
        if (newIds.length > 0) {
          showToast(`🔔 ${newIds.length} new approval request${newIds.length > 1 ? 's' : ''} received`, 'info');
          // Briefly highlight new cards after render
          setTimeout(() => {
            newIds.forEach(e => {
              const card = document.querySelector(`[data-email-id="${e.id}"]`);
              if (card) {
                card.classList.add('email-card-new');
                setTimeout(() => card.classList.remove('email-card-new'), 3000);
              }
            });
          }, 200);
        }
      }
      state._lastEmailIds = new Set(data.emails.map(e => e.id));

      state.emails  = data.emails;
      state.grouped = data.grouped;

      if (!silent || !state.currentEmailId) {
        renderEmailGroups(data.grouped);
      } else {
        renderEmailGroupsSilent(data.grouped);
      }

      document.getElementById('approvalCount').textContent = data.total;

      state.urgentCount = data.emails.filter(e => e.priority === 'high' && e.status === 'pending').length;
      const urgentBadge = document.getElementById('urgentBadge');
      if (state.urgentCount > 0) {
        urgentBadge.classList.remove('hidden');
        urgentBadge.title = `${state.urgentCount} urgent pending approval(s)`;
      } else {
        urgentBadge.classList.add('hidden');
      }
    } catch (e) {
      if (!silent) showToast('Failed to load emails: ' + e.message, 'error');
      setLoadingState(false);
    }
    await loadStats();
  }

  // Silent render: update existing cards in-place without resetting scroll
  function renderEmailGroupsSilent(grouped) {
    const container = document.getElementById('emailGroups');
    // Only full re-render if count changed significantly (avoids scroll jump for minor updates)
    const total = (grouped.today?.length || 0) + (grouped.this_week?.length || 0) + (grouped.older?.length || 0);
    const currentCards = container.querySelectorAll('.email-card').length;
    if (Math.abs(total - currentCards) > 0) {
      renderEmailGroups(grouped);
    }
  }
  async function loadOtherEmails() {
    const container = document.getElementById('otherEmailList');
    container.innerHTML = '<div class="loading-state"><div class="spinner"></div><p>Fetching emails…</p></div>';
    try {
      const data = await ApiClient.getOtherEmails({ preset: '1w' });
      document.getElementById('otherCount').textContent = data.total;
      if (data.emails.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="empty-icon">📭</div><h3>No other emails</h3><p>Nothing to show in this period.</p></div>';
        return;
      }
      container.innerHTML = data.emails.map(email => `
        <div class="digest-card">
          <div class="digest-card-top">
            <span class="digest-sender">${escHtml(email.sender)}</span>
            <span class="digest-date">${formatDate(email.receivedDateTime)}</span>
          </div>
          <div class="digest-subject">${escHtml(email.subject)}</div>
          <div class="digest-preview">${escHtml(email.bodyPreview)}</div>
        </div>
      `).join('');
    } catch (e) {
      container.innerHTML = `<p style="color:red;padding:20px">${e.message}</p>`;
    }
  }

  // ── Rendering ──────────────────────────────────────────────
  function setLoadingState(loading) {
    document.getElementById('loadingState').classList.toggle('hidden', !loading);
    document.getElementById('emptyState').classList.add('hidden');
    document.getElementById('emailGroups').innerHTML = '';
  }

  function renderEmailGroups(grouped) {
    document.getElementById('loadingState').classList.add('hidden');
    const container = document.getElementById('emailGroups');
    container.innerHTML = '';

    const total = (grouped.today?.length || 0) + (grouped.this_week?.length || 0) + (grouped.older?.length || 0);
    if (total === 0) {
      const labels = { pending: 'pending approvals', approved: 'approved emails', rejected: 'rejected emails', needs_info: 'emails needing info' };
      const msgEl = document.getElementById('emptyStateMsg');
      if (msgEl) msgEl.textContent = `No ${labels[state.currentQueue] || 'emails'} in the selected time range.`;
      document.getElementById('emptyState').classList.remove('hidden');
      return;
    }

    const groups = [
      { key: 'today',     label: 'Today',     items: grouped.today     || [] },
      { key: 'this_week', label: 'This Week',  items: grouped.this_week || [] },
      { key: 'older',     label: 'Older',      items: grouped.older     || [] },
    ];

    for (const group of groups) {
      if (group.items.length === 0) continue;
      const header = document.createElement('div');
      header.className = 'group-header';
      header.textContent = group.label;
      container.appendChild(header);
      group.items.forEach((email, i) => container.appendChild(createEmailCard(email, i)));
    }
  }

  function createEmailCard(email, idx) {
    const card = document.createElement('div');
    card.className = `email-card priority-${email.priority}`;
    card.style.animationDelay = `${idx * 0.04}s`;
    card.dataset.emailId = email.id;
    card.onclick = () => openEmailDetail(email);

    const initial = (email.sender || '?')[0].toUpperCase();
    const attChips = email.hasAttachments && email.attachments?.length > 0
      ? email.attachments.map(a => `<span class="att-chip">📎 ${escHtml(a.name)}</span>`).join('')
      : email.hasAttachments ? '<span class="att-chip">📎 Attachment</span>' : '';

    card.innerHTML = `
      <div class="email-avatar">${initial}</div>
      <div class="email-card-body">
        <div class="email-card-top">
          <span class="email-sender">${escHtml(email.sender)}</span>
          <div class="email-card-meta">
            <span class="email-date">${formatDate(email.receivedDateTime)}</span>
            <span class="priority-badge ${email.priority}">${priorityLabel(email.priority)}</span>
            <span class="status-badge ${email.status}">${email.status}</span>
          </div>
        </div>
        <div class="email-subject">${escHtml(email.subject)}</div>
        <div class="email-preview">${escHtml(email.bodyPreview)}</div>
        ${attChips ? `<div class="email-card-footer">${attChips}</div>` : ''}
      </div>
    `;
    return card;
  }

  // ── Email Detail ──────────────────────────────────────────
  async function openEmailDetail(emailSummary) {
    state.currentEmailId  = emailSummary.id;
    state.currentEmail    = emailSummary;
    state.threadVisible   = false;

    populateDetailPanel(emailSummary);
    document.getElementById('detailPanel').classList.remove('hidden');
    document.body.style.overflow = 'hidden';

    // Load full detail
    try {
      const detail = await ApiClient.getEmailDetail(emailSummary.id);
      state.currentEmail = { ...emailSummary, ...detail };
      document.getElementById('emailBodyFrame').innerHTML = sanitizeHtml(detail.body || detail.bodyPreview || '');
      renderAttachments(detail.attachments || []);
    } catch {}

    loadAiSummary();
  }

  function populateDetailPanel(email) {
    document.getElementById('detailSubject').textContent = email.subject;
    document.getElementById('detailFrom').textContent   = `${email.sender} <${email.senderEmail}>`;
    document.getElementById('detailDate').textContent   = formatDate(email.receivedDateTime, true);

    const pb = document.getElementById('detailPriority');
    pb.className = `detail-priority-badge ${email.priority}`;
    pb.textContent = `${priorityEmoji(email.priority)} ${priorityLabel(email.priority)} Priority`;

    const sb = document.getElementById('detailStatus');
    sb.className = `detail-status-badge ${email.status}`;
    sb.textContent = email.status.charAt(0).toUpperCase() + email.status.slice(1);

    document.getElementById('emailBodyFrame').textContent = email.bodyPreview || '';

    // Reset UI
    document.getElementById('aiLoading').classList.remove('hidden');
    document.getElementById('aiContent').classList.add('hidden');
    document.getElementById('actionFeedback').classList.add('hidden');
    document.getElementById('actionFeedback').className = 'action-feedback hidden';
    document.getElementById('actionComment').value = '';

    // Thread panel reset
    const threadPanel = document.getElementById('threadPanel');
    if (threadPanel) { threadPanel.innerHTML = ''; threadPanel.classList.add('hidden'); }

    // Disable actions if already handled
    const actionBar = document.getElementById('actionBar');
    const isDone = email.status !== 'pending' && email.status !== 'needs_info';
    actionBar.style.opacity = isDone ? '0.5' : '1';
    actionBar.querySelectorAll('button').forEach(b => b.disabled = isDone);
  }

  // ── Attachments ───────────────────────────────────────────
  function renderAttachments(attachments) {
    const section = document.getElementById('attachmentsSection');
    const list    = document.getElementById('attachmentsList');

    if (!attachments || attachments.length === 0) {
      section.classList.add('hidden');
      return;
    }
    section.classList.remove('hidden');

    list.innerHTML = attachments.map(att => {
      const icon  = attIcon(att.contentType, att.name);
      const size  = att.size ? formatBytes(att.size) : '';
      const dlUrl = ApiClient.getAttachmentDownloadUrl(state.currentEmailId, att.id);
      return `
  <div class="attachment-wrapper">

    <div class="attachment-item" id="att-${att.id}">
      <span class="att-icon">${icon}</span>

      <div class="att-info">
        <div class="att-name">${escHtml(att.name)}</div>
        <div class="att-meta">
          ${att.contentType || ''} ${size ? '· ' + size : ''}
        </div>
      </div>

      <div class="att-actions">
        <button
          class="btn-att"
          onclick="App.summarizeAttachment('${att.id}','${escHtml(att.name)}')"
        >
          🤖 Summarize
        </button>

        <a href="${dlUrl}" target="_blank" download>
          <button class="btn-att primary">
            ⬇ Download
          </button>
        </a>
      </div>
    </div>

    <div class="att-summary hidden" id="att-summary-${att.id}"></div>

  </div>
`;
    }).join('');
  }

  async function summarizeAttachment(attachmentId, attachmentName) {
    if (!state.currentEmailId) return;
    const summaryEl = document.getElementById(`att-summary-${attachmentId}`);
    if (!summaryEl) return;

    summaryEl.classList.remove('hidden');
    summaryEl.innerHTML = '<span class="spinner-inline"></span> Summarizing document…';

    try {
      const result = await ApiClient.summarizeAttachment(
        state.currentEmailId, attachmentId, attachmentName
      );
      summaryEl.innerHTML = `
        <div class="att-summary-box">
          <strong>📄 Document Summary — ${escHtml(attachmentName)}</strong>
          <p>${escHtml(result.summary || result.attachment_summary || 'No summary available.')}</p>
          ${result.key_points?.length
            ? `<ul>${result.key_points.map(p => `<li>${escHtml(p)}</li>`).join('')}</ul>`
            : ''}
        </div>`;
    } catch (e) {
      summaryEl.innerHTML = `<span style="color:var(--red)">Failed: ${e.message}</span>`;
    }
  }

  // ── Thread Trail ──────────────────────────────────────────
  async function toggleThread() {
    const threadPanel = document.getElementById('threadPanel');
    if (!threadPanel) return;

    state.threadVisible = !state.threadVisible;
    const btn = document.getElementById('btnThread');
    if (!state.threadVisible) {
      threadPanel.classList.add('hidden');
      if (btn) btn.textContent = '📧 View Thread';
      return;
    }

    threadPanel.classList.remove('hidden');
    if (btn) btn.textContent = '📧 Hide Thread';
    threadPanel.innerHTML = '<div class="thread-loading"><span class="spinner-inline"></span> Loading thread…</div>';

    try {
      const data = await ApiClient.getEmailThread(state.currentEmailId);
      renderThread(data.thread, threadPanel);
    } catch (e) {
      threadPanel.innerHTML = `<p style="color:var(--red);padding:12px">Failed to load thread: ${e.message}</p>`;
    }
  }

  function renderThread(thread, container) {
    if (!thread || thread.length === 0) {
      container.innerHTML = '<p style="padding:12px;color:var(--muted)">No thread history available.</p>';
      return;
    }

    const ACTION_COLORS = {
      approve:      '#16a34a',
      reject:       '#dc2626',
      request_info: '#d97706',
    };
    const ACTION_LABELS = {
      approve:      'Approved',
      reject:       'Rejected',
      request_info: 'Requested Info',
    };

    // Role → CSS class + label
    const ROLE_CLASS = {
      original:       'thread-original',
      our_reply:      'thread-ours',
      their_response: 'thread-their-response',  // ← sender replied back to us
    };
    const ROLE_BADGE = {
      original:       { text: 'Original Request', color: '#92400e', bg: '#fef3c7', border: '#fde68a' },
      their_response: { text: '💬 Their Reply', color: '#1d4ed8', bg: '#eff6ff', border: '#bfdbfe' },
    };

    container.innerHTML = `
      <div class="thread-header">
        <h3>📧 Conversation Thread (${thread.length} message${thread.length !== 1 ? 's' : ''})</h3>
        <p class="thread-subhead">Full history: original request → your replies → their responses</p>
      </div>
      <div class="thread-list">
        ${thread.map((msg) => {
          const role        = msg.msg_role || (msg.is_our_reply ? 'our_reply' : msg.is_original ? 'original' : 'their_response');
          const isOurs      = role === 'our_reply';
          const isOriginal  = role === 'original';
          const isTheirResp = role === 'their_response';

          const actionColor = msg.action_type ? ACTION_COLORS[msg.action_type] : null;
          const actionLabel = msg.action_type ? ACTION_LABELS[msg.action_type] : null;
          const roleBadge   = ROLE_BADGE[role];
          const roleClass   = ROLE_CLASS[role] || 'thread-theirs';

          const avatarLetter = (msg.sender || '?')[0].toUpperCase();
          const avatarStyle  = isOurs      ? 'background:#16a34a'
                             : isTheirResp ? 'background:#2563eb'
                             : 'background:var(--accent)';

          return `
            <div class="thread-message ${roleClass}">
              ${isTheirResp ? '<div class="thread-response-indicator">↩ They responded to your request</div>' : ''}
              <div class="thread-msg-header">
                <div class="thread-msg-avatar" style="${avatarStyle}">${avatarLetter}</div>
                <div class="thread-msg-meta">
                  <span class="thread-msg-sender">${escHtml(msg.sender)}</span>
                  <span class="thread-msg-time">${formatDate(msg.received_at, true)}</span>
                </div>
                ${roleBadge
                  ? `<span class="thread-role-badge" style="background:${roleBadge.bg};color:${roleBadge.color};border:1px solid ${roleBadge.border}">${roleBadge.text}</span>`
                  : ''}
                ${actionLabel
                  ? `<span class="thread-action-badge" style="background:${actionColor}20;color:${actionColor};border:1px solid ${actionColor}40">${actionLabel}</span>`
                  : ''}
              </div>
              <div class="thread-msg-subject">${escHtml(msg.subject)}</div>
              <div class="thread-msg-body ${isTheirResp ? 'thread-response-body' : ''}">${escHtml(msg.body_preview)}</div>
              ${isOurs && msg.enhanced_html
                ? `<div class="thread-enhanced-toggle">
                     <button class="btn-att" onclick="this.nextElementSibling.classList.toggle('hidden')">
                       🖊 View Sent HTML
                     </button>
                     <div class="thread-enhanced-html hidden">${msg.enhanced_html}</div>
                   </div>`
                : ''}
            </div>
          `;
        }).join('')}
      </div>
    `;
  }

  // ── AI Summary ────────────────────────────────────────────
  async function loadAiSummary() {
    if (!state.currentEmail) return;
    const email = state.currentEmail;

    document.getElementById('aiLoading').classList.remove('hidden');
    document.getElementById('aiContent').classList.add('hidden');

    try {
      const summary = await ApiClient.summarizeEmail(
        email.id, email.subject,
        email.body || email.bodyPreview || '',
        email.senderEmail || email.sender,
      );

      const summaryText = summary.email_summary || '';

      const formatted = summaryText
        .split(/\. |\n|•/)
        .filter(s => s.trim().length > 0)
        .map(item => `<li>${escHtml(item.trim())}</li>`)
        .join('');

      document.getElementById('aiEmailSummary').innerHTML = `
        <ul class="smart-summary-list">
          ${formatted}
        </ul>
      `;

      const docSection = document.getElementById('aiDocSection');
      if (summary.document_summary) {
        document.getElementById('aiDocSummary').textContent = summary.document_summary;
        docSection.classList.remove('hidden');
      } else {
        docSection.classList.add('hidden');
      }

      const points = summary.key_decision_points || [];
      const pointsList = document.getElementById('aiDecisionPoints');
      if (points.length > 0) {
        pointsList.innerHTML = points.map(p => `<li>${escHtml(p)}</li>`).join('');
        document.getElementById('aiDecisionSection').classList.remove('hidden');
      } else {
        document.getElementById('aiDecisionSection').classList.add('hidden');
      }

      const actionVal = document.getElementById('aiSuggestionVal');
      actionVal.textContent = summary.suggested_action || '—';
      actionVal.className = 'ai-suggestion-val ' + (summary.suggested_action || '').toLowerCase();
      document.getElementById('aiSuggestionReason').textContent = summary.suggested_action_reason || '';
      document.getElementById('aiSmartReplyText').textContent = summary.smart_reply || '—';

      document.getElementById('aiLoading').classList.add('hidden');
      document.getElementById('aiContent').classList.remove('hidden');
    } catch (e) {
      document.getElementById('aiLoading').innerHTML = `<span style="color:var(--red)">AI analysis failed: ${e.message}</span>`;
    }
  }

  function regenerateSummary() { loadAiSummary(); }

  function closeDetail() {
    document.getElementById('detailPanel').classList.add('hidden');
    document.body.style.overflow = '';
    state.currentEmailId = null;
    state.currentEmail   = null;
    state.threadVisible  = false;
  }

  // ── Actions ───────────────────────────────────────────────
  async function performAction(action) {
    if (!state.currentEmailId) return;
    const comment  = document.getElementById('actionComment').value.trim();
    const email    = state.currentEmail || {};

    const feedback = document.getElementById('actionFeedback');
    feedback.className   = 'action-feedback';
    feedback.innerHTML   = '⏳ Enhancing with AI and sending…';
    feedback.classList.remove('hidden');

    try {
      const result = await ApiClient.performAction(
        state.currentEmailId, action, comment,
        {
          subject:        email.subject        || '',
          sender:         email.senderEmail    || email.sender || '',
          bodyPreview:    email.bodyPreview    || '',
          conversationId: email.conversationId || '',
          receivedAt:     email.receivedDateTime || '',
        }
      );

      feedback.className = 'action-feedback success';
      feedback.innerHTML = `✓ ${result.message}`;

      // Show preview of what was sent
      if (result.enhanced_html) {
        const previewDiv = document.createElement('div');
        previewDiv.className = 'sent-html-preview';
        previewDiv.innerHTML = `
          <div class="sent-preview-toggle">
            <button class="btn-att" onclick="this.nextElementSibling.classList.toggle('hidden')">
              🖊 View AI-Enhanced Reply
            </button>
            <div class="sent-preview-body hidden">${result.enhanced_html}</div>
          </div>`;
        feedback.appendChild(previewDiv);
      }

      // Disable buttons
      const actionBar = document.getElementById('actionBar');
      actionBar.style.opacity = '0.5';
      actionBar.querySelectorAll('button').forEach(b => b.disabled = true);

      // Update status badge
      const sb = document.getElementById('detailStatus');
      sb.className = `detail-status-badge ${result.status}`;
      sb.textContent = result.status.charAt(0).toUpperCase() + result.status.slice(1);

      // Update in-memory list
      const idx = state.emails.findIndex(e => e.id === state.currentEmailId);
      if (idx !== -1) state.emails[idx].status = result.status;

      showToast(result.message, 'success');
      await loadStats();
      setTimeout(() => loadApprovalEmails(), 1500);
    } catch (e) {
      feedback.className = 'action-feedback error';
      feedback.textContent = '✗ ' + e.message;
    }
  }

  // ── Refresh ───────────────────────────────────────────────
  async function refresh() {
    if (state.currentSection === 'approval') await loadApprovalEmails();
    else await loadOtherEmails();
    showToast('Refreshed', 'success');
  }

  // ── Queue Management ──────────────────────────────────────
  function setQueue(queue) {
    state.currentQueue = queue;
    // Update active state on stat cards
    document.querySelectorAll('.stat-card').forEach(c => c.classList.remove('stat-card-active'));
    const map = { pending: 'statCardPending', approved: 'statCardApproved', rejected: 'statCardRejected', needs_info: 'statCardNeedsInfo' };
    const activeCard = document.getElementById(map[queue]);
    if (activeCard) activeCard.classList.add('stat-card-active');
    // Update page title
    const labels = { pending: 'Pending Approvals', approved: 'Approved', rejected: 'Rejected', needs_info: 'Needs Info' };
    document.getElementById('pageTitle').textContent = labels[queue] || 'Approval Emails';
    loadApprovalEmails();
  }

  // ── Auto Refresh ──────────────────────────────────────────
  function startAutoRefresh() {
    stopAutoRefresh(); // prevent duplicates
    state._autoRefreshInterval = setInterval(async () => {
      if (state.currentSection === 'approval') {
        await loadApprovalEmails(true);  // silent=true
      }
    }, 30000);
  }

  function stopAutoRefresh() {
    if (state._autoRefreshInterval) {
      clearInterval(state._autoRefreshInterval);
      state._autoRefreshInterval = null;
    }
  }

  // ── Utilities ──────────────────────────────────────────────
  function escHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function sanitizeHtml(html) {
    return html
      .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '')
      .replace(/<iframe[^>]*>[\s\S]*?<\/iframe>/gi, '')
      .replace(/on\w+="[^"]*"/gi, '');
  }

  function formatDate(isoStr, long = false) {
    if (!isoStr) return '—';
    const d = new Date(isoStr);
    if (long) return d.toLocaleString();
    const now  = new Date();
    const diff = now - d;
    if (diff < 3_600_000)    return Math.floor(diff / 60_000) + 'm ago';
    if (diff < 86_400_000)   return Math.floor(diff / 3_600_000) + 'h ago';
    if (diff < 604_800_000)  return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  function formatBytes(bytes) {
    if (!bytes) return '';
    if (bytes < 1024)    return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
  }

  function priorityLabel(p) { return { high: 'High', medium: 'Medium', low: 'Low' }[p] || p; }
  function priorityEmoji(p) { return { high: '🔴', medium: '🟠', low: '🟢' }[p] || ''; }

  function attIcon(contentType = '', name = '') {
    const n = name.toLowerCase();
    if (n.endsWith('.pdf') || contentType.includes('pdf'))  return '📄';
    if (n.endsWith('.docx') || n.endsWith('.doc') || contentType.includes('word')) return '📝';
    if (n.endsWith('.xlsx') || n.endsWith('.xls') || contentType.includes('excel')) return '📊';
    if (n.endsWith('.txt')) return '🗒';
    if (contentType.includes('image')) return '🖼';
    return '📎';
  }

  function showToast(msg, type = '') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = msg;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3500);
  }

  // ── Bootstrap ─────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', init);

  return {
    login, logout, showSection,
    setPreset, toggleCustomRange, applyCustomRange, applyDuration,
    openEmailDetail, closeDetail,
    performAction, regenerateSummary, refresh,
    summarizeAttachment, toggleThread,
    setQueue,
  };
})();
