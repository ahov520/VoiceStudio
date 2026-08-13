/**
 * Model Catalogue → Engines (TTS tab) → ElevenLabs TTS panel.
 *
 * Configures WHICH voice/model the `elevenlabs-tts` engine speaks with. The
 * API key itself lives in Settings → Cloud providers (shared with Scribe ASR
 * and the voice isolator). "Load voices" fills a datalist from the account's
 * voice library so users can pick by name instead of pasting a voice_id.
 *
 * Endpoints (loopback-only):
 *   GET  /api/settings/tts-elevenlabs         → {voice_id, model_id, has_key}
 *   PUT  /api/settings/tts-elevenlabs         body {voice_id?, model_id?}
 *   GET  /api/settings/tts-elevenlabs/voices  → {ok, voices: [{voice_id, name, …}]}
 */
import React, { useCallback, useEffect, useState } from 'react';
import { CloudCog } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { apiJson, apiFetch } from '../../api/client';
import { SettingsSection, SettingRow, SettingsInput } from './primitives';
import { Button } from '../../ui';

export default function TtsElevenLabsPanel({ onSaved = null }) {
  const { t } = useTranslation();
  const [voiceId, setVoiceId] = useState('');
  const [modelId, setModelId] = useState('');
  const [hasKey, setHasKey] = useState(true);
  const [voices, setVoices] = useState([]);
  const [loadingVoices, setLoadingVoices] = useState(false);
  const [voicesError, setVoicesError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState(null);
  const [server, setServer] = useState({ voice_id: '', model_id: '' });

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const d = await apiJson('/api/settings/tts-elevenlabs');
      setVoiceId(d?.voice_id || '');
      setModelId(d?.model_id || '');
      setHasKey(Boolean(d?.has_key));
      setServer({ voice_id: d?.voice_id || '', model_id: d?.model_id || '' });
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
      const res = await apiFetch('/api/settings/tts-elevenlabs', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice_id: voiceId, model_id: modelId }),
      });
      const d = await res.json();
      setVoiceId(d.voice_id || '');
      setModelId(d.model_id || '');
      setServer({ voice_id: d.voice_id || '', model_id: d.model_id || '' });
      setSaved(true);
      onSaved?.();
    } catch (e) {
      setError(e?.message || t('cloud.saveError'));
    } finally {
      setSaving(false);
    }
  };

  const loadVoices = async () => {
    setLoadingVoices(true);
    setVoicesError(null);
    try {
      const d = await apiJson('/api/settings/tts-elevenlabs/voices');
      const list = Array.isArray(d?.voices) ? d.voices : [];
      setVoices(list);
      if (!d?.ok || list.length === 0) {
        setVoicesError(t('models.ttsElevenLabsVoicesEmpty'));
      }
    } catch (e) {
      setVoicesError(e?.message || t('models.ttsElevenLabsVoicesEmpty'));
    } finally {
      setLoadingVoices(false);
    }
  };

  const dirty = voiceId !== server.voice_id || modelId !== server.model_id;

  return (
    <SettingsSection
      icon={CloudCog}
      title={t('models.ttsElevenLabsTitle')}
      description={t('models.ttsElevenLabsDescription')}
    >
      {error && (
        <div className="perfpanel__error" role="alert">
          {error}
        </div>
      )}
      {!hasKey && (
        <div className="perfpanel__error" role="alert" data-testid="tts-elevenlabs-key-missing">
          {t('cloud.keyMissing', { provider: 'ElevenLabs' })}
        </div>
      )}

      <SettingRow
        stack
        title={t('cloud.voice')}
        hint={t('models.ttsElevenLabsVoiceHint')}
        control={
          <>
            <SettingsInput
              mono
              type="text"
              value={voiceId}
              onChange={(e) => setVoiceId(e.target.value)}
              placeholder="21m00Tcm4TlvDq8ikWAM"
              list="tts-elevenlabs-voice-options"
              data-testid="tts-elevenlabs-voice-id"
            />
            <datalist id="tts-elevenlabs-voice-options">
              {voices.map((v) => (
                <option key={v.voice_id} value={v.voice_id}>
                  {v.name}
                </option>
              ))}
            </datalist>
            <Button
              variant="subtle"
              size="sm"
              onClick={loadVoices}
              loading={loadingVoices}
              disabled={loadingVoices || !hasKey}
              data-testid="tts-elevenlabs-load-voices"
            >
              {t('models.ttsElevenLabsLoadVoices')}
            </Button>
            {voicesError && !loadingVoices && (
              <span
                className="text-[length:var(--text-xs)] text-[color:var(--chrome-severity-err,#cc241d)]"
                role="status"
              >
                {voicesError}
              </span>
            )}
          </>
        }
      />

      <SettingRow
        stack
        title={t('cloud.model')}
        control={
          <>
            <SettingsInput
              mono
              type="text"
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              placeholder="eleven_multilingual_v2"
              data-testid="tts-elevenlabs-model-id"
            />
            <Button
              variant="subtle"
              size="sm"
              onClick={save}
              loading={saving}
              disabled={saving || !dirty}
              data-testid="tts-elevenlabs-save"
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
