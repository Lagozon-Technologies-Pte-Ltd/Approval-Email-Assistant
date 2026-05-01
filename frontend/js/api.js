/**
 * Approval AI Dashboard — API Client
 * Handles all communication with the FastAPI backend
 */

const API_BASE = '';  // Same-origin; change to 'http://localhost:8000' for dev

const ApiClient = {
  async _request(method, path, body = null) {
    const opts = {
      method,
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
    };
    if (body) opts.body = JSON.stringify(body);

    const resp = await fetch(`${API_BASE}${path}`, opts);
    if (resp.status === 401) {
      // Force back to login
      document.getElementById('app').classList.add('hidden');
      document.getElementById('loginScreen').classList.remove('hidden');
      throw new Error('Session expired');
    }
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || `Request failed: ${resp.status}`);
    }
    if (resp.status === 204) return null;
    return resp.json();
  },

  // ── Auth ──────────────────────────────────────────────────
  getLoginUrl() {
    return this._request('GET', '/api/auth/login');
  },
  getMe() {
    return this._request('GET', '/api/auth/me');
  },
  getAuthStatus() {
    return this._request('GET', '/api/auth/status');
  },
  logout() {
    return this._request('POST', '/api/auth/logout');
  },

  // ── Emails ────────────────────────────────────────────────
  getApprovalEmails(params = {}) {
    const qs = new URLSearchParams();
    if (params.preset) qs.set('preset', params.preset);
    if (params.start_dt) qs.set('start_dt', params.start_dt);
    if (params.end_dt) qs.set('end_dt', params.end_dt);
    if (params.duration_value) qs.set('duration_value', params.duration_value);
    if (params.duration_unit) qs.set('duration_unit', params.duration_unit);
    return this._request('GET', `/api/emails/approval?${qs}`);
  },
  getOtherEmails(params = {}) {
    const qs = new URLSearchParams();
    if (params.preset) qs.set('preset', params.preset);
    return this._request('GET', `/api/emails/other?${qs}`);
  },
  getEmailDetail(emailId) {
    return this._request('GET', `/api/emails/${emailId}`);
  },
  getAttachmentDownloadUrl(emailId, attachmentId) {
    return `${API_BASE}/api/emails/${emailId}/attachments/${attachmentId}/download`;
  },

  // ── AI Summary ────────────────────────────────────────────
  summarizeEmail(emailId, subject, body, sender) {
    return this._request('POST', '/api/summary/email', {
      email_id: emailId,
      email_subject: subject,
      email_body: body,
      email_sender: sender,
    });
  },

  // ── Actions ───────────────────────────────────────────────
  performAction(emailId, action, comment = '') {
    return this._request('POST', '/api/actions/', {
      email_id: emailId,
      action,
      comment,
    });
  },
  getStats() {
    return this._request('GET', '/api/actions/stats');
  },
};
