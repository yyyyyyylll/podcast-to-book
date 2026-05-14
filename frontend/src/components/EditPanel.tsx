import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import {
  Tabs, Button, Input, Select, message, Modal, Upload, Radio, Popconfirm, Spin, Tag,
} from 'antd';
import {
  SaveOutlined, PlusOutlined, DeleteOutlined,
  MenuOutlined, UploadOutlined, CheckOutlined,
  LoadingOutlined, DownOutlined, RightOutlined, SwapOutlined, EditOutlined, SearchOutlined, SwapRightOutlined, EyeOutlined,
} from '@ant-design/icons';
import {
  DndContext, closestCenter, PointerSensor, useSensor, useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext, useSortable, verticalListSortingStrategy, arrayMove,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import {
  getEditData, saveAndRegenerate, uploadIllustration, uploadCustomCover, uploadFullCover, uploadBackCover,
  getApiErrorMessage,
  type EditData, type EditIllustration, type EditRequest,
} from '../services/api';
import { analytics } from '../services/analytics';
import SegmentBodyEditor, { type SegmentBodyEditorHandle } from './SegmentBodyEditor';

const { TextArea } = Input;

function toCnNum(n: number): string {
  const d = String.fromCharCode(0x96F6, 0x4E00, 0x4E8C, 0x4E09, 0x56DB, 0x4E94, 0x516D, 0x4E03, 0x516B, 0x4E5D);
  const shi = String.fromCharCode(0x5341);
  if (n <= 0) return d[0];
  if (n <= 10) return n === 10 ? shi : d[n];
  if (n < 20) return shi + d[n - 10];
  if (n === 20) return d[2] + shi;
  if (n < 30) return d[2] + shi + d[n - 20];
  if (n === 30) return d[3] + shi;
  return String(n);
}

/** 仅前端用的 UI 扩展类型：在后端 EditIllustration 基础上增加 _uid、move_paragraph_dir 等字段，用于定位 after_paragraph */
type IllustrationUi = EditIllustration & {
  _uid: string;
  move_paragraph_dir?: 'forward' | 'backward';
  move_paragraph_steps?: number;
};

interface QuoteItem {
  _uid: string;
  text: string;
  speaker?: string;
  placement: 'epigraph' | 'inline_card';
  chapter_title: string;
  after_paragraph: number | null;
  move_paragraph_dir?: 'forward' | 'backward';
  move_paragraph_steps?: number;
}

// ====== Footnote utils ======

function parseFootnotes(content: string): { body: string; footnotes: { id: number; text: string }[] } {
  const match = content.match(/\n\n---\n+([\s\S]*)$/);
  if (!match) return { body: content, footnotes: [] };
  const body = content.slice(0, match.index!);
  const fnBlock = match[1];
  const footnotes: { id: number; text: string }[] = [];
  const parts = fnBlock.split(/(?=\[\^\d+\]:)/);
  for (const part of parts) {
    const m = part.match(/^\[\^(\d+)\]:\s*([\s\S]*)/);
    if (m) {
      footnotes.push({ id: parseInt(m[1], 10), text: m[2].trim() });
    }
  }
  return { body, footnotes };
}

function rebuildContent(body: string, footnotes: { id: number; text: string }[]): string {
  if (footnotes.length === 0) return body;
  const sorted = [...footnotes].sort((a, b) => a.id - b.id);
  const fnBlock = sorted.map((fn) => `[^${fn.id}]: ${fn.text}`).join('\n');
  return `${body}\n\n---\n${fnBlock}`;
}

function renumberFootnotes(content: string, oldIds: number[], newIds: number[]): string {
  let result = content;
  const mapping = new Map<number, number>();
  oldIds.forEach((old, i) => mapping.set(old, newIds[i]));
  mapping.forEach((newId, oldId) => {
    result = result.replace(new RegExp(`\\[\\^${oldId}\\](?!:)`, 'g'), `[^__TEMP_${newId}__]`);
  });
  mapping.forEach((newId) => {
    result = result.replace(new RegExp(`\\[\\^__TEMP_${newId}__\\]`, 'g'), `[^${newId}]`);
  });
  return result;
}

/** 脚注插入时用的不可见占位符（Object Replacement Character \uFFFC），避免与正文文本或 [^9]、[^10] 等脚注引用冲突 */
const FOOTNOTE_INSERT_MARKER = '\uFFFC';

function maxFootnoteRefIdBefore(
  segments: SpeakerSegment[],
  targetSegId: string,
  cursorPos: number,
): number {
  let maxBefore = 0;
  for (const seg of segments) {
    if (seg.id !== targetSegId) {
      const re = /\[\^(\d+)\]/g;
      let m: RegExpExecArray | null;
      while ((m = re.exec(seg.text)) !== null) {
        maxBefore = Math.max(maxBefore, parseInt(m[1], 10));
      }
    } else {
      const re = /\[\^(\d+)\]/g;
      let m: RegExpExecArray | null;
      while ((m = re.exec(seg.text)) !== null) {
        if (m.index < cursorPos) {
          maxBefore = Math.max(maxBefore, parseInt(m[1], 10));
        }
      }
      return maxBefore;
    }
  }
  return maxBefore;
}

/** 将段落文本中 [^id] 形式的脚注引用，对 id 位于 [fromId, maxId] 范围的统一 +1，用于插入新脚注后把后续引用整体后移 */
function shiftFootnoteRefsInSegmentText(text: string, fromId: number, maxId: number): string {
  if (fromId > maxId) return text;
  let result = text;
  for (let id = maxId; id >= fromId; id--) {
    result = result.replace(new RegExp(`\\[\\^${id}\\](?!:)`, 'g'), `[^__FN${id + 1}__]`);
  }
  for (let id = maxId; id >= fromId; id--) {
    result = result.replace(new RegExp(`\\[\\^__FN${id + 1}__\\](?!:)`, 'g'), `[^${id + 1}]`);
  }
  return result;
}

type SpeakerSegment = {
  id: string;
  speaker: string;
  text: string;
};

const _COLON_CLASS = `[\uFF1A:]`;
const _SPEAKER_PATTERNS = [
  new RegExp(`^\\*\\*(.+?)${_COLON_CLASS}\\*\\*\\s*([\\s\\S]*)$`),
  new RegExp(`^\\*\\*(.+?)\\*\\*\\s*${_COLON_CLASS}\\s*([\\s\\S]*)$`),
  new RegExp(`^([^:\\n\uFF1A]{1,40})${_COLON_CLASS}\\s+([\\s\\S]*)$`),
];
const _PLACEHOLDER_RE = /^(\u8BF4\u8BDD\u4EBA\d*|Speaker\s*\d*)$/i;

function _extractSpeaker(para: string): { speaker: string; text: string } {
  for (const re of _SPEAKER_PATTERNS) {
    const m = para.match(re);
    if (m) {
      const name = m[1].trim();
      if (_PLACEHOLDER_RE.test(name)) break;
      return { speaker: name, text: m[2].trim() };
    }
  }
  return { speaker: '', text: para };
}

function parseBodyToSegments(body: string): SpeakerSegment[] {
  const rawParts = body
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);
  if (rawParts.length === 0) {
    return [{ id: `seg_${Date.now()}_0`, speaker: '', text: '' }];
  }
  const segments: SpeakerSegment[] = [];
  let curSpeaker = '';
  let curTexts: string[] = [];
  let segIdx = 0;
  const flush = () => {
    if (curTexts.length > 0) {
      segments.push({
        id: `seg_${Date.now()}_${segIdx++}`,
        speaker: curSpeaker,
        text: curTexts.join('\n\n'),
      });
      curTexts = [];
    }
  };
  for (const part of rawParts) {
    const { speaker, text } = _extractSpeaker(part);
    if (speaker && speaker !== curSpeaker) {
      flush();
      curSpeaker = speaker;
      curTexts.push(text);
    } else if (speaker && speaker === curSpeaker) {
      curTexts.push(text);
    } else {
      if (segments.length === 0 && curTexts.length === 0) {
        curTexts.push(text);
      } else {
        curTexts.push(part);
      }
    }
  }
  flush();
  if (segments.length === 0) {
    segments.push({ id: `seg_${Date.now()}_0`, speaker: '', text: '' });
  }
  return segments;
}

function serializeSegmentsToBody(segments: SpeakerSegment[]): string {
  const outParts: string[] = [];
  for (const seg of segments) {
    const speaker = seg.speaker.trim();
    const text = seg.text.trim();
    if (!text) continue;
    const paragraphs = text.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
    for (let i = 0; i < paragraphs.length; i++) {
      if (i === 0 && speaker) {
        outParts.push(`**${speaker}\uFF1A**${paragraphs[i]}`);
      } else {
        outParts.push(paragraphs[i]);
      }
    }
  }
  return outParts.join('\n\n');
}

/** 跨整本书计算"插入点之前"的最大脚注引用 id：当前章之前所有章节 + 本章在插入点之前的段落 */
function maxFootnoteRefIdBeforeBook(
  chapters: { content?: string }[],
  activeChapterIndex: number,
  segments: SpeakerSegment[],
  targetSegId: string,
  cursorPos: number,
): number {
  let maxBefore = 0;
  for (let i = 0; i < activeChapterIndex; i++) {
    const { body } = parseFootnotes(chapters[i]?.content || '');
    const re = /\[\^(\d+)\]/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(body)) !== null) {
      maxBefore = Math.max(maxBefore, parseInt(m[1], 10));
    }
  }
  return Math.max(maxBefore, maxFootnoteRefIdBefore(segments, targetSegId, cursorPos));
}

/** 统计整本书所有章节（含其 segments 正文）中出现过的最大脚注 id，用于生成新脚注时避免冲突 */
function maxFootnoteIdAcrossBook(
  chapters: { content?: string }[],
  activeChapterIndex: number,
  segments: SpeakerSegment[],
  activeFootnotes: { id: number; text: string }[],
): number {
  let maxId = 0;
  for (let i = 0; i < chapters.length; i++) {
    if (i === activeChapterIndex) {
      const body = serializeSegmentsToBody(segments);
      const re = /\[\^(\d+)\]/g;
      let m: RegExpExecArray | null;
      while ((m = re.exec(body)) !== null) {
        maxId = Math.max(maxId, parseInt(m[1], 10));
      }
      for (const fn of activeFootnotes) {
        maxId = Math.max(maxId, fn.id);
      }
    } else {
      const { body, footnotes } = parseFootnotes(chapters[i]?.content || '');
      const re = /\[\^(\d+)\]/g;
      let m: RegExpExecArray | null;
      while ((m = re.exec(body)) !== null) {
        maxId = Math.max(maxId, parseInt(m[1], 10));
      }
      for (const fn of footnotes) {
        maxId = Math.max(maxId, fn.id);
      }
    }
  }
  return maxId;
}

// ====== Sortable illustration card ======

