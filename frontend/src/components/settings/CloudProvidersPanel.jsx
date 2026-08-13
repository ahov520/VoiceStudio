/**
 * Settings → Cloud providers — one API key per cloud speech provider.
 *
 * ElevenLabs (TTS / Scribe ASR / voice isolation), Alibaba Cloud Model Studio
 * (DashScope: CosyVoice TTS / Qwen ASR) and MVSEP (vocal separation) each get
 * a single key here, shared by every engine of that provider. Keys persist
 * encrypted server-side and are never echoed back; env vars
 * (ELEVENLABS_API_KEY / DASHSCOPE_API_KEY / MVSEP_API_TOKEN) win over saved
 * keys, and the row says so when one is active.
 *
 * Endpoints (loopback-only):
 *   GET  /api/settings/cloud-providers                → {providers: [...]}
 *   PUT  /api/settings/cloud-providers/{id}           body {api_key}
 *   POST /api/settings/cloud-providers/{id}/test      → {ok, status, latency_ms, …}
 *        Test saves first (same contract as the LLM providers panel).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Cloud, Plug } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { apiJson, apiFetch, apiPost } from '../../api/client';
import { SettingsSection, SettingRow, SettingsInput } from './primitives';
import { Button } from '../../ui';

const NOTES_KEY = {
  elevenlabs: 'cloud.elevenlabsNotes',
  dashscope: 'cloud.dashscopeNotes',
  mvsep: 'cloud.mvsepNotes',
};

function ProviderRow({ provider, onSaved }) {
  const { t } = useTranslation();
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [error, setError] = useState(null);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await apiFetch(`/api/settings/cloud-providers/${provider.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: apiKey }),
      });
      setApiKey('');
      setSaved(true);
      onSaved?.();
      return true;
    } catch (e) {
      setError(e?.message || t('cloud.saveError'));
      return false;
    } finally {
      setSaving(false);
    }
  };

  const testKey = async () => {
    setTesting(true);
    setTestResult(null);
    setError(null);
    try {
      // Save first so the probe sees the just-typed key (LLM-panel contract).
      if (apiKey && !(await save())) return;
      const res = await apiPost(`/api/settings/cloud-providers/${provider.id}/test`);
      setTestResult(res);
    } catch (e) {
      setTestResult({ ok: false, status: 'request_failed', detail: e?.message });
    } finally {
      setTesting(false);
    }
  };

  const testMessage = (r) => {
    const ms = Math.round(r?.latency_ms || 0);
    switch (r?.status) {
      case 'ok':
        return t('cloud.testOk', { ms });
      case 'auth_failed':
        return t('cloud.testAuthFailed', { code: r.http_status });
      case 'not_configured':
        return t('cloud.testNotConfigured');
      case 'timeout':
        return t('cloud.testTimeout');
      case 'unreachable':
        return t('cloud.testUnreachable');
      default:
        return r?.detail || t('cloud.testFailed');
    }
  };

  const keyHint = provider.key_from_env
    ? t('cloud.keyFromEnv')
    : provider.has_key
      ? t('models.asrOpenAICompatKeyConfigured')
      : t(NOTES_KEY[provider.id] || 'cloud.description');

  return (
    <SettingRow
      stack
      title={provider.display_name}
      hint={keyHint}
      control={
        <>
          <SettingsInput
            mono
            type="password"
            value={apiKey}
            onChange={(e) => {
              setApiKey(e.target.value);
              setTestResult(null);
              setSaved(false);
            }}
            placeholder={provider.has_key ? '••••••••' : t('cloud.apiKey')}
            disabled={provider.key_from_env}
            data-testid={`cloud-key-${provider.id}`}
          />
          <Button
            variant="subtle"
            size="sm"
            onClick={save}
            loading={saving}
            disabled={saving || testing || !apiKey}
            data-testid={`cloud-save-${provider.id}`}
          >
            {t('common.save')}
          </Button>
          <Button
            variant="subtle"
            size="sm"
            onClick={testKey}
            loading={testing}
            disabled={testing || saving || (!provider.has_key && !provider.key_from_env && !apiKey)}
            leading={!testing && <Plug size={11} />}
            data-testid={`cloud-test-${provider.id}`}
          >
            {testing ? t('models.asrOpenAICompatTesting') : t('cloud.testKey')}
          </Button>
          {saved && !saving && !testResult && (
            <span
              className="text-[length:var(--text-xs)] text-[color:var(--chrome-fg-dim)]"
              role="status"
            >
              {t('common.saved')}
            </span>
          )}
          {testResult && !testing && (
            <span
              className={`text-[length:var(--text-xs)] ${
                testResult.ok
                  ? 'text-[color:var(--chrome-severity-ok,#98971a)]'
                  : 'text-[color:var(--chrome-severity-err,#cc241d)]'
              }`}
              role="status"
              title={testResult.detail || undefined}
              data-testid={`cloud-test-result-${provider.id}`}
            >
              {testMessage(testResult)}
            </span>
          )}
          {provider.signup_url && (
            <a
              href={provider.signup_url}
              target="_blank"
              rel="noreferrer"
              className="text-[length:var(--text-xs)] text-[color:var(--chrome-fg-dim)] underline"
            >
              {t('cloud.getKey')}
            </a>
          )}
          {error && (
            <span
              className="text-[length:var(--text-xs)] text-[color:var(--chrome-severity-err,#cc241d)]"
              role="alert"
            >
              {error}
            </span>
          )}
        </>
      }
    />
  );
}

export default function CloudProvidersPanel() {
  const { t } = useTranslation();
  const [providers, setProviders] = useState([]);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const d = await apiJson('/api/settings/cloud-providers');
      setProviders(Array.isArray(d?.providers) ? d.providers : []);
    } catch (e) {
      setError(e?.message || t('cloud.loadError'));
    }
  }, [t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <SettingsSection icon={Cloud} title={t('cloud.title')} description={t('cloud.description')}>
      {error && (
        <div className="perfpanel__error" role="alert">
          {error}
        </div>
      )}
      {providers.map((p) => (
        <ProviderRow key={p.id} provider={p} onSaved={refresh} />
      ))}
    </SettingsSection>
  );
}
