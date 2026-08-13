/**
 * Model Catalogue → Engines (TTS tab) → OpenAI-compatible remote TTS panel.
 *
 * The synthesis twin of AsrOpenAICompatPanel (#877): points the
 * `openai-compat-tts` engine at any /v1/audio/speech server — SiliconFlow
 * (CosyVoice2 and friends), OpenAI's own TTS API, or a self-hosted box.
 * Configure and test here, then activate with the "Use" button on the
 * engine's row in the TTS matrix above. The engine re-reads this config on
 * every generation — no restart needed after saving.
 *
 * Endpoints (loopback-only):
 *   GET  /api/settings/tts-openai-compat       → {base_url, model, voice, has_key}
 *   PUT  /api/settings/tts-openai-compat       body {base_url?, model?, voice?, api_key?}
 *   POST /api/settings/tts-openai-compat/test  → {ok, status, latency_ms, …}
 */
import React, { useCallback, useEffect, useState } from 'react';
import { AudioLines, Plug } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { apiJson, apiFetch, apiPost } from '../../api/client';
import { SettingsSection, SettingRow, SettingsInput } from './primitives';
import { Button } from '../../ui';

export default function TtsOpenAICompatPanel({ onSaved = null }) {
  const { t } = useTranslation();
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [voice, setVoice] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [hasKey, setHasKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [server, setServer] = useState({ base_url: '', model: '', voice: '' });

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const d = await apiJson('/api/settings/tts-openai-compat');
      setBaseUrl(d?.base_url || '');
      setModel(d?.model || '');
      setVoice(d?.voice || '');
      setHasKey(Boolean(d?.has_key));
      setApiKey('');
      setServer({ base_url: d?.base_url || '', model: d?.model || '', voice: d?.voice || '' });
    } catch (e) {
      setError(e?.message || t('cloud.loadError'));
    }
  }, [t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const edit = (setter) => (e) => {
    setter(e.target.value);
    setTestResult(null);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch('/api/settings/tts-openai-compat', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_url: baseUrl,
          model,
          voice,
          // Only send api_key when the user typed something — an untouched
          // field must leave the stored key unchanged, not clear it.
          ...(apiKey ? { api_key: apiKey } : {}),
        }),
      });
      const d = await res.json();
      setBaseUrl(d.base_url || '');
      setModel(d.model || '');
      setVoice(d.voice || '');
      setHasKey(Boolean(d.has_key));
      setApiKey('');
      setServer({ base_url: d.base_url || '', model: d.model || '', voice: d.voice || '' });
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

  const dirty =
    baseUrl !== server.base_url || model !== server.model || voice !== server.voice || apiKey !== '';

  const testConnection = async () => {
    setTesting(true);
    setTestResult(null);
    setError(null);
    try {
      if (dirty && !(await save())) return;
      const res = await apiPost('/api/settings/tts-openai-compat/test');
      setTestResult(res);
    } catch (e) {
      setTestResult({ ok: false, status: 'request_failed', detail: e?.message });
    } finally {
      setTesting(false);
    }
  };

  // Generic connectivity strings are shared with the ASR panel; only the
  // synthesis-specific wordings get their own keys.
  const testMessage = (r) => {
    const ms = Math.round(r?.latency_ms || 0);
    switch (r?.status) {
      case 'ok':
        if (r.model_found === true)
          return t('models.asrOpenAICompatTestOkModelListed', { ms, model: server.model });
        if (r.model_found === false)
          return t('models.asrOpenAICompatTestOkModelMissing', { ms, model: server.model });
        return t('models.asrOpenAICompatTestOk', { ms });
      case 'ok_no_models':
        return t('models.ttsOpenAICompatTestOkNoModels', { ms });
      case 'auth_failed':
        return t('models.asrOpenAICompatTestAuthFailed', { code: r.http_status });
      case 'http_error':
        return t('models.asrOpenAICompatTestHttpError', { code: r.http_status });
      case 'timeout':
        return t('models.asrOpenAICompatTestTimeout');
      case 'unreachable':
        return t('models.asrOpenAICompatTestUnreachable');
      case 'not_configured':
        return t('models.asrOpenAICompatTestNotConfigured');
      case 'invalid_url':
        return t('models.asrOpenAICompatTestInvalidUrl');
      default:
        return r?.detail || t('models.asrOpenAICompatTestFailed');
    }
  };

  return (
    <SettingsSection
      icon={AudioLines}
      title={t('models.ttsOpenAICompatTitle')}
      description={t('models.ttsOpenAICompatDescription')}
    >
      {error && (
        <div className="perfpanel__error" role="alert">
          {error}
        </div>
      )}

      <SettingRow
        stack
        title={t('cloud.serverUrl')}
        hint={t('models.ttsOpenAICompatBaseUrlHint')}
        control={
          <SettingsInput
            mono
            type="text"
            value={baseUrl}
            onChange={edit(setBaseUrl)}
            placeholder="https://api.siliconflow.cn/v1"
            data-testid="tts-openai-compat-base-url"
          />
        }
      />

      <SettingRow
        stack
        title={t('cloud.model')}
        control={
          <SettingsInput
            mono
            type="text"
            value={model}
            onChange={edit(setModel)}
            placeholder="tts-1"
            data-testid="tts-openai-compat-model"
          />
        }
      />

      <SettingRow
        stack
        title={t('cloud.voice')}
        hint={t('models.ttsOpenAICompatVoiceHint')}
        control={
          <SettingsInput
            mono
            type="text"
            value={voice}
            onChange={edit(setVoice)}
            placeholder="alloy"
            data-testid="tts-openai-compat-voice"
          />
        }
      />

      <SettingRow
        stack
        title={t('cloud.apiKey')}
        hint={
          hasKey ? t('models.asrOpenAICompatKeyConfigured') : t('models.asrOpenAICompatApiKeyHint')
        }
        control={
          <>
            <SettingsInput
              mono
              type="password"
              value={apiKey}
              onChange={edit(setApiKey)}
              placeholder={hasKey ? '••••••••' : t('models.asrOpenAICompatApiKeyOptional')}
              data-testid="tts-openai-compat-api-key"
            />
            <Button
              variant="subtle"
              size="sm"
              onClick={save}
              loading={saving}
              disabled={saving || testing || !dirty}
              data-testid="tts-openai-compat-save"
            >
              {t('common.save')}
            </Button>
            {saved && !dirty && !saving && (
              <span
                className="text-[length:var(--text-xs)] text-[color:var(--chrome-fg-dim)]"
                role="status"
                data-testid="tts-openai-compat-saved"
              >
                {t('common.saved')}
              </span>
            )}
          </>
        }
      />

      <SettingRow
        stack
        title={t('models.asrOpenAICompatTestTitle')}
        hint={t('models.ttsOpenAICompatTestHint')}
        control={
          <>
            <Button
              variant="subtle"
              size="sm"
              onClick={testConnection}
              loading={testing}
              disabled={testing || saving || !baseUrl.trim()}
              leading={!testing && <Plug size={11} />}
              data-testid="tts-openai-compat-test"
            >
              {testing ? t('models.asrOpenAICompatTesting') : t('models.asrOpenAICompatTest')}
            </Button>
            {testResult && !testing && (
              <span
                className={`text-[length:var(--text-xs)] ${
                  testResult.ok
                    ? 'text-[color:var(--chrome-severity-ok,#98971a)]'
                    : 'text-[color:var(--chrome-severity-err,#cc241d)]'
                }`}
                role="status"
                title={testResult.detail || undefined}
                data-testid="tts-openai-compat-test-result"
              >
                {testMessage(testResult)}
              </span>
            )}
          </>
        }
      />
    </SettingsSection>
  );
}