function SortableIllustrationCard({
  ill, index, chapters, onDelete, onEdit, onReplace,
}: {
  ill: IllustrationUi;
  index: number;
  chapters: string[];
  onDelete: () => void;
  onEdit: (field: string, value: any) => void;
  onReplace: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition } = useSortable({ id: ill._uid });
  const style = { transform: CSS.Transform.toString(transform), transition };
  const moveDir = ill.move_paragraph_dir ?? 'backward';
  const moveSteps = ill.move_paragraph_steps ?? 0;

  return (
    <div ref={setNodeRef} style={style} className="edit-ill-card">
      <div className="edit-ill-thumb" onClick={onReplace} title={"\u70B9\u51FB\u66FF\u6362\u56FE\u7247"}>
        {ill.preview_url ? (
          <img src={ill.preview_url} alt={ill.caption || `\u63D2\u56FE ${index + 1}`} />
        ) : (
          <div className="edit-ill-thumb-empty">{"\u65E0\u56FE"}</div>
        )}
        <div className="edit-ill-thumb-overlay">
          <SwapOutlined />
        </div>
      </div>

      <div className="edit-ill-body">
        <Input
          size="small"
          value={ill.caption}
          onChange={(e) => onEdit('caption', e.target.value)}
          placeholder={"\u6DFB\u52A0\u56FE\u6CE8"}
          variant="borderless"
          className="edit-ill-caption"
        />

        <div className="edit-ill-controls">
          <div className="edit-ill-control-group">
            <span className="edit-ill-label">{"\u79FB\u52A8\u4F4D\u7F6E"}</span>
            <Select
              size="small"
              value={moveDir}
              onChange={(v) => onEdit('move_paragraph_dir', v)}
              style={{ width: 58 }}
              variant="borderless"
              popupClassName="edit-ill-select-popup"
              options={[
                { label: '\u524D\u79FB', value: 'forward' },
                { label: '\u540E\u79FB', value: 'backward' },
              ]}
            />
            <Input
              size="small"
              type="number"
              min={0}
              value={moveSteps}
              onChange={(e) => onEdit('move_paragraph_steps', Math.max(0, parseInt(e.target.value, 10) || 0))}
              style={{ width: 52 }}
              variant="borderless"
              suffix={"\u6BB5"}
              className="edit-ill-num"
            />
          </div>

        </div>
      </div>

      <div className="edit-ill-actions">
        <Popconfirm title={"\u786E\u5B9A\u5220\u9664\u6B64\u63D2\u56FE\u5417\uFF1F"} onConfirm={onDelete} okText={"\u786E\u5B9A"} cancelText={"\u53D6\u6D88"}>
          <button className="edit-ill-action-btn edit-ill-action-danger" title={"\u5220\u9664\u63D2\u56FE"}>
            <DeleteOutlined />
          </button>
        </Popconfirm>
      </div>
    </div>
  );
}

// ====== Quote card ======

function QuoteCard({
  quote, chapters, onEdit, onDelete,
}: {
  quote: QuoteItem;
  chapters: string[];
  onEdit: (field: string, value: any) => void;
  onDelete: () => void;
}) {
  const moveDir = quote.move_paragraph_dir ?? 'backward';
  const moveSteps = quote.move_paragraph_steps ?? 0;

  return (
    <div className="edit-quote-card">
      <div className="edit-quote-body">
        <div className="edit-quote-text-wrap">
          <TextArea
            value={quote.text}
            onChange={(e) => onEdit('text', e.target.value)}
            autoSize={{ minRows: 1, maxRows: 6 }}
            variant="borderless"
            className="edit-quote-text"
            placeholder={"\u8F93\u5165\u91D1\u53E5\u5185\u5BB9"}
          />
        </div>

        <div className="edit-quote-controls">
          <div className="edit-ill-control-group">
            <span className="edit-ill-label">{"\u79FB\u52A8\u4F4D\u7F6E"}</span>
            <Select
              size="small"
              value={moveDir}
              onChange={(v) => onEdit('move_paragraph_dir', v)}
              style={{ width: 58 }}
              variant="borderless"
              popupClassName="edit-ill-select-popup"
              options={[
                { label: '\u524D\u79FB', value: 'forward' },
                { label: '\u540E\u79FB', value: 'backward' },
              ]}
            />
            <Input
              size="small"
              type="number"
              min={0}
              value={moveSteps}
              onChange={(e) => onEdit('move_paragraph_steps', Math.max(0, parseInt(e.target.value, 10) || 0))}
              style={{ width: 52 }}
              variant="borderless"
              suffix={"\u6BB5"}
              className="edit-ill-num"
            />
          </div>
        </div>
      </div>

      <div className="edit-ill-actions">
        <Popconfirm title={"\u786E\u5B9A\u5220\u9664\u6B64\u91D1\u53E5\u5417\uFF1F"} onConfirm={onDelete} okText={"\u786E\u5B9A"} cancelText={"\u53D6\u6D88"}>
          <button className="edit-ill-action-btn edit-ill-action-danger" title={"\u5220\u9664\u91D1\u53E5"}>
            <DeleteOutlined />
          </button>
        </Popconfirm>
      </div>
    </div>
  );
}

// ====== Collapsible section ======

function CollapseSection({ title, defaultOpen = false, children }: {
  title: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="edit-collapse-section">
      <div className="edit-collapse-header" onClick={() => setOpen(!open)}>
        <span className="edit-collapse-arrow">{open ? <DownOutlined /> : <RightOutlined />}</span>
        <span className="edit-collapse-title">{title}</span>
      </div>
      {open && <div className="edit-collapse-body">{children}</div>}
    </div>
  );
}

// ====== EditPanel component ======

export interface EditPanelProps {
  taskId: string;
  onSaveComplete: (newPdfUrl: string) => void;
  onCancel: () => void;
  onPreviewRefresh?: (newPdfUrl: string) => void;
}

