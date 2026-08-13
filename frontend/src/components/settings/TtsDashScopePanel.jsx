/**
 * Model Catalogue → Engines (TTS tab) → Alibaba Cloud CosyVoice (DashScope) panel.
 *
 * Configures the model + voice the `dashscope-tts` engine uses. The API key
 * lives in Settings → Cloud providers (shared with DashScope ASR). Model and
 * voice versions must match (cosyvoice-v2 → *_v2 voices, cosyvoice-v3-* →
 * v3 voices) — the hint says so because a mismatch is the #1 support trap.
 *
 * Endpoints (loopback-only):
 *   GET /api/settings/tts-dashscope  → {model, voice, has_key}
 *   PUT /api/settings/tts-dashscope  body {model?, voice?}
 */
import React, { useCallback, useEffect, useState } from 'react';
import { CloudCog } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { apiJson, apiFetch } from '../../api/client';
import { SettingsSection, SettingRow, SettingsInput } from './primitives';
import { Button } from '../../ui';

export default function TtsDashScopePanel({ onSaved = null }) {
  const { t } = useTranslation();
  const [model, setModel] = useState('');
  const [voice, setVoice] = useState('');
  const [hasKey, setHasKey] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState(null);
  const [server, setServer] = useState({ model: '', voice: '' });

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const d = await apiJson('/api/settings/tts-dashscope');
      setModel(d?.model || '');
      setVoice(d?.voice || '');
      setHasKey(Boolean(d?.has_key));
      setServer({ model: d?.model || '', voice: d?.voice || '' });
    } catch (e) {
      setError(e?.message || t('cloud.loadError'));
    }
  }, [t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch('/api/settings/tts-dashscope', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model, voice }),
      });
      const d = await res.json();
      setModel(d.model || '');
      setVoice(d.voice || '');
      setServer({ model: d.model || '', voice: d.voice || '' });
      setSaved(true);
      onSaved?.();
    } catch (e) {
      setError(e?.message || t('cloud.saveError'));
    } finally {
      setSaving(false);
    }
  };

  const dirty = model !== server.model || voice !== server.voice;

  return (
    <SettingsSection
      icon={CloudCog}
      title={t('models.ttsDashScopeTitle')}
      description={t('models.ttsDashScopeDescription')}
    >
      {error && (
        <div className="perfpanel__error" role="alert">
          {error}
        </div>
      )}
      {!hasKey && (
        <div className="perfpanel__error" role="alert" data-testid="tts-dashscope-key-missing">
          {t('cloud.keyMissing', { provider: 'DashScope' })}
        </div>
      )}

      <SettingRow
        stack
        title={t('cloud.model')}
        hint={t('models.ttsDashScopeModelHint')}
        control={
          <SettingsInput
            mono
            type="text"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="cosyvoice-v2"
            data-testid="tts-dashscope-model"
          />
        }
      />

      <SettingRow
        stack
        title={t('cloud.voice')}
        hint={t('models.ttsDashScopeVoiceHint')}
        control={
          <>
            <SettingsInput
              mono
              type="text"
              value={voice}
              onChange={(e) => setVoice(e.target.value)}
              placeholder="longxiaochun_v2"
              data-testid="tts-dashscope-voice"
            />
            <Button
              variant="subtle"
              size="sm"
              onClick={save}
              loading={saving}
              disabled={saving || !dirty}
              data-testid="tts-dashscope-save"
            >
              {t('common.save')}
            </Button>
            {saved && !dirty && !saving && (
              <span
                className="text-[length:var(--text-xs)] text-[color:var(--chrome-fg-dim)]"
                role="status"
              >
                {t('common.saved')}
              </span>
            )}
          </>
        }
      />
    </SettingsSection>
  );
}
