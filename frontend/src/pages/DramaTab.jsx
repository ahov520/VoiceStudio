import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import toast from 'react-hot-toast';
import { Clapperboard, Wand2, Save, FolderOpen, Copy, Trash2, Loader } from 'lucide-react';
import { Button } from '../ui';
import {
  parseDrama,
  saveDramaProject,
  listDramaProjects,
  getDramaProject,
  deleteDramaProject,
} from '../api/drama';

//: Emotion set mirrors backend/services/drama_director.py EMOTIONS.
const EMOTIONS = [
  'neutral', 'calm', 'happy', 'sad', 'angry', 'fearful',
  'surprised', 'whispered', 'shouting', 'crying', 'sarcastic', 'tense',
];

export default function DramaTab({ profiles = [] }) {
  const { t } = useTranslation();
  const [script, setScript] = useState('');
  const [parsing, setParsing] = useState(false);
  const [parseError, setParseError] = useState(null);
  const [cast, setCast] = useState([]);
  const [lines, setLines] = useState([]);
  const [scriptText, setScriptText] = useState('');
  const [voiceMap, setVoiceMap] = useState({});
  const [projectName, setProjectName] = useState('');
  const [projects, setProjects] = useState([]);
  const [saving, setSaving] = useState(false);

  const refreshProjects = useCallback(async () => {
    try {
      const res = await listDramaProjects();
      setProjects(res.projects || []);
    } catch {
      // Project list is non-critical; keep whatever we had.
    }
  }, []);

  useEffect(() => {
    refreshProjects();
  }, [refreshProjects]);

  const onDirect = useCallback(async () => {
    if (!script.trim()) return;
    setParsing(true);
    setParseError(null);
    try {
      const res = await parseDrama(script, profiles);
      setCast(res.cast);
      setLines(res.lines);
      setScriptText(res.script_text);
      setVoiceMap(res.voice_map);
      setProjectName((prev) => prev || (res.cast[0] ? t('drama.default_project_name', { name: res.cast[0].name }) : ''));
    } catch (err) {
      setParseError(err?.message || String(err));
    } finally {
      setParsing(false);
    }
  }, [script, profiles, t]);

  const patchLine = useCallback((index, patch) => {
    setLines((prev) => prev.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  }, []);

  const patchCast = useCallback((index, patch) => {
    setCast((prev) => prev.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  }, []);

  // Recompile the audiobook script whenever cast/lines change.
  useEffect(() => {
    if (!cast.length && !lines.length) return;
    const out = [`# ${projectName || 'Drama'}`];
    let current = null;
    const vm = {};
    for (const c of cast) {
      if (c.voice?.profile_id) vm[c.name] = c.voice.profile_id;
    }
    for (const ln of lines) {
      if (ln.speaker !== current) {
        out.push(`[voice:${ln.speaker}]`);
        current = ln.speaker;
      }
      const emotion = EMOTIONS.includes(ln.emotion) ? ln.emotion : 'neutral';
      const marker = emotion === 'happy' || emotion === 'angry' || emotion === 'shouting' ? 'fast'
        : emotion === 'calm' || emotion === 'sad' || emotion === 'fearful' || emotion === 'whispered' || emotion === 'crying' ? 'slow'
        : emotion === 'surprised' || emotion === 'sarcastic' || emotion === 'tense' ? 'emphasis'
        : null;
      const text = ln.text || '';
      const rendered = marker ? `[${marker}]${text}[/${marker}]` : text;
      const pause = 180 + Math.round((ln.intensity || 0) * 200);
      out.push(`${rendered} [pause ${pause}]`);
    }
    setScriptText(out.join('\n'));
    setVoiceMap(vm);
  }, [cast, lines, projectName]);

  const onSave = useCallback(async () => {
    if (!scriptText.trim()) return;
    setSaving(true);
    try {
      await saveDramaProject({
        name: projectName.trim() || 'Drama',
        script,
        cast,
        lines,
      });
      toast.success(t('drama.saved'));
      refreshProjects();
    } catch (err) {
      toast.error(err?.message || String(err));
    } finally {
      setSaving(false);
    }
  }, [scriptText, projectName, script, cast, lines, t, refreshProjects]);

  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(scriptText);
      toast.success(t('drama.copied'));
    } catch {
      toast.error(t('drama.copy_failed'));
    }
  }, [scriptText, t]);

  const onLoad = useCallback(async (id) => {
    try {
      const p = await getDramaProject(id);
      setScript(p.script || '');
      setCast(p.cast || []);
      setLines(p.lines || []);
      setProjectName(p.name || '');
      setParseError(null);
    } catch (err) {
      toast.error(err?.message || String(err));
    }
  }, []);

  const onDelete = useCallback(async (id) => {
    try {
      await deleteDramaProject(id);
      refreshProjects();
    } catch (err) {
      toast.error(err?.message || String(err));
    }
  }, [refreshProjects]);

  const castProfiles = useMemo(
    () => profiles.filter((p) => p && p.id && p.name),
    [profiles],
  );

  return (
    <div className="flex-1 flex flex-col min-h-0 px-[10px] py-[8px] gap-[8px]">
      <div className="flex justify-between items-center shrink-0">
        <div className="label-row">
          <Clapperboard className="label-icon" size={13} />
          <span className="font-semibold text-[0.85rem] text-fg">{t('drama.title')}</span>
          <span className="text-fg-muted text-[0.7rem]">{t('drama.subtitle')}</span>
        </div>
        <Button variant="subtle" size="sm" onClick={() => setScript(t('drama.sample_script'))}>
          {t('drama.sample')}
        </Button>
      </div>

      <div className="flex flex-1 min-h-0 gap-[8px]">
        {/* ── Left: script input ── */}
        <div className="flex flex-col min-w-0 w-[38%] gap-[6px]">
          <div className="flex items-center justify-between">
            <span className="text-[0.7rem] text-fg-muted">{t('drama.script_label')}</span>
            <Button
              variant="primary"
              size="sm"
              onClick={onDirect}
              disabled={!script.trim() || parsing}
            >
              {parsing ? <Loader className="spinner" size={13} /> : <Wand2 size={13} />}
              {parsing ? t('drama.directing') : t('drama.auto_direct')}
            </Button>
          </div>
          <textarea
            className="input-base flex-1 min-h-0 resize-none font-mono text-[0.72rem] leading-relaxed"
            placeholder={t('drama.script_placeholder')}
            value={script}
            onChange={(e) => setScript(e.target.value)}
            spellCheck={false}
          />
          {parseError && (
            <div className="text-[0.65rem] text-[#fb4934] bg-[rgba(251,73,52,0.08)] rounded-[5px] px-[8px] py-[5px]">
              {parseError}
            </div>
          )}
        </div>

        {/* ── Right: cast + lines + output ── */}
        <div className="flex flex-1 min-w-0 flex-col gap-[8px]">
          {cast.length === 0 && lines.length === 0 ? (
            <div className="flex-1 flex items-center justify-center text-[0.75rem] text-fg-muted border border-dashed border-border rounded-[8px]">
              {t('drama.empty_hint')}
            </div>
          ) : (
            <>
              <div className="flex flex-col min-h-0 flex-1 gap-[6px]">
                <span className="text-[0.7rem] text-fg-muted shrink-0">
                  {t('drama.cast_label')} ({cast.length})
                </span>
                <div className="flex flex-wrap gap-[6px] overflow-y-auto shrink-0 max-h-[140px]">
                  {cast.map((c, ci) => (
                    <div
                      key={c.name + ci}
                      className="flex items-center gap-[6px] px-[8px] py-[5px] bg-[rgba(255,255,255,0.03)] [border:1px_solid_rgba(255,255,255,0.08)] rounded-[6px]"
                    >
                      <span className="text-[0.7rem] text-fg font-medium">{c.name}</span>
                      <select
                        className="input-base text-[0.62rem] max-w-[150px]"
                        value={c.voice?.profile_id || ''}
                        onChange={(e) =>
                          patchCast(ci, {
                            voice: e.target.value
                              ? { profile_id: e.target.value }
                              : { recipe_instruct: c.description || c.name },
                          })
                        }
                      >
                        <option value="">{t('drama.voice_auto')}</option>
                        {castProfiles.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name}
                          </option>
                        ))}
                      </select>
                      {!c.voice?.profile_id && (
                        <span
                          className="text-[0.58rem] text-fg-muted truncate max-w-[140px]"
                          title={c.voice?.recipe_instruct || c.description}
                        >
                          ✦ {c.voice?.recipe_instruct || c.description || t('drama.voice_recipe')}
                        </span>
                      )}
                    </div>
                  ))}
                </div>

                <span className="text-[0.7rem] text-fg-muted shrink-0">{t('drama.lines_label')}</span>
                <div className="flex-1 min-h-0 overflow-y-auto flex flex-col gap-[4px]">
                  {lines.map((ln, li) => (
                    <div
                      key={li}
                      className="flex items-start gap-[6px] px-[8px] py-[5px] bg-[rgba(255,255,255,0.02)] [border:1px_solid_rgba(255,255,255,0.05)] rounded-[6px]"
                    >
                      <span className="text-[0.62rem] text-[#b8bb26] whitespace-nowrap mt-[2px] shrink-0">
                        {ln.speaker}
                      </span>
                      <span className="flex-1 min-w-0 text-[0.7rem] text-fg leading-snug">
                        {ln.text}
                      </span>
                      <select
                        className="input-base text-[0.6rem] shrink-0"
                        value={ln.emotion}
                        onChange={(e) => patchLine(li, { emotion: e.target.value })}
                        aria-label={t('drama.emotion')}
                      >
                        {EMOTIONS.map((e) => (
                          <option key={e} value={e}>
                            {t(`drama.emotion_${e}`)}
                          </option>
                        ))}
                      </select>
                      <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.1"
                        value={ln.intensity}
                        onChange={(e) => patchLine(li, { intensity: Number(e.target.value) })}
                        className="w-[64px] shrink-0 accent-[var(--color-brand)]"
                        aria-label={t('drama.intensity')}
                      />
                    </div>
                  ))}
                </div>
              </div>

              <div className="flex flex-col gap-[6px] shrink-0">
                <div className="flex items-center gap-[6px]">
                  <span className="text-[0.7rem] text-fg-muted">{t('drama.output_label')}</span>
                  <input
                    className="input-base text-[0.65rem] flex-1"
                    placeholder={t('drama.project_name')}
                    value={projectName}
                    onChange={(e) => setProjectName(e.target.value)}
                  />
                  <Button variant="subtle" size="sm" onClick={onCopy} disabled={!scriptText.trim()}>
                    <Copy size={12} /> {t('drama.copy')}
                  </Button>
                  <Button variant="primary" size="sm" onClick={onSave} disabled={!scriptText.trim() || saving}>
                    {saving ? <Loader className="spinner" size={12} /> : <Save size={12} />}
                    {t('drama.save')}
                  </Button>
                </div>
                <textarea
                  className="input-base h-[110px] resize-none font-mono text-[0.65rem] leading-relaxed"
                  readOnly
                  value={scriptText}
                  spellCheck={false}
                />
                {projects.length > 0 && (
                  <div className="flex flex-wrap gap-[6px] items-center">
                    <FolderOpen size={12} className="text-fg-muted" />
                    {projects.map((p) => (
                      <span
                        key={p.id}
                        className="flex items-center gap-[5px] px-[7px] py-[3px] bg-[rgba(255,255,255,0.03)] [border:1px_solid_rgba(255,255,255,0.07)] rounded-[5px]"
                      >
                        <button
                          type="button"
                          className="text-[0.62rem] text-fg hover:text-[#b8bb26]"
                          onClick={() => onLoad(p.id)}
                        >
                          {p.name} ({p.line_count})
                        </button>
                        <button
                          type="button"
                          className="text-fg-muted hover:text-[#fb4934]"
                          onClick={() => onDelete(p.id)}
                          aria-label={t('drama.delete')}
                        >
                          <Trash2 size={10} />
                        </button>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