export default function EditPanel({ taskId, onSaveComplete, onCancel, onPreviewRefresh }: EditPanelProps) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [data, setData] = useState<EditData | null>(null);
  const [isDirty, setIsDirty] = useState(false);
  const [activeTab, setActiveTab] = useState('settings');

  const [userTitle, setUserTitle] = useState('');
  const [userHostName, setUserHostName] = useState('');
  const [userGuestNames, setUserGuestNames] = useState<string[]>([]);
  const [editorPreface, setEditorPreface] = useState('');

  const [chapters, setChapters] = useState<any[]>([]);
  const [activeChapter, setActiveChapter] = useState(0);

  const [illustrations, setIllustrations] = useState<IllustrationUi[]>([]);
  const [showAddIllModal, setShowAddIllModal] = useState(false);
  const [newIllFile, setNewIllFile] = useState<File | null>(null);
  const [newIllChapter, setNewIllChapter] = useState('');
  const [newIllParagraph, setNewIllParagraph] = useState(1);
  const [newIllCaption, setNewIllCaption] = useState('');
  const [uploading, setUploading] = useState(false);
  const replaceInputRef = useRef<HTMLInputElement>(null);
  const [replacingUid, setReplacingUid] = useState<string | null>(null);

  const [highlights, setHighlights] = useState<any>({ quotes: [] });
  const [quotes, setQuotes] = useState<QuoteItem[]>([]);
  const [showAddQuoteModal, setShowAddQuoteModal] = useState(false);
  const [newQuoteText, setNewQuoteText] = useState('');
  const [newQuoteChapter, setNewQuoteChapter] = useState('');
  const [newQuoteParagraph, setNewQuoteParagraph] = useState(1);

  const [summaryBullets, setSummaryBullets] = useState<string[]>([]);
  const [epigraphText, setEpigraphText] = useState('');
  const [epigraphSpeaker, setEpigraphSpeaker] = useState('');

  const [coverStyle, setCoverStyle] = useState('');
  const [customCoverUrl, setCustomCoverUrl] = useState('');
  const [coverUploading, setCoverUploading] = useState(false);
  const [customFullCoverUrl, setCustomFullCoverUrl] = useState('');
  const [fullCoverUploading, setFullCoverUploading] = useState(false);
  const [customBackCoverUrl, setCustomBackCoverUrl] = useState('');
  const [backCoverUploading, setBackCoverUploading] = useState(false);
  const [coverGridOpen, setCoverGridOpen] = useState(false);
  const [pendingRestoreOriginalCover, setPendingRestoreOriginalCover] = useState(false);

  const segmentInputRefs = useRef<Record<string, SegmentBodyEditorHandle | null>>({});
  /** 记录"插入脚注/插图"触发时的光标位置快照，便于弹窗关闭或异步操作后恢复焦点/选区 */
  const insertCaretSnapshotRef = useRef<{ segmentId: string; offset: number } | null>(null);
  const [footnoteQuickEditId, setFootnoteQuickEditId] = useState<number | null>(null);
  const [footnoteQuickEditText, setFootnoteQuickEditText] = useState('');
  const [focusedSegmentId, setFocusedSegmentId] = useState<string>('');
  const [focusedCursorPos, setFocusedCursorPos] = useState(0);
  const [insertFnText, setInsertFnText] = useState('');
  const [showInsertFnModal, setShowInsertFnModal] = useState(false);
  const [showReplaceModal, setShowReplaceModal] = useState(false);
  const [replaceFrom, setReplaceFrom] = useState('');
  const [replaceTo, setReplaceTo] = useState('');
  const [customSpeakerDraft, setCustomSpeakerDraft] = useState('');
  const [renameSpeakerOpen, setRenameSpeakerOpen] = useState(false);
  const [renameSpeakerOldName, setRenameSpeakerOldName] = useState('');
  const [renameSpeakerNewName, setRenameSpeakerNewName] = useState('');
  const [segments, setSegmentsState] = useState<SpeakerSegment[]>([]);
  const segmentsSyncRef = useRef(false);
  const newItemUidRef = useRef<string | null>(null);

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  useEffect(() => {
    if (!taskId) return;
    setLoading(true);
    getEditData(taskId)
      .then((d) => {
        setData(d);
        setUserTitle(d.user_title || d.original_title || '');
        setUserHostName(d.user_host_name || d.host_name || '');
        setUserGuestNames(
          d.user_guest_names?.length ? d.user_guest_names
            : d.guest_names?.length ? d.guest_names
            : [''],
        );
        setEditorPreface(d.editor_preface || '');
        const loadedChapters = d.annotated_content?.chapters || [];
        setChapters(loadedChapters);
        const loadedHighlights = d.highlights || { quotes: [] };
        setHighlights(loadedHighlights);

        const allQuotes = (loadedHighlights.quotes || []).filter(
          (q: any) => typeof q === 'object' && q.text,
        );
        const epigraphQuote = allQuotes.find((q: any) => q.placement === 'epigraph');
        if (epigraphQuote) {
          setEpigraphText(epigraphQuote.text || '');
          setEpigraphSpeaker(epigraphQuote.speaker || '');
        }
        const rawQuotes = allQuotes.filter((q: any) => q.placement !== 'epigraph');
        const chOrder = (d.annotated_content?.chapters || []).map((c: any) => c.title || '');
        rawQuotes.sort((a: any, b: any) => {
          const ai = chOrder.indexOf(a.chapter_title);
          const bi = chOrder.indexOf(b.chapter_title);
          const ca = ai < 0 ? chOrder.length : ai;
          const cb = bi < 0 ? chOrder.length : bi;
          if (ca !== cb) return ca - cb;
          return (a.after_paragraph ?? 999) - (b.after_paragraph ?? 999);
        });
        setQuotes(
          rawQuotes.map((q: any, i: number) => ({
            _uid: `q_${i}_${Date.now()}`,
            text: q.text || '',
            speaker: q.speaker || '',
            placement: q.placement || 'inline_card',
            chapter_title: q.chapter_title || '',
            after_paragraph: q.after_paragraph ?? null,
            move_paragraph_dir: 'backward' as const,
            move_paragraph_steps: 0,
          })),
        );

        const preamble = d.annotated_content?.preamble || {};
        setSummaryBullets(
          (preamble.summary_bullets || []).map((b: string) => b.replace(/\*\*/g, '')),
        );

        setCoverStyle(d.cover_style || 'classic');
        setCustomFullCoverUrl(d.custom_full_cover_url || '');
        setCustomBackCoverUrl(d.custom_back_cover_url || '');

        const chapterOrder = loadedChapters.map((c: any) => c.title || '');
        const rawImages = (d.illustrations?.images || []) as EditIllustration[];
        rawImages.sort((a, b) => {
          const ai = chapterOrder.indexOf(a.chapter_title);
          const bi = chapterOrder.indexOf(b.chapter_title);
          const ca = ai < 0 ? chapterOrder.length : ai;
          const cb = bi < 0 ? chapterOrder.length : bi;
          if (ca !== cb) return ca - cb;
          return (a.after_paragraph ?? 999) - (b.after_paragraph ?? 999);
        });
        setIllustrations(
          rawImages.map((img, i) => ({
            ...img,
            _uid: `ill_${i}_${Date.now()}`,
            move_paragraph_dir: 'backward' as const,
            move_paragraph_steps: 0,
          })),
        );
        setIsDirty(false);
      })
      .catch(() => message.error('\u52A0\u8F7D\u7F16\u8F91\u6570\u636E\u5931\u8D25'))
      .finally(() => setLoading(false));
  }, [taskId]);

  useEffect(() => {
    if (segmentsSyncRef.current) {
      segmentsSyncRef.current = false;
      return;
    }
    const chData = chapters[activeChapter];
    if (!chData) { setSegmentsState([]); return; }
    const { body } = parseFootnotes(chData.content || '');
    setSegmentsState(parseBodyToSegments(body));
  }, [activeChapter, chapters]);

  const markDirty = useCallback(() => setIsDirty(true), []);

  const chapterTitles = useMemo(
    () => chapters.map((c: any) => c.title || '\u672A\u547D\u540D\u7AE0\u8282'),
    [chapters],
  );

  const insertSorted = useCallback(
    <T extends { chapter_title: string; after_paragraph: number | null }>(
      list: T[],
      item: T,
    ): T[] => {
      const chOrder = chapters.map((c: any) => c.title || '');
      const itemChIdx = chOrder.indexOf(item.chapter_title);
      const itemCh = itemChIdx < 0 ? chOrder.length : itemChIdx;
      const itemPara = item.after_paragraph ?? 999;
      let pos = list.length;
      for (let i = 0; i < list.length; i++) {
        const ci = chOrder.indexOf(list[i].chapter_title);
        const ch = ci < 0 ? chOrder.length : ci;
        const para = list[i].after_paragraph ?? 999;
        if (ch > itemCh || (ch === itemCh && para > itemPara)) {
          pos = i;
          break;
        }
      }
      const next = [...list];
      next.splice(pos, 0, item);
      return next;
    },
    [chapters],
  );

  useEffect(() => {
    const uid = newItemUidRef.current;
    if (!uid) return;
    const timer = setTimeout(() => {
      const el = document.querySelector(`[data-uid="${uid}"]`);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        (el as HTMLElement).style.transition = 'box-shadow 0.3s';
        (el as HTMLElement).style.boxShadow = '0 0 0 2px var(--accent)';
        setTimeout(() => {
          (el as HTMLElement).style.boxShadow = '';
        }, 1500);
      }
      newItemUidRef.current = null;
    }, 100);
    return () => clearTimeout(timer);
  }, [illustrations, quotes]);

  const bookSpeakerOptions = useMemo(() => {
    const set = new Set<string>();
    for (const ch of chapters) {
      const { body } = parseFootnotes(ch?.content || '');
      for (const seg of parseBodyToSegments(body)) {
        const n = seg.speaker.trim();
        if (n) set.add(n);
      }
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'));
  }, [chapters]);

  const renameSpeakerGlobally = useCallback((from: string, to: string) => {
    const oldName = from.trim();
    const newName = to.trim();
    if (!oldName || !newName) {
      message.warning('\u8BF7\u8F93\u5165\u6709\u6548\u7684\u8BF4\u8BDD\u4EBA\u59D3\u540D');
      return;
    }
    if (oldName === newName) {
      setRenameSpeakerOpen(false);
      return;
    }
    let changedChapters = 0;
    let changedBlocks = 0;
    const nextChapters = chapters.map((ch) => {
      const { body, footnotes } = parseFootnotes(ch.content || '');
      const segs = parseBodyToSegments(body);
      let changed = false;
      const nextSegs = segs.map((seg) => {
        if (seg.speaker.trim() === oldName) {
          changed = true;
          changedBlocks += 1;
          return { ...seg, speaker: newName };
        }
        return seg;
      });
      if (!changed) return ch;
      changedChapters += 1;
      const nextBody = serializeSegmentsToBody(nextSegs);
      return { ...ch, content: rebuildContent(nextBody, footnotes) };
    });
    if (changedBlocks === 0) {
      message.info('\u672A\u627E\u5230\u8BE5\u8BF4\u8BDD\u4EBA');
      return;
    }
    setChapters(nextChapters);
    setUserHostName((h) => (h.trim() === oldName ? newName : h));
    setUserGuestNames((gs) => gs.map((n) => ((n || '').trim() === oldName ? newName : n)));
    markDirty();
    setRenameSpeakerOpen(false);
    message.success(
      `\u5DF2\u5728 ${changedChapters} \u7AE0\u4E2D\u66FF\u6362 ${changedBlocks} \u4E2A\u8BF4\u8BDD\u5757\uFF08\u5168\u4E66\uFF09`,
    );
  }, [chapters, markDirty]);

  // ====== Save & Preview ======

  const buildEditRequest = () => {
    const updatedPreamble = {
      ...(data!.annotated_content?.preamble || {}),
      summary_bullets: summaryBullets.filter((b) => b.trim()),
    };
    const updatedAnnotated = data!.annotated_content
      ? { ...data!.annotated_content, chapters, preamble: updatedPreamble }
      : null;

    const finalIlls: IllustrationUi[] = illustrations.map((ill) => {
      const steps = ill.move_paragraph_steps ?? 0;
      if (steps <= 0) return ill;
      const dir = ill.move_paragraph_dir ?? 'backward';
      const cur = ill.after_paragraph ?? 0;
      const next = dir === 'forward' ? Math.max(0, cur - steps) : cur + steps;
      return { ...ill, after_paragraph: next, move_paragraph_steps: 0 };
    });

    const cleanIlls = finalIlls.map(
      ({ _uid, preview_url, move_paragraph_dir, move_paragraph_steps, ...rest }) => ({
        filename: rest.filename,
        chapter_title: rest.chapter_title,
        after_paragraph: rest.after_paragraph,
        caption: rest.caption,
        size: rest.size,
        type: rest.type,
        source: rest.source,
      }),
    );

    const finalQuotes: QuoteItem[] = quotes.map((q) => {
      const steps = q.move_paragraph_steps ?? 0;
      if (steps <= 0) return q;
      const dir = q.move_paragraph_dir ?? 'backward';
      const cur = q.after_paragraph ?? 0;
      const next = dir === 'forward' ? Math.max(0, cur - steps) : cur + steps;
      return { ...q, after_paragraph: next, move_paragraph_steps: 0 };
    });

    const cleanQuotes = finalQuotes.map(
      ({ _uid, move_paragraph_dir, move_paragraph_steps, ...rest }) => ({
        text: rest.text,
        speaker: rest.speaker || '',
        placement: rest.placement,
        chapter_title: rest.chapter_title,
        after_paragraph: rest.after_paragraph,
      }),
    );

    const allSaveQuotes = [...cleanQuotes];
    if (epigraphText.trim()) {
      allSaveQuotes.push({
        text: epigraphText.trim(),
        speaker: epigraphSpeaker.trim(),
        placement: 'epigraph',
        chapter_title: '',
        after_paragraph: null,
      });
    }
    const updatedHighlights = { ...highlights, quotes: allSaveQuotes };

    const req: EditRequest = {};
    if (updatedAnnotated) req.annotated_content = updatedAnnotated;
    req.highlights = updatedHighlights;
    req.illustrations = { images: cleanIlls };
    req.editor_preface = editorPreface;
    req.user_title = userTitle;
    req.user_host_name = userHostName;
    req.user_guest_names = userGuestNames.filter((n) => n.trim());
    const stripCacheBust = (url: string) => url.split('?')[0];
    if (customFullCoverUrl) {
      req.custom_full_cover_url = stripCacheBust(customFullCoverUrl);
    } else if (customCoverUrl) {
      req.custom_cover_url = stripCacheBust(customCoverUrl);
    } else {
      req.custom_full_cover_url = '';
      if (coverStyle) {
        req.cover_style = coverStyle;
      }
    }
    if (pendingRestoreOriginalCover) {
      req.restore_original_cover = true;
    }
    req.custom_back_cover_url = customBackCoverUrl ? stripCacheBust(customBackCoverUrl) : '';

    return { req, finalIlls, finalQuotes, updatedHighlights };
  };

  const handleSave = async () => {
    if (!taskId || !data) return;
    const saveStart = Date.now();
    analytics.track('edit_save_clicked', { task_id: taskId });
    setSaving(true);
    const { req, finalIlls, finalQuotes, updatedHighlights } = buildEditRequest();
    try {
      const result = await saveAndRegenerate(taskId, req);
      analytics.track('edit_save_succeeded', { task_id: taskId, duration_ms: Date.now() - saveStart });
      message.success('\u6392\u7248\u6210\u529F\uFF0CPDF \u5DF2\u66F4\u65B0');
      setIllustrations(finalIlls);
      setQuotes(finalQuotes);
      setHighlights(updatedHighlights);
      setIsDirty(false);
      setPendingRestoreOriginalCover(false);
      onSaveComplete(result.pdf_url || '');
    } catch (e: any) {
      const detail = e?.response?.data?.detail || '\u4FDD\u5B58\u5931\u8D25\uFF0C\u8BF7\u91CD\u8BD5';
      analytics.track('edit_save_failed', { task_id: taskId, error: detail });
      message.error(detail);
    } finally {
      setSaving(false);
    }
  };

  const handlePreviewRefresh = async () => {
    if (!taskId || !data || !onPreviewRefresh) return;
    setPreviewing(true);
    const { req, finalIlls, finalQuotes, updatedHighlights } = buildEditRequest();
    try {
      const result = await saveAndRegenerate(taskId, req);
      message.success('\u9884\u89C8\u5DF2\u5237\u65B0');
      setIllustrations(finalIlls);
      setQuotes(finalQuotes);
      setHighlights(updatedHighlights);
      setIsDirty(false);
      setPendingRestoreOriginalCover(false);
      onPreviewRefresh(result.pdf_url || '');
    } catch (e: any) {
      const detail = e?.response?.data?.detail || '\u5237\u65B0\u9884\u89C8\u5931\u8D25\uFF0C\u8BF7\u91CD\u8BD5';
      message.error(detail);
    } finally {
      setPreviewing(false);
    }
  };

  // ====== Chapter editing ======

  const updateChapterContent = (index: number, content: string) => {
    const updated = [...chapters];
    updated[index] = { ...updated[index], content };
    setChapters(updated);
    markDirty();
  };

  // ====== Illustration handlers ======

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = illustrations.findIndex((i) => i._uid === active.id);
    const newIndex = illustrations.findIndex((i) => i._uid === over.id);
    setIllustrations(arrayMove(illustrations, oldIndex, newIndex));
    markDirty();
    analytics.track('edit_section_used', { task_id: taskId, section: 'drag_reorder' });
  };

  const deleteIllustration = (uid: string) => {
    setIllustrations((prev) => prev.filter((i) => i._uid !== uid));
    markDirty();
  };

  const editIllustration = (uid: string, field: string, value: any) => {
    setIllustrations((prev) =>
      prev.map((i) => (i._uid === uid ? { ...i, [field]: value } : i)),
    );
    markDirty();
  };

  const triggerReplaceImage = (uid: string) => {
    setReplacingUid(uid);
    replaceInputRef.current?.click();
  };

  const handleReplaceImage = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !taskId || !replacingUid) return;
    setUploading(true);
    try {
      const { filename } = await uploadIllustration(taskId, file);
      setIllustrations((prev) =>
        prev.map((i) =>
          i._uid === replacingUid
            ? { ...i, filename, preview_url: `/files/${taskId}/${filename}` }
            : i,
        ),
      );
      markDirty();
      analytics.track('edit_illustration_action', { task_id: taskId, action: 'replace' });
      message.success('\u56FE\u7247\u5DF2\u66FF\u6362');
    } catch (err) {
      message.error(getApiErrorMessage(err, '\u56FE\u7247\u66FF\u6362\u5931\u8D25\uFF0C\u8BF7\u91CD\u8BD5'));
    } finally {
      setUploading(false);
      setReplacingUid(null);
      if (replaceInputRef.current) replaceInputRef.current.value = '';
    }
  };

  const handleAddIllustration = async () => {
    if (!taskId || !newIllFile) return;
    setUploading(true);
    try {
      const { filename } = await uploadIllustration(taskId, newIllFile);
      const newIll: IllustrationUi = {
        _uid: `ill_new_${Date.now()}`,
        filename,
        preview_url: `/files/${taskId}/${filename}`,
        chapter_title: newIllChapter,
        after_paragraph: newIllParagraph,
        caption: newIllCaption,
        size: 'large',
        type: 'user_upload',
        source: 'user_upload',
        move_paragraph_dir: 'backward',
        move_paragraph_steps: 0,
      };
      newItemUidRef.current = newIll._uid;
      setIllustrations((prev) => insertSorted(prev, newIll));
      setShowAddIllModal(false);
      setNewIllFile(null);
      setNewIllCaption('');
      setNewIllParagraph(0);
      markDirty();
      analytics.track('edit_illustration_action', { task_id: taskId, action: 'add' });
      message.success('\u63D2\u56FE\u6DFB\u52A0\u6210\u529F');
    } catch (err) {
      message.error(getApiErrorMessage(err, '\u63D2\u56FE\u6DFB\u52A0\u5931\u8D25\uFF0C\u8BF7\u91CD\u8BD5'));
    } finally {
      setUploading(false);
    }
  };

  // ====== Quote handlers ======

  const editQuote = (uid: string, field: string, value: any) => {
    setQuotes((prev) => prev.map((q) => (q._uid === uid ? { ...q, [field]: value } : q)));
    markDirty();
  };

  const deleteQuote = (uid: string) => {
    setQuotes((prev) => prev.filter((q) => q._uid !== uid));
    markDirty();
  };

  const handleAddQuote = () => {
    if (!newQuoteText.trim()) return;
    const newQ: QuoteItem = {
      _uid: `q_new_${Date.now()}`,
      text: newQuoteText.trim(),
      speaker: '',
      placement: 'inline_card',
      chapter_title: newQuoteChapter,
      after_paragraph: newQuoteParagraph,
      move_paragraph_dir: 'backward',
      move_paragraph_steps: 0,
    };
    newItemUidRef.current = newQ._uid;
    setQuotes((prev) => insertSorted(prev, newQ));
    setShowAddQuoteModal(false);
    setNewQuoteText('');
    setNewQuoteParagraph(1);
    markDirty();
    analytics.track('edit_section_used', { task_id: taskId, section: 'quote' });
    message.success('\u91D1\u53E5\u6DFB\u52A0\u6210\u529F');
  };

  // ====== Annotation handlers (unified inline editing) ======

  const getAnnotationsForChapter = (chapterIndex: number) => {
    const ch = chapters[chapterIndex];
    if (!ch) return { body: '', footnotes: [] };
    return parseFootnotes(ch.content || '');
  };

  const updateFootnoteInline = (chapterIndex: number, fnId: number, newText: string) => {
    const { body, footnotes } = getAnnotationsForChapter(chapterIndex);
    const updated = footnotes.map((fn) => fn.id === fnId ? { ...fn, text: newText } : fn);
    updateChapterContent(chapterIndex, rebuildContent(body, updated));
  };

  const deleteFootnoteInline = (chapterIndex: number, fnId: number) => {
    const { body, footnotes } = getAnnotationsForChapter(chapterIndex);
    const remaining = footnotes.filter((fn) => fn.id !== fnId);
    let cleanedBody = body.replace(new RegExp(`\\[\\^${fnId}\\](?!:)`, 'g'), '');
    const oldIds = remaining.map((fn) => fn.id);
    const newIds = remaining.map((_, i) => i + 1);
    cleanedBody = renumberFootnotes(cleanedBody, oldIds, newIds);
    const renumbered = remaining.map((fn, i) => ({ ...fn, id: i + 1 }));
    updateChapterContent(chapterIndex, rebuildContent(cleanedBody, renumbered));
  };

  // ====== Cover handlers ======

  const handleCoverImageUpload = async (file: File) => {
    if (!taskId) return;
    setCoverUploading(true);
    try {
      const { cover_url } = await uploadCustomCover(taskId, file);
      setCustomCoverUrl(cover_url + `?t=${Date.now()}`);
      setCustomFullCoverUrl('');
      markDirty();
      analytics.track('edit_cover_changed', { task_id: taskId, action: 'upload_cover' });
      message.success('\u5C01\u9762\u56FE\u7247\u66FF\u6362\u6210\u529F');
    } catch (err) {
      message.error(getApiErrorMessage(err, '\u5C01\u9762\u4E0A\u4F20\u5931\u8D25\uFF0C\u8BF7\u91CD\u8BD5'));
    } finally {
      setCoverUploading(false);
    }
  };

  const handleFullCoverUpload = async (file: File) => {
    if (!taskId) return;
    setFullCoverUploading(true);
    try {
      const { full_cover_url } = await uploadFullCover(taskId, file);
      setCustomFullCoverUrl(full_cover_url + `?t=${Date.now()}`);
      setCustomCoverUrl('');
      setCoverStyle('');
      markDirty();
      analytics.track('edit_cover_changed', { task_id: taskId, action: 'upload_full' });
      message.success('\u6574\u4F53\u5C01\u9762\u4E0A\u4F20\u6210\u529F');
    } catch (err) {
      message.error(getApiErrorMessage(err, '\u6574\u4F53\u5C01\u9762\u4E0A\u4F20\u5931\u8D25\uFF0C\u8BF7\u91CD\u8BD5'));
    } finally {
      setFullCoverUploading(false);
    }
  };

  const handleBackCoverUpload = async (file: File) => {
    if (!taskId) return;
    setBackCoverUploading(true);
    try {
      const { back_cover_url } = await uploadBackCover(taskId, file);
      setCustomBackCoverUrl(back_cover_url + `?t=${Date.now()}`);
      markDirty();
      analytics.track('edit_cover_changed', { task_id: taskId, action: 'upload_back' });
      message.success('\u5C01\u5E95\u4E0A\u4F20\u6210\u529F');
    } catch (err) {
      message.error(getApiErrorMessage(err, '\u5C01\u5E95\u4E0A\u4F20\u5931\u8D25\uFF0C\u8BF7\u91CD\u8BD5'));
    } finally {
      setBackCoverUploading(false);
    }
  };

  const handleCancel = () => {
    analytics.track('edit_cancelled', { task_id: taskId, has_unsaved_changes: isDirty });
    if (isDirty) {
      Modal.confirm({
        title: '\u786E\u8BA4\u53D6\u6D88\u7F16\u8F91\uFF1F',
        content: '\u5C1A\u672A\u4FDD\u5B58\u7684\u4FEE\u6539\u5C06\u4E22\u5931\uFF0C\u786E\u5B9A\u53D6\u6D88\u5417\uFF1F',
        okText: '\u786E\u5B9A\u53D6\u6D88',
        cancelText: '\u7EE7\u7EED\u7F16\u8F91',
        onOk: onCancel,
      });
    } else {
      onCancel();
    }
  };

  // ====== Render ======

  if (loading) {
    return (
      <div className="edit-panel-loading">
        <Spin indicator={<LoadingOutlined style={{ fontSize: 28 }} />} />
        <p>{"\u52A0\u8F7D\u7F16\u8F91\u6570\u636E..."}</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="edit-panel-loading">
        <p>{"\u52A0\u8F7D\u6570\u636E\u5931\u8D25"}</p>
        <Button onClick={onCancel}>{"\u8FD4\u56DE"}</Button>
      </div>
    );
  }

  const tabHeaders = [
    { key: 'settings', label: '\u5168\u5C40\u8BBE\u7F6E' },
    { key: 'content', label: '\u6B63\u6587\u7F16\u8F91' },
    { key: 'illustrations', label: '\u63D2\u56FE\u7BA1\u7406' },
    { key: 'quotes', label: '\u91D1\u53E5\u7BA1\u7406' },
    { key: 'cover', label: '\u5C01\u9762\u7BA1\u7406' },
  ];

  const renderSettingsTab = () => (
        <div className="edit-tab-body">
          <CollapseSection title={"\u4E66\u7C4D\u4FE1\u606F"} defaultOpen>
            <div className="edit-field">
              <label>{"\u4E66\u7C4D\u6807\u9898"}</label>
              <Input
                value={userTitle}
                onChange={(e) => { setUserTitle(e.target.value); markDirty(); }}
                placeholder={data.original_title || '\u8F93\u5165\u6807\u9898'}
              />
              {data.original_title && userTitle !== data.original_title && (
                <span className="edit-field-hint">{"\u539F\u6807\u9898: "}{data.original_title}</span>
              )}
            </div>
            <div className="edit-field">
              <label>{"\u4E3B\u6301\u4EBA\u540D\u79F0"}</label>
              <Input
                value={userHostName}
                onChange={(e) => { setUserHostName(e.target.value); markDirty(); }}
                placeholder={"\u59D3\u540D"}
              />
            </div>
            <div className="edit-field">
              <label>{"\u5609\u5BBE\u540D\u79F0"}</label>
              {userGuestNames.map((name, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                  <Input
                    value={name}
                    onChange={(e) => {
                      const updated = [...userGuestNames];
                      updated[i] = e.target.value;
                      setUserGuestNames(updated);
                      markDirty();
                    }}
                    placeholder={`\u5609\u5BBE ${i + 1}`}
                  />
                  {userGuestNames.length > 1 && (
                    <Button
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      onClick={() => {
                        setUserGuestNames(userGuestNames.filter((_, j) => j !== i));
                        markDirty();
                      }}
                    />
                  )}
                </div>
              ))}
              <Button
                size="small"
                icon={<PlusOutlined />}
                onClick={() => { setUserGuestNames([...userGuestNames, '']); markDirty(); }}
              >
                {"\u6DFB\u52A0\u5609\u5BBE"}
              </Button>
            </div>
          </CollapseSection>

          <CollapseSection title={"\u7F16\u8005\u5E8F"}>
            <div className="edit-field">
              <TextArea
                value={editorPreface}
                onChange={(e) => { setEditorPreface(e.target.value); markDirty(); }}
                placeholder={"\u8F93\u5165\u7F16\u8005\u5E8F"}
                autoSize={{ minRows: 6, maxRows: 20 }}
                maxLength={2000}
                showCount
              />
            </div>
          </CollapseSection>

          <CollapseSection title={"\u7AE0\u8282\u6807\u9898"}>
            {chapters.map((ch: any, i: number) => (
              <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center' }}>
                <span className="edit-chapter-idx">{`\u7B2C${toCnNum(i + 1)}\u7AE0`}</span>
                <Input
                  value={ch.title || ''}
                  onChange={(e) => {
                    const updated = [...chapters];
                    const oldTitle = updated[i].title || '';
                    updated[i] = { ...updated[i], title: e.target.value };
                    setChapters(updated);
                    setIllustrations((prev) =>
                      prev.map((ill) =>
                        ill.chapter_title === oldTitle ? { ...ill, chapter_title: e.target.value } : ill,
                      ),
                    );
                    setQuotes((prev) =>
                      prev.map((q) =>
                        q.chapter_title === oldTitle ? { ...q, chapter_title: e.target.value } : q,
                      ),
                    );
                    markDirty();
                  }}
                  placeholder={`\u7AE0\u8282 ${i + 1}`}
                />
              </div>
            ))}
          </CollapseSection>

          <CollapseSection title={"\u7CBE\u534E\u63D0\u8981"}>
            {summaryBullets.map((bullet, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                <TextArea
                  value={bullet}
                  onChange={(e) => {
                    const updated = [...summaryBullets];
                    updated[i] = e.target.value;
                    setSummaryBullets(updated);
                    markDirty();
                  }}
                  autoSize={{ minRows: 1, maxRows: 4 }}
                  placeholder={`\u63D0\u8981 ${i + 1}`}
                />
                <Button
                  size="small"
                  danger
                  icon={<DeleteOutlined />}
                  onClick={() => {
                    setSummaryBullets(summaryBullets.filter((_, j) => j !== i));
                    markDirty();
                  }}
                />
              </div>
            ))}
            <Button
              size="small"
              icon={<PlusOutlined />}
              onClick={() => { setSummaryBullets([...summaryBullets, '']); markDirty(); }}
            >
              {"\u6DFB\u52A0\u63D0\u8981"}
            </Button>
          </CollapseSection>

          <CollapseSection title={"\u9898\u8BB0"}>
            <span className="edit-field-hint" style={{ marginBottom: 8 }}>{"\u9898\u8BB0\u663E\u793A\u5728\u5C01\u9762\u4E4B\u540E\u3001\u6B63\u6587\u4E4B\u524D\u7684\u72EC\u7ACB\u9875\u9762"}</span>
            <div className="edit-field edit-field-row">
              <span className="edit-field-label-side">{"\u5185\u5BB9"}</span>
              <TextArea
                value={epigraphText}
                onChange={(e) => { setEpigraphText(e.target.value); markDirty(); }}
                placeholder={"\u8F93\u5165\u9898\u8BB0\u5185\u5BB9\uFF0C\u7559\u7A7A\u5219\u4E0D\u751F\u6210\u9898\u8BB0\u9875"}
                autoSize={{ minRows: 2, maxRows: 6 }}
              />
            </div>
            <div className="edit-field edit-field-row">
              <span className="edit-field-label-side">{"\u7F72\u540D"}</span>
              <Input
                value={epigraphSpeaker}
                onChange={(e) => { setEpigraphSpeaker(e.target.value); markDirty(); }}
                placeholder={"\u53EF\u7559\u7A7A"}
              />
            </div>
          </CollapseSection>
        </div>
  );

  const renderContentTab = () => {
        const chData = chapters[activeChapter];
        const parsed = chData ? parseFootnotes(chData.content || '') : { body: '', footnotes: [] };
        const footnotes = parsed.footnotes;

        const openFootnoteById = (id: number) => {
          const fn = footnotes.find((f) => f.id === id);
          if (!fn) {
            message.warning('\u672A\u627E\u5230\u8BE5\u7F16\u53F7\u7684\u6CE8\u91CA\u5B9A\u4E49');
            return;
          }
          setFootnoteQuickEditId(id);
          setFootnoteQuickEditText(fn.text);
        };

        const syncSegments = (nextSegments: SpeakerSegment[]) => {
          setSegmentsState(nextSegments);
          segmentsSyncRef.current = true;
          const nextBody = serializeSegmentsToBody(nextSegments);
          updateChapterContent(activeChapter, rebuildContent(nextBody, footnotes));
        };

        const updateSegmentSpeaker = (segmentId: string, speaker: string) => {
          const nextSegments = segments.map((seg) =>
            seg.id === segmentId ? { ...seg, speaker } : seg);
          syncSegments(nextSegments);
        };

        const updateSegmentText = (segmentId: string, text: string) => {
          const nextSegments = segments.map((seg) =>
            seg.id === segmentId ? { ...seg, text } : seg);
          syncSegments(nextSegments);
        };

        const addSegmentAfter = (afterId: string) => {
          const idx = segments.findIndex((s) => s.id === afterId);
          const insertAt = idx === -1 ? segments.length : idx + 1;
          const next = [...segments];
          next.splice(insertAt, 0, { id: `seg_${Date.now()}`, speaker: '', text: '' });
          setSegmentsState(next);
          markDirty();
        };

        const removeSegment = (segmentId: string) => {
          if (segments.length <= 1) {
            message.info('\u81F3\u5C11\u4FDD\u7559\u4E00\u4E2A\u5757');
            return;
          }
          const nextSegments = segments.filter((seg) => seg.id !== segmentId);
          syncSegments(nextSegments);
          if (focusedSegmentId === segmentId) {
            setFocusedSegmentId('');
            setFocusedCursorPos(0);
          }
        };

        const replaceNext = () => {
          const needle = replaceFrom;
          if (!needle) {
            message.warning('\u8BF7\u8F93\u5165\u8981\u66FF\u6362\u7684\u5185\u5BB9');
            return;
          }
          const startIndex = Math.max(0, segments.findIndex((s) => s.id === focusedSegmentId));
          for (let offset = 0; offset < segments.length; offset += 1) {
            const idx = (startIndex + offset) % segments.length;
            const seg = segments[idx];
            const hit = seg.text.indexOf(needle);
            if (hit >= 0) {
              const newText = `${seg.text.slice(0, hit)}${replaceTo}${seg.text.slice(hit + needle.length)}`;
              updateSegmentText(seg.id, newText);
              const pos = hit + replaceTo.length;
              requestAnimationFrame(() => {
                const ed = segmentInputRefs.current[seg.id];
                if (ed) {
                  ed.focus();
                  ed.setPlainOffset(pos);
                  setFocusedSegmentId(seg.id);
                  setFocusedCursorPos(pos);
                }
              });
              return;
            }
          }
          message.info('\u672A\u627E\u5230\u5339\u914D\u5185\u5BB9');
        };

        const replaceAll = () => {
          const needle = replaceFrom;
          if (!needle) {
            message.warning('\u8BF7\u8F93\u5165\u8981\u66FF\u6362\u7684\u5185\u5BB9');
            return;
          }
          let count = 0;
          const nextSegments = segments.map((seg) => {
            const localCount = seg.text.split(needle).length - 1;
            count += Math.max(0, localCount);
            return localCount > 0 ? { ...seg, text: seg.text.split(needle).join(replaceTo) } : seg;
          });
          if (count <= 0) {
            message.info('\u672A\u627E\u5230\u5339\u914D\u5185\u5BB9');
            return;
          }
          syncSegments(nextSegments);
          analytics.track('edit_section_used', { task_id: taskId, section: 'text_replace', count });
          message.success(`\u5DF2\u66FF\u6362 ${count} \u5904`);
        };

        const insertFootnoteAtCaret = (fnText: string) => {
          const targetId = focusedSegmentId || segments[segments.length - 1]?.id;
          if (!targetId) return;
          const targetSeg = segments.find((seg) => seg.id === targetId);
          if (!targetSeg) return;

          const snap = insertCaretSnapshotRef.current;
          let pos: number;
          if (snap && snap.segmentId === targetId) {
            pos = Math.min(Math.max(0, snap.offset), targetSeg.text.length);
            insertCaretSnapshotRef.current = null;
          } else if (focusedSegmentId === targetId) {
            pos = Math.min(Math.max(0, focusedCursorPos), targetSeg.text.length);
          } else {
            const ed0 = segmentInputRefs.current[targetId];
            pos = ed0
              ? Math.min(Math.max(0, ed0.getPlainOffset()), targetSeg.text.length)
              : Math.min(Math.max(0, focusedCursorPos), targetSeg.text.length);
          }

          const M = maxFootnoteRefIdBeforeBook(chapters, activeChapter, segments, targetId, pos);
          const newId = M + 1;
          const maxId = maxFootnoteIdAcrossBook(chapters, activeChapter, segments, footnotes);

          let nextSegments: SpeakerSegment[];
          let newFootnotes: { id: number; text: string }[];
          let nextCaretOffset: number;

          if (maxId >= newId) {
            const withMarker = segments.map((seg) =>
              seg.id === targetId
                ? {
                    ...seg,
                    text: `${seg.text.slice(0, pos)}${FOOTNOTE_INSERT_MARKER}${seg.text.slice(pos)}`,
                  }
                : seg,
            );
            const shiftedSegs = withMarker.map((seg) => ({
              ...seg,
              text: shiftFootnoteRefsInSegmentText(seg.text, newId, maxId),
            }));
            const shiftedFns = footnotes.map((fn) =>
              fn.id >= newId ? { ...fn, id: fn.id + 1 } : fn,
            );
            const t = shiftedSegs.find((s) => s.id === targetId);
            if (!t) return;
            const mi = t.text.indexOf(FOOTNOTE_INSERT_MARKER);
            if (mi < 0) return;
            const ins = `[^${newId}]`;
            const mergedText = `${t.text.slice(0, mi)}${ins}${t.text.slice(mi + FOOTNOTE_INSERT_MARKER.length)}`;
            nextCaretOffset = mi + ins.length;
            nextSegments = shiftedSegs.map((seg) =>
              seg.id === targetId ? { ...seg, text: mergedText } : seg,
            );
            newFootnotes = [...shiftedFns, { id: newId, text: fnText }];
          } else {
            const ins = `[^${newId}]`;
            const newText = `${targetSeg.text.slice(0, pos)}${ins}${targetSeg.text.slice(pos)}`;
            nextCaretOffset = pos + ins.length;
            nextSegments = segments.map((seg) =>
              seg.id === targetId ? { ...seg, text: newText } : seg,
            );
            newFootnotes = [...footnotes, { id: newId, text: fnText }];
          }

          const nextBody = serializeSegmentsToBody(nextSegments);
          setSegmentsState(nextSegments);
          segmentsSyncRef.current = true;

          if (maxId >= newId) {
            const updatedChapters = chapters.map((ch, ci) => {
              if (ci === activeChapter) {
                return { ...ch, content: rebuildContent(nextBody, newFootnotes) };
              }
              const { body, footnotes: fns } = parseFootnotes(ch.content || '');
              const newBody = shiftFootnoteRefsInSegmentText(body, newId, maxId);
              const newFns = fns.map((fn) =>
                fn.id >= newId ? { ...fn, id: fn.id + 1 } : fn,
              );
              return { ...ch, content: rebuildContent(newBody, newFns) };
            });
            setChapters(updatedChapters);
            markDirty();
          } else {
            updateChapterContent(activeChapter, rebuildContent(nextBody, newFootnotes));
          }

          const nextPos = nextCaretOffset;
          requestAnimationFrame(() => {
            const ed2 = segmentInputRefs.current[targetId];
            if (ed2) {
              ed2.focus();
              ed2.setPlainOffset(nextPos);
              setFocusedSegmentId(targetId);
              setFocusedCursorPos(nextPos);
            }
          });
        };

        return (
          <div className="edit-tab-body">
            <div className="edit-content-header">
              <Select
                value={activeChapter}
                onChange={(v) => { setActiveChapter(v); }}
                size="small"
                style={{ flex: 1 }}
                popupClassName="edit-chapter-select-popup"
                options={chapters.map((c: any, i: number) => ({
                  label: `\u7B2C${toCnNum(i + 1)}\u7AE0: ${c.title || '\u672A\u547D\u540D'}`,
                  value: i,
                }))}
              />
              <Button size="small" icon={<SwapRightOutlined />} onClick={() => setShowReplaceModal(true)}>{"\u66FF\u6362\u6587\u5B57"}</Button>
              <Button
                size="small"
                icon={<EditOutlined />}
                style={{ marginLeft: 8 }}
                onMouseDown={() => {
                  const sid = focusedSegmentId || segments[segments.length - 1]?.id;
                  if (!sid) return;
                  const seg = segments.find((s) => s.id === sid);
                  const ed = segmentInputRefs.current[sid];
                  if (seg && ed) {
                    insertCaretSnapshotRef.current = {
                      segmentId: sid,
                      offset: Math.min(Math.max(0, ed.getPlainOffset()), seg.text.length),
                    };
                  }
                }}
                onClick={() => {
                  setInsertFnText('');
                  setShowInsertFnModal(true);
                }}
              >{"\u63D2\u5165\u6CE8\u91CA"}</Button>
            </div>
            <p className="edit-format-hint" style={{ margin: '8px 0' }}>
              {"\u8BF4\u8BDD\u4EBA\u4E0E\u6B63\u6587\u5206\u5F00\u7F16\u8F91\uFF1B\u6B63\u6587\u4E2D\u4EE5\u7EA2\u8272\u6807\u7B7E\u5F62\u5F0F\u663E\u793A\u6CE8\u91CA\u5F15\u7528\u3002\u70B9\u51FB\u6807\u7B7E\u53EF\u7F16\u8F91\u5BF9\u5E94\u6CE8\u91CA\uFF1B\u4E5F\u53EF\u5728\u4E0B\u65B9\u6CE8\u91CA\u5217\u8868\u5904\u7F16\u8F91"}
            </p>
            {chData && (
              <div className="edit-content-preview edit-content-single-editor">
                <div className="edit-speaker-segments">
                  {segments.map((seg) => {
                    const mergedSpeakerList = (() => {
                      const set = new Set(bookSpeakerOptions);
                      const cur = seg.speaker.trim();
                      if (cur) set.add(cur);
                      return Array.from(set).sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'));
                    })();
                    return (
                    <div key={seg.id} className="edit-speaker-segment-row">
                      <div className="edit-speaker-row">
                        <Select
                          value={seg.speaker || undefined}
                          allowClear
                          showSearch
                          placeholder={"\u9009\u62E9\u8BF4\u8BDD\u4EBA"}
                          size="small"
                          style={{ minWidth: 160, flex: 1 }}
                          optionFilterProp="value"
                          filterOption={(input, option) =>
                            String(option?.value ?? '')
                              .toLowerCase()
                              .includes(input.trim().toLowerCase())}
                          options={mergedSpeakerList.map((name) => ({
                            value: name,
                            label: name,
                          }))}
                          optionLabelProp="label"
                          optionRender={(oriOption) => {
                            const name = String((oriOption.data as { value?: string })?.value ?? '');
                            return (
                              <div className="edit-speaker-option-row">
                                <span className="edit-speaker-option-name">{name}</span>
                                <Button
                                  type="link"
                                  size="small"
                                  className="edit-speaker-rename-btn"
                                  icon={<EditOutlined />}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setRenameSpeakerOldName(name);
                                    setRenameSpeakerNewName(name);
                                    setRenameSpeakerOpen(true);
                                  }}
                                  onMouseDown={(e) => e.stopPropagation()}
                                >
                                  {"\u4FEE\u6539"}
                                </Button>
                              </div>
                            );
                          }}
                          onChange={(v) => updateSegmentSpeaker(seg.id, (v as string) || '')}
                          dropdownRender={(menu) => (
                            <div>
                              {menu}
                              <div className="edit-speaker-add-row">
                                <Input
                                  size="small"
                                  value={customSpeakerDraft}
                                  onChange={(e) => setCustomSpeakerDraft(e.target.value)}
                                  placeholder={"\u65B0\u589E\u8BF4\u8BDD\u4EBA"}
                                />
                                <Button
                                  size="small"
                                  type="link"
                                  onClick={() => {
                                    const name = customSpeakerDraft.trim();
                                    if (!name) return;
                                    updateSegmentSpeaker(seg.id, name);
                                    setCustomSpeakerDraft('');
                                  }}
                                >
                                  {"\u6DFB\u52A0"}
                                </Button>
                              </div>
                            </div>
                          )}
                        />
                        <Button
                          size="small"
                          icon={<PlusOutlined />}
                          onClick={() => addSegmentAfter(seg.id)}
                        >
                          {"\u5728\u4E0B\u65B9\u65B0\u589E\u5757"}
                        </Button>
                        <Button
                          size="small"
                          danger
                          icon={<DeleteOutlined />}
                          onClick={() => removeSegment(seg.id)}
                        >
                          {"\u5220\u9664\u5757"}
                        </Button>
                      </div>
                      <SegmentBodyEditor
                        ref={(inst) => {
                          segmentInputRefs.current[seg.id] = inst;
                        }}
                        value={seg.text}
                        onChange={(v) => updateSegmentText(seg.id, v)}
                        onFocus={() => setFocusedSegmentId(seg.id)}
                        onCursorOffsetChange={(o) => setFocusedCursorPos(o)}
                        onFootnoteClick={openFootnoteById}
                        className="edit-main-textarea edit-segment-single-body edit-segment-rich-body"
                        minRows={2}
                      />
                    </div>
                  );
                  })}
                </div>
                {footnotes.length > 0 && (
                  <div className="edit-fn-list">
                    <div className="edit-fn-list-title">
                      {"\u6CE8\u91CA\u5217\u8868\uFF08\u53EF\u76F4\u63A5\u7F16\u8F91\uFF09"}
                    </div>
                    {footnotes.map((fn) => (
                      <div key={fn.id} className="edit-fn-list-item">
                        <Tag color="processing" style={{ fontSize: 10, flexShrink: 0 }}>{fn.id}</Tag>
                        <TextArea
                          value={fn.text}
                          onChange={(e) => updateFootnoteInline(activeChapter, fn.id, e.target.value)}
                          className="edit-fn-list-input"
                          autoSize={{ minRows: 1, maxRows: 8 }}
                        />
                        <Popconfirm
                          title={"\u786E\u5B9A\u5220\u9664\u6B64\u6CE8\u91CA\uFF1F"}
                          onConfirm={() => deleteFootnoteInline(activeChapter, fn.id)}
                          okText={"\u786E\u5B9A"}
                          cancelText={"\u53D6\u6D88"}
                        >
                          <Button size="small" danger icon={<DeleteOutlined />} />
                        </Popconfirm>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
            <Modal
              title={"\u63D2\u5165\u6CE8\u91CA"}
              open={showInsertFnModal}
              onCancel={() => {
                insertCaretSnapshotRef.current = null;
                setShowInsertFnModal(false);
              }}
              onOk={() => {
                if (!insertFnText.trim()) { message.warning('\u8BF7\u8F93\u5165\u6CE8\u91CA\u5185\u5BB9'); return; }
                insertFootnoteAtCaret(insertFnText.trim());
                setShowInsertFnModal(false);
                message.success('\u6CE8\u91CA\u5DF2\u63D2\u5165');
              }}
              okText={"\u786E\u5B9A"}
              cancelText={"\u53D6\u6D88"}
            >
              <div className="edit-field">
                <label>{"\u6CE8\u91CA\u5185\u5BB9"}</label>
                <TextArea
                  value={insertFnText}
                  onChange={(e) => setInsertFnText(e.target.value)}
                  placeholder={"\u8BF7\u8F93\u5165\u6CE8\u91CA\u5185\u5BB9"}
                  autoSize={{ minRows: 2, maxRows: 6 }}
                />
              </div>
            </Modal>
            <Modal
              title={"\u66FF\u6362"}
              open={showReplaceModal}
              onCancel={() => setShowReplaceModal(false)}
              footer={null}
            >
              <div className="edit-field">
                <label>{"\u67E5\u627E\u5185\u5BB9"}</label>
                <Input
                  value={replaceFrom}
                  onChange={(e) => setReplaceFrom(e.target.value)}
                  placeholder={"\u8F93\u5165\u8981\u66FF\u6362\u7684\u6587\u5B57"}
                />
              </div>
              <div className="edit-field">
                <label>{"\u66FF\u6362\u4E3A"}</label>
                <Input
                  value={replaceTo}
                  onChange={(e) => setReplaceTo(e.target.value)}
                  placeholder={"\u8F93\u5165\u66FF\u6362\u540E\u7684\u6587\u5B57"}
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 8 }}>
                <Button onClick={replaceNext}>{"\u66FF\u6362\u4E0B\u4E00\u4E2A"}</Button>
                <Button type="primary" onClick={replaceAll}>{"\u5168\u90E8\u66FF\u6362"}</Button>
              </div>
            </Modal>
          </div>
        );
  };

  const renderIllustrationsTab = () => (
        <div className="edit-tab-body">
          <input
            ref={replaceInputRef}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            style={{ display: 'none' }}
            onChange={handleReplaceImage}
          />
          <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Button
              size="small"
              icon={<PlusOutlined />}
              onClick={() => {
                setNewIllChapter(chapterTitles[0] || '');
                setShowAddIllModal(true);
              }}
            >
              {"\u6DFB\u52A0\u63D2\u56FE"}
            </Button>
            {illustrations.length > 0 && (
              <span style={{ fontSize: 12, color: '#999' }}>{`\u5171 ${illustrations.length} \u5F20`}</span>
            )}
          </div>
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
            <SortableContext items={illustrations.map((i) => i._uid)} strategy={verticalListSortingStrategy}>
              {illustrations.map((ill, idx) => {
                const prevChapter = idx > 0 ? illustrations[idx - 1].chapter_title : null;
                const showHeader = ill.chapter_title !== prevChapter;
                return (
                  <div key={ill._uid} data-uid={ill._uid}>
                    {showHeader && (
                      <div className="edit-ill-group-title">
                        {chapterTitles.includes(ill.chapter_title) ? ill.chapter_title : '\u5176\u4ED6'}
                      </div>
                    )}
                    <SortableIllustrationCard
                      ill={ill}
                      index={idx}
                      chapters={chapterTitles}
                      onDelete={() => deleteIllustration(ill._uid)}
                      onEdit={(field, value) => editIllustration(ill._uid, field, value)}
                      onReplace={() => triggerReplaceImage(ill._uid)}
                    />
                  </div>
                );
              })}
            </SortableContext>
          </DndContext>
          {illustrations.length === 0 && (
            <div style={{ textAlign: 'center', padding: 24, color: '#999', fontSize: 13 }}>{"\u6682\u65E0\u63D2\u56FE"}</div>
          )}
          <Modal
            title={"\u6DFB\u52A0\u63D2\u56FE"}
            open={showAddIllModal}
            onCancel={() => setShowAddIllModal(false)}
            onOk={handleAddIllustration}
            confirmLoading={uploading}
            okText={"\u786E\u5B9A"}
            cancelText={"\u53D6\u6D88"}
            okButtonProps={{ disabled: !newIllFile }}
          >
            <div className="edit-field">
              <label>{"\u9009\u62E9\u56FE\u7247"}</label>
              <Upload
                beforeUpload={(file) => { setNewIllFile(file); return false; }}
                maxCount={1}
                accept="image/jpeg,image/png,image/webp"
                fileList={newIllFile ? [{ uid: '1', name: newIllFile.name, status: 'done' } as any] : []}
                onRemove={() => setNewIllFile(null)}
              >
                <Button icon={<UploadOutlined />}>{"\u9009\u62E9\u6587\u4EF6"}</Button>
              </Upload>
            </div>
            <div className="edit-field">
              <label>{"\u6240\u5C5E\u7AE0\u8282"}</label>
              <Select
                value={newIllChapter}
                onChange={setNewIllChapter}
                popupClassName="edit-chapter-select-popup"
                options={chapterTitles.map((c, i) => ({ label: `\u7B2C${toCnNum(i + 1)}\u7AE0: ${c}`, value: c }))}
                style={{ width: '100%' }}
              />
            </div>
            <div className="edit-field">
              <label>{"\u63D2\u5165\u5230\u7B2C\u51E0\u6BB5\u4E4B\u540E"}</label>
              <Input
                type="number"
                min={1}
                value={newIllParagraph}
                onChange={(e) => setNewIllParagraph(Math.max(1, parseInt(e.target.value, 10) || 1))}
              />
            </div>
            <div className="edit-field">
              <label>{"\u56FE\u6CE8"}</label>
              <Input
                value={newIllCaption}
                onChange={(e) => setNewIllCaption(e.target.value)}
                placeholder={"\u9009\u586B"}
              />
            </div>
          </Modal>
        </div>
  );

  const renderQuotesTab = () => (
        <div className="edit-tab-body">
          <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Button
              size="small"
              icon={<PlusOutlined />}
              onClick={() => {
                setNewQuoteChapter(chapterTitles[0] || '');
                setNewQuoteText('');
                setNewQuoteParagraph(1);
                setShowAddQuoteModal(true);
              }}
            >
              {"\u6DFB\u52A0\u91D1\u53E5"}
            </Button>
          </div>
          {quotes.map((q, idx) => {
            const prevChapter = idx > 0 ? quotes[idx - 1].chapter_title : null;
            const showHeader = q.chapter_title !== prevChapter;
            return (
              <div key={q._uid} data-uid={q._uid}>
                {showHeader && (
                  <div className="edit-ill-group-title">
                    {chapterTitles.includes(q.chapter_title) ? q.chapter_title : '\u5176\u4ED6'}
                  </div>
                )}
                <QuoteCard
                  quote={q}
                  chapters={chapterTitles}
                  onEdit={(field, value) => editQuote(q._uid, field, value)}
                  onDelete={() => deleteQuote(q._uid)}
                />
              </div>
            );
          })}
          {quotes.length === 0 && (
            <div style={{ textAlign: 'center', padding: 24, color: '#999', fontSize: 13 }}>{"\u6682\u65E0\u91D1\u53E5"}</div>
          )}
          <Modal
            title={"\u6DFB\u52A0\u91D1\u53E5"}
            open={showAddQuoteModal}
            onCancel={() => setShowAddQuoteModal(false)}
            onOk={handleAddQuote}
            okText={"\u786E\u5B9A"}
            cancelText={"\u53D6\u6D88"}
            okButtonProps={{ disabled: !newQuoteText.trim() }}
          >
            <div className="edit-field">
              <label>{"\u91D1\u53E5\u5185\u5BB9"}</label>
              <TextArea
                value={newQuoteText}
                onChange={(e) => setNewQuoteText(e.target.value)}
                autoSize={{ minRows: 2, maxRows: 6 }}
                placeholder={"\u8F93\u5165\u91D1\u53E5\u6587\u672C"}
              />
            </div>
            <div className="edit-field">
              <label>{"\u6240\u5C5E\u7AE0\u8282"}</label>
              <Select
                value={newQuoteChapter}
                onChange={setNewQuoteChapter}
                popupClassName="edit-chapter-select-popup"
                options={chapterTitles.map((c, i) => ({ label: `\u7B2C${toCnNum(i + 1)}\u7AE0: ${c}`, value: c }))}
                style={{ width: '100%' }}
              />
            </div>
            <div className="edit-field">
              <label>{"\u63D2\u5165\u5230\u7B2C\u51E0\u6BB5\u4E4B\u540E"}</label>
              <Input
                type="number"
                min={1}
                value={newQuoteParagraph}
                onChange={(e) => setNewQuoteParagraph(Math.max(1, parseInt(e.target.value, 10) || 1))}
              />
            </div>
          </Modal>
        </div>
  );

  const renderCoverTab = () => {
    const hasCustomFront = !!customFullCoverUrl;
    const hasCustomBack = !!customBackCoverUrl;
    const hasReplacedImage = !!customCoverUrl;
    const customHint = hasCustomFront && hasCustomBack
      ? "\u5F53\u524D\u4F7F\u7528\u81EA\u5B9A\u4E49\u5C01\u9762\u548C\u5C01\u5E95"
      : hasCustomFront
        ? "\u5F53\u524D\u4F7F\u7528\u81EA\u5B9A\u4E49\u5C01\u9762"
        : hasCustomBack
          ? "\u5F53\u524D\u4F7F\u7528\u81EA\u5B9A\u4E49\u5C01\u5E95"
          : hasReplacedImage
            ? "\u5F53\u524D\u4F7F\u7528\u81EA\u5B9A\u4E49\u5C01\u9762\u56FE\u7247"
            : '';

    return (
        <div className="edit-tab-body">
          {customHint && (
            <div className="edit-cover-active-hint">
              <span>{customHint}</span>
              <Button size="small" type="link" danger onClick={() => {
                setCustomFullCoverUrl('');
                setCustomBackCoverUrl('');
                setCustomCoverUrl('');
                setPendingRestoreOriginalCover(true);
                setCoverStyle(data.cover_style || 'classic');
                markDirty();
              }}>
                {"\u6062\u590D\u6A21\u677F\u5C01\u9762"}
              </Button>
            </div>
          )}

          <div className="edit-cover-section">
            <div className="edit-cover-collapse-header" onClick={() => setCoverGridOpen(v => !v)}>
              <span className="edit-cover-collapse-arrow">
                {coverGridOpen ? <DownOutlined /> : <RightOutlined />}
              </span>
              <span className="edit-cover-collapse-title">{"\u5C01\u9762\u6A21\u677F"}</span>
              {!coverGridOpen && !customFullCoverUrl && (
                <span className="edit-cover-collapse-current">
                  {(data.available_cover_styles || []).find(s => s.id === coverStyle)?.name || coverStyle}
                </span>
              )}
            </div>
            {coverGridOpen && (
              <div className="edit-cover-grid">
                {(data.available_cover_styles || []).map((s) => (
                  <div
                    key={s.id}
                    className={`edit-cover-item ${!customFullCoverUrl && coverStyle === s.id ? 'active' : ''}`}
                    onClick={() => { setCoverStyle(s.id); setCustomCoverUrl(''); setCustomFullCoverUrl(''); setPendingRestoreOriginalCover(true); markDirty(); analytics.track('edit_cover_changed', { task_id: taskId, action: 'template_switch', style: s.id }); }}
                  >
                    <div className="edit-cover-thumb">
                      {s.preview_url ? (
                        <img
                          src={s.preview_url}
                          alt={s.name}
                          className="edit-cover-thumb-img"
                          loading="lazy"
                        />
                      ) : (
                        <div className="edit-cover-thumb-inner">{s.name}</div>
                      )}
                    </div>
                    <span className="edit-cover-label">{s.name}</span>
                    {!customFullCoverUrl && coverStyle === s.id && <CheckOutlined className="edit-cover-check" />}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="edit-cover-upload-section">
            <h4>{"\u66FF\u6362\u5C01\u9762\u56FE\u7247"}</h4>
            <p className="edit-cover-upload-hint">{"\u4FDD\u7559\u5F53\u524D\u6A21\u677F\u8BBE\u8BA1\uFF0C\u4EC5\u66FF\u6362\u5C01\u9762\u4E2D\u7684\u56FE\u7247"}</p>
            <Upload.Dragger
              beforeUpload={(file) => { handleCoverImageUpload(file); return false; }}
              accept="image/jpeg,image/png,image/webp"
              showUploadList={false}
            >
              {coverUploading ? (
                <Spin />
              ) : customCoverUrl ? (
                <div>
                  <img src={customCoverUrl} alt={"\u5C01\u9762\u56FE\u7247"} style={{ maxHeight: 120, objectFit: 'contain' }} />
                  <p style={{ marginTop: 8, color: '#666', fontSize: 12 }}>{"\u5DF2\u66FF\u6362\uFF0C\u70B9\u51FB\u91CD\u65B0\u4E0A\u4F20"}</p>
                </div>
              ) : (
                <div>
                  <p><UploadOutlined style={{ fontSize: 24, color: '#999' }} /></p>
                  <p style={{ fontSize: 13 }}>{"\u70B9\u51FB\u6216\u62D6\u62FD\u4E0A\u4F20\u56FE\u7247"}</p>
                </div>
              )}
            </Upload.Dragger>
          </div>

          <div className="edit-cover-upload-section" style={{ marginTop: 20 }}>
            <h4>{"\u81EA\u5B9A\u4E49\u5C01\u9762"}</h4>
            <p className="edit-cover-upload-hint">
              {"\u8DF3\u8FC7\u6A21\u677F\uFF0C\u76F4\u63A5\u4F7F\u7528\u60A8\u7684\u56FE\u7247\u4F5C\u4E3A\u5C01\u9762\u6216\u5C01\u5E95\u3002\u5EFA\u8BAE\u4F7F\u7528 "}
              <strong>{"1056 \u00D7 1482 px"}</strong>
              {" \u6216\u540C\u6BD4\u4F8B\u56FE\u7247\uFF08B5 \u5C3A\u5BF8 176 \u00D7 250 mm\uFF09\u3002"}
            </p>
            <div style={{ display: 'flex', gap: 16 }}>
              <div style={{ flex: 1 }}>
                <Upload.Dragger
                  beforeUpload={(file) => { handleFullCoverUpload(file); return false; }}
                  accept="image/jpeg,image/png,image/webp"
                  showUploadList={false}
                >
                  {fullCoverUploading ? (
                    <Spin />
                  ) : customFullCoverUrl ? (
                    <div>
                      <img src={customFullCoverUrl} alt={"\u5C01\u9762"} style={{ maxHeight: 140, objectFit: 'contain' }} />
                      <p style={{ marginTop: 8, color: '#666', fontSize: 12 }}>{"\u70B9\u51FB\u91CD\u65B0\u4E0A\u4F20"}</p>
                    </div>
                  ) : (
                    <div>
                      <p><UploadOutlined style={{ fontSize: 24, color: '#999' }} /></p>
                      <p style={{ fontSize: 13 }}>{"\u4E0A\u4F20\u5C01\u9762"}</p>
                    </div>
                  )}
                </Upload.Dragger>
              </div>
              <div style={{ flex: 1 }}>
                <Upload.Dragger
                  beforeUpload={(file) => { handleBackCoverUpload(file); return false; }}
                  accept="image/jpeg,image/png,image/webp"
                  showUploadList={false}
                >
                  {backCoverUploading ? (
                    <Spin />
                  ) : customBackCoverUrl ? (
                    <div>
                      <img src={customBackCoverUrl} alt={"\u5C01\u5E95"} style={{ maxHeight: 140, objectFit: 'contain' }} />
                      <p style={{ marginTop: 8, color: '#666', fontSize: 12 }}>{"\u70B9\u51FB\u91CD\u65B0\u4E0A\u4F20"}</p>
                    </div>
                  ) : (
                    <div>
                      <p><UploadOutlined style={{ fontSize: 24, color: '#999' }} /></p>
                      <p style={{ fontSize: 13 }}>{"\u4E0A\u4F20\u5C01\u5E95"}</p>
                    </div>
                  )}
                </Upload.Dragger>
              </div>
            </div>
          </div>

          <div className="edit-cover-coming-soon">
            <div className="edit-cover-coming-soon-text">
              <span className="edit-cover-coming-soon-title">{"EchoPress \u7B7E\u7EA6\u8BBE\u8BA1\u5E08\u5B9A\u5236"}</span>
              <span className="edit-cover-coming-soon-desc">{"\u7531\u4E13\u4E1A\u8BBE\u8BA1\u5E08\u4E3A\u60A8\u5B9A\u5236\u72EC\u7279\u5C01\u9762"}</span>
            </div>
            <span className="edit-cover-coming-soon-badge">{"\u5373\u5C06\u63A8\u51FA"}</span>
          </div>
        </div>
    );
  };

  return (
    <div className="edit-panel">
      <div className="edit-panel-header">
        <div className="edit-panel-header-left">
          <h3 className="edit-panel-title serif">{"\u7F16\u8F91\u4E66\u7C4D"}</h3>
          {isDirty && <span className="edit-dirty-badge">{"\u6709\u672A\u4FDD\u5B58\u7684\u4FEE\u6539"}</span>}
        </div>
        <div className="edit-panel-header-right">
          <Button size="small" onClick={handleCancel}>{"\u53D6\u6D88"}</Button>
          <Button
            size="small"
            type="primary"
            icon={saving ? <LoadingOutlined /> : <SaveOutlined />}
            onClick={handleSave}
            loading={saving}
            disabled={saving || previewing}
          >
            {saving ? '\u6392\u7248\u4E2D...' : '\u91CD\u6392\u7248\u5E76\u4FDD\u5B58'}
          </Button>
        </div>
      </div>

      <div className="edit-panel-content">
        <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabHeaders} size="small" />
        {activeTab === 'settings' && renderSettingsTab()}
        {activeTab === 'content' && renderContentTab()}
        {activeTab === 'illustrations' && renderIllustrationsTab()}
        {activeTab === 'quotes' && renderQuotesTab()}
        {activeTab === 'cover' && renderCoverTab()}
      </div>

      <Modal
        title={
          footnoteQuickEditId !== null
            ? `\u7F16\u8F91\u6CE8\u91CA ${footnoteQuickEditId}`
            : ''
        }
        open={footnoteQuickEditId !== null}
        onOk={() => {
          if (footnoteQuickEditId !== null) {
            updateFootnoteInline(activeChapter, footnoteQuickEditId, footnoteQuickEditText);
          }
          setFootnoteQuickEditId(null);
        }}
        onCancel={() => setFootnoteQuickEditId(null)}
        okText={"\u786E\u5B9A"}
        cancelText={"\u53D6\u6D88"}
        destroyOnClose
      >
        <TextArea
          value={footnoteQuickEditText}
          onChange={(e) => setFootnoteQuickEditText(e.target.value)}
          rows={5}
          placeholder={"\u6CE8\u91CA\u5185\u5BB9"}
        />
      </Modal>

      <Modal
        title={"\u91CD\u547D\u540D\u8BF4\u8BDD\u4EBA\uFF08\u5168\u4E66\uFF09"}
        open={renameSpeakerOpen}
        onOk={() => renameSpeakerGlobally(renameSpeakerOldName, renameSpeakerNewName)}
        onCancel={() => setRenameSpeakerOpen(false)}
        okText={"\u786E\u5B9A"}
        cancelText={"\u53D6\u6D88"}
        destroyOnClose
      >
        <p style={{ marginBottom: 8, color: 'var(--text-secondary, #666)', fontSize: 13 }}>
          {"\u5C06\u6240\u6709\u7AE0\u8282\u4E2D\u8BE5\u8BF4\u8BDD\u4EBA\u7684\u6B63\u6587\u5757\u4E00\u5E76\u66FF\u6362\u4E3A\u65B0\u59D3\u540D\uFF08\u542B\u5FEB\u6377\u8BBE\u7F6E\u91CC\u7684\u4E3B\u6301/\u5609\u5BBE\u59D3\u540D\u82E5\u4E0E\u4E4B\u76F8\u540C\u4E5F\u4F1A\u540C\u6B65\uFF09\u3002"}
        </p>
        <div style={{ marginBottom: 10 }}>
          <span style={{ color: '#888', marginRight: 8 }}>{"\u539F\u59D3\u540D"}</span>
          <strong>{renameSpeakerOldName}</strong>
        </div>
        <Input
          value={renameSpeakerNewName}
          onChange={(e) => setRenameSpeakerNewName(e.target.value)}
          placeholder={"\u8F93\u5165\u65B0\u59D3\u540D"}
          onPressEnter={() => renameSpeakerGlobally(renameSpeakerOldName, renameSpeakerNewName)}
        />
      </Modal>

      <Modal
        open={saving}
        closable={false}
        footer={null}
        centered
        maskClosable={false}
        width={340}
      >
        <div style={{ textAlign: 'center', padding: '20px 0' }}>
          <Spin indicator={<LoadingOutlined style={{ fontSize: 36 }} />} />
          <p style={{ marginTop: 16, fontSize: 15, fontWeight: 500 }}>{"\u6B63\u5728\u91CD\u65B0\u6392\u7248 PDF..."}</p>
          <p style={{ color: '#999', fontSize: 13 }}>{"\u9884\u8BA1\u9700\u8981 10-30 \u79D2\uFF0C\u8BF7\u7A0D\u5019"}</p>
        </div>
      </Modal>
    </div>
  );
}
