import type { ClipboardEvent, CSSProperties, KeyboardEvent } from 'react';
import {
  forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef,
} from 'react';

/** 可编辑段落体对外暴露的命令式句柄：支持聚焦、脚注 [^id] 光标定位与选区设置 */
export type SegmentBodyEditorHandle = {
  focus: () => void;
  getPlainOffset: () => number;
  setPlainOffset: (offset: number) => void;
  setSelectionRange: (start: number, end: number) => void;
};

function serializeRoot(root: HTMLElement): string {
  let out = '';
  for (const node of Array.from(root.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE) {
      out += node.textContent ?? '';
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      const el = node as HTMLElement;
      if (el.classList.contains('edit-fn-chip')) {
        const id = el.dataset.fnId ?? '';
        out += `[^${id}]`;
      } else if (el.tagName === 'BR') {
        out += '\n';
      } else {
        out += serializeRoot(el);
      }
    }
  }
  return out;
}

function appendTextLines(el: HTMLElement, chunk: string) {
  const lines = chunk.split('\n');
  lines.forEach((line, i) => {
    if (i > 0) el.appendChild(document.createElement('br'));
    el.appendChild(document.createTextNode(line));
  });
}

function appendChip(el: HTMLElement, idStr: string, onFootnoteMouseDown: (id: number) => void) {
  const span = document.createElement('span');
  span.className = 'edit-fn-chip';
  span.contentEditable = 'false';
  span.dataset.fnId = idStr;
  span.textContent = '[\u6CE8\u91CA' + idStr + ']';
  span.addEventListener('mousedown', (e) => {
    e.preventDefault();
    e.stopPropagation();
    onFootnoteMouseDown(parseInt(idStr, 10));
  });
  el.appendChild(span);
}

function hydrateRoot(
  el: HTMLElement,
  text: string,
  onFootnoteMouseDown: (id: number) => void,
) {
  el.replaceChildren();
  const re = /\[\^(\d+)\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    appendTextLines(el, text.slice(last, m.index));
    appendChip(el, m[1], onFootnoteMouseDown);
    last = m.index + m[0].length;
  }
  appendTextLines(el, text.slice(last));
}

function getPlainOffsetsFromSelection(root: HTMLElement): { start: number; end: number } {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) return { start: 0, end: 0 };
  const range = sel.getRangeAt(0);
  const rStart = document.createRange();
  rStart.selectNodeContents(root);
  rStart.setEnd(range.startContainer, range.startOffset);
  const d1 = document.createElement('div');
  d1.appendChild(rStart.cloneContents());
  const startStr = serializeRoot(d1);

  const rEnd = document.createRange();
  rEnd.selectNodeContents(root);
  rEnd.setEnd(range.endContainer, range.endOffset);
  const d2 = document.createElement('div');
  d2.appendChild(rEnd.cloneContents());
  const endStr = serializeRoot(d2);

  return { start: startStr.length, end: endStr.length };
}

function pointAtPlainOffset(root: HTMLElement, offset: number): { node: Node; offset: number } {
  const total = serializeRoot(root).length;
  const o = Math.min(Math.max(0, offset), total);
  let pos = 0;
  const children = Array.from(root.childNodes);
  for (let i = 0; i < children.length; i++) {
    const node = children[i];
    if (node.nodeType === Node.TEXT_NODE) {
      const len = (node.textContent || '').length;
      if (o <= pos + len) return { node, offset: o - pos };
      pos += len;
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      const el = node as HTMLElement;
      if (el.classList.contains('edit-fn-chip')) {
        const id = el.dataset.fnId ?? '';
        const L = `[^${id}]`.length;
        if (o < pos + L) return { node: root, offset: i };
        if (o === pos + L) return { node: root, offset: i + 1 };
        pos += L;
      } else if (el.tagName === 'BR') {
        if (o === pos) return { node: root, offset: i };
        pos += 1;
      } else {
        pos += serializeRoot(el).length;
      }
    }
  }
  return { node: root, offset: children.length };
}

function setDomSelectionFromPlainOffsets(root: HTMLElement, start: number, end: number) {
  const a = pointAtPlainOffset(root, start);
  const b = pointAtPlainOffset(root, end);
  const range = document.createRange();
  range.setStart(a.node, a.offset);
  range.setEnd(b.node, b.offset);
  const sel = window.getSelection();
  if (sel) {
    sel.removeAllRanges();
    sel.addRange(range);
  }
}

