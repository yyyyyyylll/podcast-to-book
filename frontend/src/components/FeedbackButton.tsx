import { useState } from 'react';
import { useLocation } from 'react-router-dom';
import FeedbackModal from './FeedbackModal';
import { analytics } from '../services/analytics';

export default function FeedbackButton() {
  const [open, setOpen] = useState(false);
  const [hovered, setHovered] = useState(false);
  const location = useLocation();

  if (location.pathname.startsWith('/admin')) return null;

  return (
    <>
      <button
        className={`ep-fab${hovered ? ' ep-fab-expanded' : ''}`}
        onClick={() => { analytics.track('feedback_opened'); setOpen(true); }}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        aria-label="意见反馈"
      >
        <img src="/icons/support.svg" width="28" height="28" alt="" style={{ filter: hovered ? 'invert(1)' : 'none', transition: 'filter 0.25s' }} />
        {hovered && <span className="ep-fab-label">反馈</span>}
      </button>
      <FeedbackModal open={open} onClose={() => setOpen(false)} />
    </>
  );
}
