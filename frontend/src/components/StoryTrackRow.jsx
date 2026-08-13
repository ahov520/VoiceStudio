/**
 * StoryTrackRow — one Stories line/chapter card, extracted from StoriesEditor
 * and memoized so a 2,000-line imported novel doesn't re-render every card on
 * every keystroke, focus change or dragover tick.
 *
 * Renders in two hosts:
 *   • plain flex list (small stories) — `style` is undefined, textareas keep
 *     their manual resize handle;
 *   • react-window virtual list (large stories) — `style` carries the absolute
 *     position, row heights are deterministic, so the textarea resize handle
 *     is disabled (`virtualized`).
 *
 * The active/focused highlight is pure CSS (`.stories-line:focus-within`
 * matches the old `--active` class), so focusing a line no longer touches
 * React state at all.
 */
import React, { memo } from "react";
import {
  Play,
  Trash2,
  GripVertical,
  Mic,
  Bookmark,
  Users,
  Pause as PauseIcon,
  SlidersHorizontal,
  Laugh,
  Wind,
  CircleQuestionMark,
  Zap,
  CircleCheck,
  Annoyed,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { Menu } from "../ui";
import VoiceSelector from "./VoiceSelector";
import { castMember } from "../utils/storyCast";

// ── Shared class strings (moved verbatim from StoriesEditor) ───────────────
const SELECT_CHROME =
  "bg-bg-elev-2 border border-border rounded-md text-fg [font-size:var(--text-xs)] px-[6px] py-[4px] [font-family:var(--font-sans)] [color-scheme:dark]";
const RESET_BTN =
  "bg-transparent border border-border text-fg-subtle [font-size:var(--text-xs)] px-[8px] py-[2px] rounded-sm cursor-pointer hover:text-fg";
const SPEED_RANGE = "w-[120px]";
const TRACK_BTN =
  "w-[26px] h-[26px] flex items-center justify-center bg-transparent text-fg-subtle cursor-pointer rounded-md [transition:color_0.15s,background_0.15s,opacity_0.15s] p-0 hover:bg-white/[0.06] focus-visible:[box-shadow:var(--focus-ring)]";

// A chapter line is any track whose text is a markdown heading (`# …`). It
// renders as a section bar (no voice/tune/preview), and storyExport keys its
// chapter cues off the same prefix — keep the two in sync.
// Lenient on purpose: a heading with an empty title is still `# ` (or `#`), and
// it must stay a chapter while the user edits the title — otherwise clearing the
// text would flip the bar back into a voiced line card mid-edit.
export const isChapterText = (s) => /^\s*#{1,6}(\s|$)/.test(s || "");

// Curated inline emotion/sound tags (a subset of utils/constants TAGS) for the
// per-line tone drawer. Inserting a tag is the model-native way to direct tone.
export const STORY_TONES = [
  { tag: "[laughter]", icon: Laugh, key: "laugh" },
  { tag: "[sigh]", icon: Wind, key: "sigh" },
  { tag: "[question-en]", icon: CircleQuestionMark, key: "question" },
  { tag: "[surprise-wa]", icon: Zap, key: "surprise" },
  { tag: "[confirmation-en]", icon: CircleCheck, key: "confirm" },
  { tag: "[dissatisfaction-hnn]", icon: Annoyed, key: "dissatisfaction" },
];

function StoryTrackRow({
  track,
  index,
  cast,
  profiles,
  isDragOver,
  expanded,
  virtualized = false,
  style,
  profileName,
  onUpdate,
  onRemove,
  onPreview,
  onInsertPause,
  onInsertToken,
  onSetVoiceForSelection,
  onToggleExpand,
  registerTextRef,
  onRowDragStart,
  onRowDragOver,
  onRowDragLeave,
  onRowDrop,
}) {
  const { t } = useTranslation();

  const dragHandleProps = {
    draggable: true,
    onDragStart: (e) => {
      onRowDragStart(track.id);
      e.dataTransfer.effectAllowed = "move";
    },
  };
  const dropProps = {
    onDragOver: (e) => {
      e.preventDefault();
      onRowDragOver(track.id);
    },
    onDragLeave: () => onRowDragLeave(track.id),
    onDrop: (e) => {
      e.preventDefault();
      onRowDrop(track.id);
    },
  };

  // In the virtual list react-window positions a cell div; the card keeps its
  // own classes (and the `listitem` role) so plain-mode markup is unchanged.
  const cell = (card) => (style ? <div style={style}>{card}</div> : card);

  // Chapters render as a section bar — no voice / tune / preview.
  if (isChapterText(track.text)) {
    const title = track.text.replace(/^#{1,6}\s*/, "");
    return cell(
      <div
        role="listitem"
        className={[
          "stories-chapter group flex items-center gap-[10px] mt-[18px] mb-[2px]",
          virtualized ? "!mt-[7px]" : "",
          isDragOver
            ? "[outline:1px_dashed_var(--color-accent)] outline-offset-[2px]"
            : "",
        ]
          .filter(Boolean)
          .join(" ")}
        {...dropProps}
      >
        <div
          className="stories-line-number flex items-center justify-center text-fg-subtle cursor-grab"
          aria-hidden="true"
          {...dragHandleProps}
        >
          {String(index + 1).padStart(2, "0")}
        </div>
        <Bookmark
          size={15}
          className="flex-none text-accent"
          aria-hidden="true"
        />
        <input
          className="stories-chapter__input flex-1 min-w-0 bg-transparent border-none [font-family:inherit] text-fg px-0 py-[4px] placeholder:text-fg-subtle placeholder:font-semibold focus-visible:outline-none"
          value={title}
          onChange={(e) => onUpdate(track.id, "text", `# ${e.target.value}`)}
          placeholder={t("stories.addChapter")}
          aria-label={t("stories.addChapter")}
          name={`story-chapter-${track.id}`}
          autoComplete="off"
        />
        <button
          type="button"
          className="stories-icon-button flex-none flex p-[6px] bg-transparent border-none text-fg-subtle cursor-pointer opacity-0 group-hover:opacity-70 group-focus-within:opacity-70 hover:!opacity-100 hover:text-danger focus-visible:!opacity-100"
          onClick={(e) => {
            e.stopPropagation();
            onRemove(track.id);
          }}
          title={t("stories.removeLine")}
          aria-label={t("stories.removeLine")}
        >
          <Trash2 size={13} />
        </button>
      </div>,
    );
  }

  const member = castMember(cast, track.character);
  const inheritedId = member && member.profileId;
  const inheritedName = inheritedId ? profileName(inheritedId) : null;
  return cell(
    <div
      role="listitem"
      className={[
        "stories-line group grid items-center cursor-grab",
        track.character === "narrator"
          ? "[border-left:3px_solid_var(--color-accent)]"
          : "",
        isDragOver ? "[box-shadow:inset_0_2px_0_0_var(--color-accent)]" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      {...dropProps}
    >
      <div
        className="stories-line__drag flex flex-col items-center justify-center gap-[2px] text-fg-subtle cursor-grab active:cursor-grabbing"
        aria-hidden="true"
        {...dragHandleProps}
      >
        <span className="stories-line-number">
          {String(index + 1).padStart(2, "0")}
        </span>
        <GripVertical size={14} />
      </div>

      <textarea
        className={`stories-line__text w-full bg-transparent border border-transparent text-fg [font-family:var(--font-sans)] leading-[1.65] focus-visible:outline-none ${
          virtualized ? "resize-none" : "resize-y"
        }`}
        ref={(el) => registerTextRef(track.id, el)}
        value={track.text}
        onChange={(e) => onUpdate(track.id, "text", e.target.value)}
        placeholder={t("stories.linePlaceholder")}
        rows={1}
        aria-label={`${member ? member.name : ""} ${t("stories.text")}`}
        name={`story-line-${track.id}`}
        autoComplete="off"
      />

      <div className="stories-line__character flex items-center gap-[7px] min-w-0">
        <span
          className="w-[10px] h-[10px] rounded-full shrink-0"
          style={{ background: member ? member.color : "#a89984" }}
        />
        <select
          className={`${SELECT_CHROME} flex-1`}
          value={track.character}
          onChange={(e) => onUpdate(track.id, "character", e.target.value)}
          aria-label={t("stories.character")}
          name={`story-character-for-line-${track.id}`}
        >
          {cast.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </div>

      {/* Per-line voice override → shared gallery-enabled picker (#1220).
        '' inherits the character's cast voice (label shows "↳ <name>"); any
        pick stores a real profile id (gallery picks materialize first).
        `|| null` keeps the store's null-default shape so existing projects
        load unchanged. */}
      <span className="stories-line__voice min-w-0">
        <VoiceSelector
          value={track.profileId || ""}
          onChange={(v) => onUpdate(track.id, "profileId", v || null)}
          profiles={profiles}
          size="sm"
          menuPortal
          defaultLabel={
            inheritedName ? `↳ ${inheritedName}` : t("stories.defaultVoice")
          }
        />
      </span>

      <div className="stories-line__actions flex gap-[4px] [transition:opacity_0.12s_ease] opacity-45 group-hover:opacity-100 group-focus-within:opacity-100">
        <Menu
          placement="bottom-end"
          items={[
            ...(profiles.length === 0
              ? [
                  {
                    id: "noprof",
                    label: t("stories.noProfiles"),
                    disabled: true,
                  },
                ]
              : profiles.map((p) => ({
                  id: `voice-${p.id}`,
                  label: p.name,
                  onSelect: () => onSetVoiceForSelection(track.id, p.id),
                }))),
            "separator",
            {
              id: "voice-default",
              label: t("stories.resetInlineVoice"),
              onSelect: () => onSetVoiceForSelection(track.id, "default"),
            },
          ]}
        >
          <button
            type="button"
            className={`${TRACK_BTN} hover:text-fg`}
            onClick={(e) => e.stopPropagation()}
            title={t("stories.inlineVoiceHint")}
            aria-label={t("stories.inlineVoice")}
          >
            <Users size={12} />
          </button>
        </Menu>
        <button
          type="button"
          className={`${TRACK_BTN} hover:text-fg ${expanded ? "text-accent bg-white/[0.06]" : ""}`}
          onClick={(e) => {
            e.stopPropagation();
            onToggleExpand(track.id);
          }}
          title={t("stories.tune")}
          aria-label={t("stories.tune")}
        >
          <SlidersHorizontal size={12} />
        </button>
        <button
          type="button"
          className={`${TRACK_BTN} hover:text-fg`}
          onClick={(e) => {
            e.stopPropagation();
            onInsertPause(track.id);
          }}
          title={t("stories.insertPause")}
          aria-label={t("stories.insertPause")}
        >
          <PauseIcon size={12} />
        </button>
        <button
          type="button"
          className={`${TRACK_BTN} hover:text-fg`}
          onClick={(e) => {
            e.stopPropagation();
            onPreview(track);
          }}
          disabled={track.generating || !track.text.trim()}
          title={t("stories.preview")}
          aria-label={t("stories.preview")}
        >
          {track.generating ? (
            <Mic size={12} className="spinner" />
          ) : (
            <Play size={12} />
          )}
        </button>
        <button
          type="button"
          className={`${TRACK_BTN} hover:text-danger`}
          onClick={(e) => {
            e.stopPropagation();
            onRemove(track.id);
          }}
          title={t("stories.removeLine")}
          aria-label={t("stories.removeLine")}
        >
          <Trash2 size={12} />
        </button>
      </div>

      {expanded && (
        <div
          className="stories-line__drawer basis-full flex flex-wrap items-center gap-[12px]"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex flex-wrap gap-[4px]">
            {STORY_TONES.map((tn) => (
              <button
                key={tn.tag}
                type="button"
                className="inline-flex items-center gap-[4px] bg-bg-elev-2 border border-border rounded-full text-fg [font-size:var(--text-xs)] px-[9px] py-[3px] cursor-pointer hover:text-accent"
                onClick={() => onInsertToken(track.id, tn.tag)}
                title={tn.tag}
              >
                <tn.icon size={12} aria-hidden="true" />{" "}
                {t(`stories.tones.${tn.key}`)}
              </button>
            ))}
          </div>
          <label className="inline-flex items-center gap-[8px] [font-size:var(--text-xs)] text-fg-subtle">
            <span>{t("stories.speed")}</span>
            <input
              type="range"
              min="0.5"
              max="2"
              step="0.05"
              value={track.speed || 1}
              onChange={(e) =>
                onUpdate(track.id, "speed", parseFloat(e.target.value))
              }
              aria-label={t("stories.speed")}
              name={`story-speed-${track.id}`}
              className={SPEED_RANGE}
            />
            <span className="[font-family:var(--font-mono)] text-fg min-w-[44px]">
              {(track.speed || 1).toFixed(2)}×
            </span>
            {track.speed != null && (
              <button
                type="button"
                className={RESET_BTN}
                onClick={() => onUpdate(track.id, "speed", null)}
              >
                {t("stories.reset")}
              </button>
            )}
          </label>
        </div>
      )}
    </div>,
  );
}

export default memo(StoryTrackRow);
