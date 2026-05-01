/**
 * Approval AI Dashboard — Main Application
 * Vanilla JS, no frameworks
 */

const App = (() => {
  // ── State ─────────────────────────────────────────────────
  let state = {
    currentSection: 'approval',
    currentFilter: { preset: '24h' },
    currentEmailId: null,
    currentEmail: null,
    emails: [],
    grouped: {},
    urgentCount: 0,
  };

  // ── Init ──────────────────────────────────────────────────
  async function init() {
    checkAuthFromUrl();
    try {
      const status = await ApiClient.getAuthStatus();
      if (status.authenticated) {
        showApp();
      } else {
        showLogin();
      }
    } catch {
      showLogin();
    }
  }

  function checkAuthFromUrl() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('error')) {
      document.getElementById('loginError').textContent =
        'Authentication failed: ' + params.get('error');
      document.getElementById('loginError').classList.remove('hidden');
    }
    if (params.has('authenticated')) {
      history.replaceState({}, '', '/');
    }
  }

  async function showApp() {
    document.getElementById('loginScreen').classList.add('hidden');
    document.getElementById('app').classList.remove('hidden');
    await loadUser();
    await loadStats();
    await loadApprovalEmails();
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
    } catch (e) {
      showToast('Failed to initiate login: ' + e.message, 'error');
    }
  }

  async function logout() {
    try {
      await ApiClient.logout();
    } catch {}
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
      const stats = await ApiClient.getStats();
      document.getElementById('statPending').textContent = stats.pending ?? '—';
      document.getElementById('statApproved').textContent = stats.approved ?? '—';
      document.getElementById('statRejected').textContent = stats.rejected ?? '—';
    } catch {}
  }

  // ── Navigation ────────────────────────────────────────────
  function showSection(section) {
    state.currentSection = section;

    // Update nav
    document.querySelectorAll('.nav-item').forEach(el => {
      el.classList.toggle('active', el.dataset.section === section);
    });

    // Toggle sections
    document.getElementById('sectionApproval').classList.toggle('hidden', section !== 'approval');
    document.getElementById('sectionOther').classList.toggle('hidden', section !== 'other');
    document.getElementById('filterBar').classList.toggle('hidden', section !== 'approval');

    // Update title
    document.getElementById('pageTitle').textContent =
      section === 'approval' ? 'Approval Emails' : 'Other Emails';
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
    const end = document.getElementById('endDt').value;
    if (!start || !end) { showToast('Please select start and end date/time.', 'error'); return; }
    state.currentFilter = { start_dt: new Date(start).toISOString(), end_dt: new Date(end).toISOString() };
    loadApprovalEmails();
  }

  function applyDuration() {
    const val = parseInt(document.getElementById('durationValue').value, 10);
    const unit = document.getElementById('durationUnit').value;
    if (!val || val < 1) { showToast('Please enter a valid duration.', 'error'); return; }
    state.currentFilter = { duration_value: val, duration_unit: unit };
    loadApprovalEmails();
  }

  // ── Email Loading ─────────────────────────────────────────
  async function loadApprovalEmails() {
    setLoadingState(true);
    try {
      const data = await ApiClient.getApprovalEmails(state.currentFilter);
      state.emails = data.emails;
      state.grouped = data.grouped;
      renderEmailGroups(data.grouped);
      document.getElementById('approvalCount').textContent = data.total;

      // Check for urgent
      state.urgentCount = data.emails.filter(e => e.priority === 'high' && e.status === 'pending').length;
      const urgentBadge = document.getElementById('urgentBadge');
      if (state.urgentCount > 0) {
        urgentBadge.classList.remove('hidden');
        urgentBadge.title = `${state.urgentCount} urgent pending approval(s)`;
      } else {
        urgentBadge.classList.add('hidden');
      }
    } catch (e) {
      showToast('Failed to load emails: ' + e.message, 'error');
      setLoadingState(false);
    }
    await loadStats();
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
      document.getElementById('emptyState').classList.remove('hidden');
      return;
    }

    const groups = [
      { key: 'today', label: 'Today', items: grouped.today || [] },
      { key: 'this_week', label: 'This Week', items: grouped.this_week || [] },
      { key: 'older', label: 'Older', items: grouped.older || [] },
    ];

    for (const group of groups) {
      if (group.items.length === 0) continue;
      const header = document.createElement('div');
      header.className = 'group-header';
      header.textContent = group.label;
      container.appendChild(header);

      group.items.forEach((email, i) => {
        const card = createEmailCard(email, i);
        container.appendChild(card);
      });
    }
  }

  function createEmailCard(email, idx) {
    const card = document.createElement('div');
    card.className = `email-card priority-${email.priority}`;
    card.style.animationDelay = `${idx * 0.04}s`;
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
    state.currentEmailId = emailSummary.id;
    state.currentEmail = emailSummary;

    // Show panel immediately with cached data
    populateDetailPanel(emailSummary);
    document.getElementById('detailPanel').classList.remove('hidden');
    document.body.style.overflow = 'hidden';

    // Load full detail (with HTML body)
    try {
      const detail = await ApiClient.getEmailDetail(emailSummary.id);
      state.currentEmail = { ...emailSummary, ...detail };
      document.getElementById('emailBodyFrame').innerHTML = sanitizeHtml(detail.body || detail.bodyPreview || '');
      renderAttachments(detail.attachments || []);
    } catch {}

    // Start AI summary
    loadAiSummary();
  }

  function populateDetailPanel(email) {
    document.getElementById('detailSubject').textContent = email.subject;
    document.getElementById('detailFrom').textContent = `${email.sender} <${email.senderEmail}>`;
    document.getElementById('detailDate').textContent = formatDate(email.receivedDateTime, true);

    const pb = document.getElementById('detailPriority');
    pb.className = `detail-priority-badge ${email.priority}`;
    pb.textContent = `${priorityEmoji(email.priority)} ${priorityLabel(email.priority)} Priority`;

    const sb = document.getElementById('detailStatus');
    sb.className = `detail-status-badge ${email.status}`;
    sb.textContent = email.status.charAt(0).toUpperCase() + email.status.slice(1);

    document.getElementById('emailBodyFrame').textContent = email.bodyPreview || '';

    // Reset AI
    document.getElementById('aiLoading').classList.remove('hidden');
    document.getElementById('aiContent').classList.add('hidden');
    document.getElementById('actionFeedback').classList.add('hidden');
    document.getElementById('actionFeedback').className = 'action-feedback hidden';
    document.getElementById('actionComment').value = '';

    // Disable actions if already handled
    const actionBar = document.getElementById('actionBar');
    actionBar.style.opacity = email.status !== 'pending' ? '0.5' : '1';
    actionBar.querySelectorAll('button').forEach(b => b.disabled = email.status !== 'pending');
  }

  function renderAttachments(attachments) {
    const section = document.getElementById('attachmentsSection');
    const list = document.getElementById('attachmentsList');

    if (!attachments || attachments.length === 0) {
      section.classList.add('hidden');
      return;
    }
    section.classList.remove('hidden');

    list.innerHTML = attachments.map(att => {
      const icon = attIcon(att.contentType, att.name);
      const size = att.size ? formatBytes(att.size) : '';
      const dlUrl = ApiClient.getAttachmentDownloadUrl(state.currentEmailId, att.id);
      return `
        <div class="attachment-item">
          <span class="att-icon">${icon}</span>
          <div class="att-info">
            <div class="att-name">${escHtml(att.name)}</div>
            <div class="att-meta">${att.contentType || ''} ${size ? '· ' + size : ''}</div>
          </div>
          <div class="att-actions">
            <a href="${dlUrl}" target="_blank" download>
              <button class="btn-att primary">⬇ Download</button>
            </a>
          </div>
        </div>
      `;
    }).join('');
  }

  async function loadAiSummary() {
    if (!state.currentEmail) return;
    const email = state.currentEmail;

    document.getElementById('aiLoading').classList.remove('hidden');
    document.getElementById('aiContent').classList.add('hidden');

    try {
      const summary = await ApiClient.summarizeEmail(
        email.id,
        email.subject,
        email.body || email.bodyPreview || '',
        email.senderEmail || email.sender,
      );

      document.getElementById('aiEmailSummary').textContent = summary.email_summary || '—';

      const docSection = document.getElementById('aiDocSection');
      if (summary.document_summary) {
        document.getElementById('aiDocSummary').textContent = summary.document_summary;
        docSection.classList.remove('hidden');
      } else {
        docSection.classList.add('hidden');
      }


      const pointsList = document.getElementById('aiDecisionPoints');
      const points = summary.key_decision_points || [];
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

  function regenerateSummary() {
    loadAiSummary();
  }

  function closeDetail() {
    document.getElementById('detailPanel').classList.add('hidden');
    document.body.style.overflow = '';
    state.currentEmailId = null;
    state.currentEmail = null;
  }

  // ── Actions ───────────────────────────────────────────────
  async function performAction(action) {
    if (!state.currentEmailId) return;
    const comment = document.getElementById('actionComment').value.trim();

    const feedback = document.getElementById('actionFeedback');
    feedback.className = 'action-feedback';
    feedback.textContent = 'Sending…';
    feedback.classList.remove('hidden');

    try {
      const result = await ApiClient.performAction(state.currentEmailId, action, comment);
      feedback.className = 'action-feedback success';
      feedback.textContent = `✓ ${result.message}`;

      // Disable buttons
      const actionBar = document.getElementById('actionBar');
      actionBar.style.opacity = '0.5';
      actionBar.querySelectorAll('button').forEach(b => b.disabled = true);

      // Update status badge
      const sb = document.getElementById('detailStatus');
      sb.className = `detail-status-badge ${result.status}`;
      sb.textContent = result.status.charAt(0).toUpperCase() + result.status.slice(1);

      // Update in-memory email list
      const idx = state.emails.findIndex(e => e.id === state.currentEmailId);
      if (idx !== -1) state.emails[idx].status = result.status;

      showToast(result.message, 'success');
      await loadStats();

      // Reload email list after short delay
      setTimeout(() => loadApprovalEmails(), 1500);
    } catch (e) {
      feedback.className = 'action-feedback error';
      feedback.textContent = '✗ ' + e.message;
    }
  }

  // ── Refresh ───────────────────────────────────────────────
  async function refresh() {
    if (state.currentSection === 'approval') {
      await loadApprovalEmails();
    } else {
      await loadOtherEmails();
    }
    showToast('Refreshed', 'success');
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
    // Simple sanitizer — remove script/iframe tags
    return html
      .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '')
      .replace(/<iframe[^>]*>[\s\S]*?<\/iframe>/gi, '')
      .replace(/on\w+="[^"]*"/gi, '');
  }

  function formatDate(isoStr, long = false) {
    if (!isoStr) return '—';
    const d = new Date(isoStr);
    if (long) return d.toLocaleString();
    const now = new Date();
    const diff = now - d;
    if (diff < 3600_000) return Math.floor(diff / 60_000) + 'm ago';
    if (diff < 86_400_000) return Math.floor(diff / 3600_000) + 'h ago';
    if (diff < 604_800_000) return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  function formatBytes(bytes) {
    if (!bytes) return '';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
  }

  function priorityLabel(p) {
    return { high: 'High', medium: 'Medium', low: 'Low' }[p] || p;
  }
  function priorityEmoji(p) {
    return { high: '🔴', medium: '🟠', low: '🟢' }[p] || '';
  }

  function attIcon(contentType = '', name = '') {
    const n = name.toLowerCase();
    if (n.endsWith('.pdf') || contentType.includes('pdf')) return '📄';
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

  // Public API
  return {
    login, logout, showSection,
    setPreset, toggleCustomRange, applyCustomRange, applyDuration,
    openEmailDetail, closeDetail, performAction,
    regenerateSummary, refresh,
  };
})();