export type SegmentBodyEditorProps = {
  value: string;
  onChange: (next: string) => void;
  onFocus?: () => void;
  onBlur?: () => void;
  onCursorOffsetChange?: (offset: number) => void;
  onFootnoteClick: (id: number) => void;
  className?: string;
  minRows?: number;
};

const SegmentBodyEditor = forwardRef<SegmentBodyEditorHandle, SegmentBodyEditorProps>(
  function SegmentBodyEditor(
    {
      value,
      onChange,
      onFocus,
      onBlur,
      onCursorOffsetChange,
      onFootnoteClick,
      className,
      minRows = 3,
    },
    ref,
  ) {
    const rootRef = useRef<HTMLDivElement>(null);
    /** 记录上次通过 onChange 抛出的值，避免父组件把 value 作为 props 回写时触发重入 */
    const lastEmittedRef = useRef<string | null>(null);
    const footnoteRef = useRef(onFootnoteClick);
    footnoteRef.current = onFootnoteClick;
    const stableFootnoteDown = useCallback((id: number) => {
      footnoteRef.current(id);
    }, []);

    const emitOffset = useCallback(() => {
      const root = rootRef.current;
      if (!root || !onCursorOffsetChange) return;
      const { start } = getPlainOffsetsFromSelection(root);
      onCursorOffsetChange(start);
    }, [onCursorOffsetChange]);

    useLayoutEffect(() => {
      const root = rootRef.current;
      if (!root) return;
      const dom = serializeRoot(root);
      if (lastEmittedRef.current === null) {
        if (dom !== value || (value === '' && root.childNodes.length === 0)) {
          hydrateRoot(root, value, stableFootnoteDown);
        }
        lastEmittedRef.current = value;
        return;
      }
      if (value === lastEmittedRef.current) return;
      lastEmittedRef.current = value;
      hydrateRoot(root, value, stableFootnoteDown);
    }, [value, stableFootnoteDown]);

    useImperativeHandle(
      ref,
      () => ({
        focus: () => {
          rootRef.current?.focus();
        },
        getPlainOffset: () => {
          const root = rootRef.current;
          if (!root) return 0;
          return getPlainOffsetsFromSelection(root).start;
        },
        setPlainOffset: (offset: number) => {
          const root = rootRef.current;
          if (!root) return;
          setDomSelectionFromPlainOffsets(root, offset, offset);
          emitOffset();
        },
        setSelectionRange: (start: number, end: number) => {
          const root = rootRef.current;
          if (!root) return;
          setDomSelectionFromPlainOffsets(root, start, end);
          emitOffset();
        },
      }),
      [emitOffset],
    );

    const flushInput = useCallback(() => {
      const root = rootRef.current;
      if (!root) return;
      const next = serializeRoot(root);
      lastEmittedRef.current = next;
      onChange(next);
      emitOffset();
    }, [onChange, emitOffset]);

    const onInput = useCallback(() => {
      flushInput();
    }, [flushInput]);

    const onPaste = useCallback(
      (e: ClipboardEvent<HTMLDivElement>) => {
        e.preventDefault();
        const text = e.clipboardData.getData('text/plain');
        document.execCommand('insertText', false, text);
        flushInput();
      },
      [flushInput],
    );

    const onKeyDown = useCallback(
      (e: KeyboardEvent<HTMLDivElement>) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          document.execCommand('insertText', false, '\n');
          queueMicrotask(() => flushInput());
        }
      },
      [flushInput],
    );

    useEffect(() => {
      const onSel = () => {
        const root = rootRef.current;
        if (!root) return;
        const sel = window.getSelection();
        if (!sel?.anchorNode || !root.contains(sel.anchorNode)) return;
        emitOffset();
      };
      document.addEventListener('selectionchange', onSel);
      return () => document.removeEventListener('selectionchange', onSel);
    }, [emitOffset]);

    const style: CSSProperties | undefined = minRows
      ? { minHeight: `calc(${minRows} * 1.8 * 18px + 24px)` }
      : undefined;

    return (
      <div
        ref={rootRef}
        className={className}
        contentEditable
        suppressContentEditableWarning
        role="textbox"
        aria-multiline
        style={style}
        onInput={onInput}
        onFocus={onFocus}
        onBlur={onBlur}
        onPaste={onPaste}
        onKeyDown={onKeyDown}
        onMouseUp={emitOffset}
        onKeyUp={emitOffset}
      />
    );
  },
);

export default SegmentBodyEditor;
