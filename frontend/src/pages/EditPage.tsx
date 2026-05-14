import { useParams, useNavigate, Navigate } from 'react-router-dom';
import EditPanel from '../components/EditPanel';

export default function EditPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();

  if (!taskId) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="edit-page">
      <EditPanel
        taskId={taskId}
        onSaveComplete={() => navigate(`/result/${taskId}`, { replace: true })}
        onCancel={() => navigate(`/result/${taskId}`)}
      />
    </div>
  );
}
