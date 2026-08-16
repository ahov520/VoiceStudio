/**
 * Settings → Vocal separation — which engine splits vocals from background.
 *
 * Local Demucs (default) runs on this machine; MVSEP and ElevenLabs Voice
 * Isolator are cloud engines that upload the audio track and need a key from
 * Settings → Cloud providers. Selecting an engine persists immediately; the
 * dub prep pipeline and mic cleanup pick it up on their next run. The
 * ElevenLabs isolator returns the voice track only, so the panel warns that
 * dub exports keep no background music with it.
 *
 * Endpoints (loopback-only):
 *   GET /api/settings/separation  → {active, active_from_env, backends, mvsep_sep_type}
 *   PUT /api/settings/separation  body {backend?, mvsep_sep_type?}
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Scissors } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { apiJson, apiFetch } from '../../api/client';
import { SettingsSection, SettingRow, SettingsInput } from './primitives';
import { Button } from '../../ui';

export default function SeparationPanel() {
  const { t } = useTranslation();
  const [state, setState] = useState(null);
  const [sepType, setSepType] = useState('');
  const [savingType, setSavingType] = useState(false);
  const [savedType, setSavedType] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const d = await apiJson('/api/settings/separation');
      setState(d);
      setSepType(d?.mvsep_sep_type || '');
    } catch (e) {
      setError(e?.message || t('cloud.loadError'));
    }
  }, [t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const put = async (body) => {
    const res = await apiFetch('/api/settings/separation', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await res.json();
    setState(d);
    setSepType(d?.mvsep_sep_type || '');
    return d;
  };

  const selectBackend = async (id) => {
    setError(null);
    try {
      await put({ backend: id });
    } catch (e) {
      setError(e?.message || t('cloud.saveError'));
    }
  };

  const saveSepType = async () => {
    setSavingType(true);
    setError(null);
    try {
      await put({ mvsep_sep_type: sepType });
      setSavedType(true);
    } catch (e) {
      setError(e?.message || t('cloud.saveError'));
    } finally {
      setSavingType(false);
    }
  };

  const backends = state?.backends || [];

  return (
    <SettingsSection
      icon={Scissors}
      title={t('separation.title')}
      description={t('separation.description')}
    >
      {error && (
        <div className="perfpanel__error" role="alert">
          {error}
        </div>
      )}
      {state?.active_from_env && (
        <div className="perfpanel__error" role="alert">
          {t('separation.engineFromEnv')}
        </div>
      )}

      <SettingRow
        stack
        title={t('separation.engine')}
        control={
          <div
            className="flex w-full flex-col gap-1.5"
            role="radiogroup"
            aria-label={t('separation.engine')}
          >
            {backends.map((b) => {
              const selected = state?.active === b.id;
              return (
                <label
                  key={b.id}
                  className={`flex cursor-pointer items-start gap-2 rounded border px-2 py-1.5 text-[length:var(--text-sm)] ${
                    selected
                      ? 'border-[color:var(--chrome-accent,#458588)]'
                      : 'border-[color:var(--chrome-border,#3c3836)]'
                  }`}
                  data-testid={`separation-engine-${b.id}`}
                >
                  <input
                    type="radio"
                    name="separation-engine"
                    checked={selected}
                    disabled={Boolean(state?.active_from_env)}
                    onChange={() => selectBackend(b.id)}
                    className="mt-0.5"
                  />
                  <span className="flex flex-col gap-0.5">
                    <span>
                      {b.display_name}{' '}
                      <span className="text-[length:var(--text-xs)] text-[color:var(--chrome-fg-dim)]">
                        [
                        {b.category === 'cloud'
                          ? t('separation.cloudBadge')
                          : t('separation.localBadge')}
                        ]
                      </span>
                    </span>
                    {!b.available && b.reason && (
                      <span className="text-[length:var(--text-xs)] text-[color:var(--chrome-severity-err,#cc241d)]">
                        {b.reason}
                      </span>
                    )}
                    {!b.returns_background && (
                      <span className="text-[length:var(--text-xs)] text-[color:var(--chrome-fg-dim)]">
                        {t('separation.noBackground')}
                      </span>
                    )}
                  </span>
                </label>
              );
            })}
          </div>
        }
      />

      <SettingRow
        stack
        title={t('separation.mvsepSepType')}
        hint={t('separation.mvsepSepTypeHint')}
        control={
          <>
            <SettingsInput
              mono
              type="text"
              value={sepType}
              onChange={(e) => {
                setSepType(e.target.value);
                setSavedType(false);
              }}
              placeholder="40"
              data-testid="separation-mvsep-sep-type"
            />
            <Button
              variant="subtle"
              size="sm"
              onClick={saveSepType}
              loading={savingType}
              disabled={savingType || sepType === (state?.mvsep_sep_type || '')}
              data-testid="separation-mvsep-sep-type-save"
            >
              {t('common.save')}
            </Button>
            {savedType && !savingType && (
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
