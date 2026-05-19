import { useState, useCallback, useEffect, useRef, useMemo } from 'react'
import { useDropzone } from 'react-dropzone'
import { AuthProvider, useAuth } from 'react-oidc-context'
import { Log } from 'oidc-client-ts'
import { ShieldCheck, Loader } from 'lucide-react'
import axios from 'axios'
import './index.css'
import casmartsLogo from './assets/logo_casmarts.png'
import appLogo from './assets/logo.png'

// Enable OIDC logging for easier debugging
Log.setLogger(console);
Log.setLevel(Log.DEBUG);

// URL dinámica: funciona desde localhost y desde la IP real del servidor
const BASE_PATH = import.meta.env.BASE_URL || '/idp-smart/';
const API_BASE = window.location.origin + BASE_PATH.replace(/\/$/, '');

const oidcConfig = {
  authority: "https://auth.casmart.internal/application/o/idp-smart/",
  client_id: "idp-smart",
  redirect_uri: window.location.origin + BASE_PATH,
  post_logout_redirect_uri: window.location.origin + BASE_PATH,
  response_type: "code",
  scope: "openid profile email",
  automaticSilentRenew: false,
  monitorSession: false,
  loadUserInfo: true,
  onSigninCallback: () => {
    window.history.replaceState({}, document.title, window.location.pathname);
  }
}

// Etapas en orden para la barra de progreso
const STAGE_ORDER = ['INICIO', 'VISION', 'SCHEMA_LOAD', 'AGENT', 'MAPPER', 'SIMPLIFY', 'DB_SAVE', 'COMPLETADO']

const fmtTime = (s) => {
  if (!s && s !== 0) return '—'
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60), rem = s % 60
  return `${m}m ${rem}s`
}

const fileIcon = (name) => {
  const ext = name.split('.').pop().toLowerCase()
  if (['pdf'].includes(ext)) return '📄'
  if (['png', 'jpg', 'jpeg', 'tif', 'tiff', 'webp'].includes(ext)) return '🖼️'
  if (['doc', 'docx'].includes(ext)) return '📝'
  return '📎'
}

// ── Tarjeta de tarea activa ──────────────────────────────────────────────────
function TaskCard({ task, onDismiss }) {
  const [progress, setProgress] = useState(null)
  const intervalRef = useRef(null)

  const poll = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API_BASE}/api/v1/progress/${task.taskId}`)
      setProgress(data)
      if (data.finished) clearInterval(intervalRef.current)
    } catch { /* network hiccup — retrying */ }
  }, [task.taskId])

  useEffect(() => {
    poll()
    intervalRef.current = setInterval(poll, 4000)
    return () => clearInterval(intervalRef.current)
  }, [poll])

  const pct = progress?.progress_pct ?? 0
  const finished = progress?.finished
  const isError = progress?.status?.startsWith('ERROR') || progress?.status === 'ERROR'
  const stageLabel = progress?.message || progress?.stage_label || 'Iniciando…'
  const elapsed = progress?.elapsed_seconds
  const remaining = progress?.estimated_remaining_s

  const stageIdx = STAGE_ORDER.indexOf(progress?.stage_current)

  return (
    <div className={`task-card ${finished ? (isError ? 'task-error' : 'task-done') : 'task-active'}`}>
      <div className="task-header">
        <div className="task-meta">
          <span className={`task-badge ${finished ? (isError ? 'badge-error' : 'badge-done') : 'badge-active'}`}>
            {finished ? (isError ? '✗ Error' : '✓ Completado') : '⟳ En proceso'}
          </span>
          <span className="task-label">{task.actLabel}</span>
          <div className="task-file-info">
            <span className="file-name-line">
              <span className="file-icon">📁</span> {progress?.file_name || task.fileName}
            </span>
            {progress?.page_count > 0 && (
              <span className="page-count-badge">
                <span className="page-count-icon">📄</span> {progress.page_count} {progress.page_count === 1 ? 'página' : 'páginas'}
              </span>
            )}
          </div>
          <div className="task-engine-info">
            {progress?.llm_provider && (
              <span className="provider-badge">
                <span className="provider-icon">🤖</span> {progress.llm_provider.toUpperCase()}
                {progress?.llm_model && <span className="model-name-pill">{progress.llm_model}</span>}
              </span>
            )}
            {progress?.gpu_model && (
              <span className="gpu-badge" title="Hardware utilizado">
                <span className="gpu-icon">⚡</span> {progress.gpu_model}
              </span>
            )}
          </div>
        </div>
        <div className="task-times">
          {progress?.is_waiting && !finished && (
            <span className="time-chip waiting">⏳ Esperando worker…</span>
          )}
          {!progress?.is_waiting && elapsed != null && (
            <span className="time-chip" title={finished ? "Tiempo total" : "Tiempo transcurrido"}>
              ⏱ {fmtTime(elapsed)}
            </span>
          )}
          {!finished && !progress?.is_waiting && remaining != null && (
            <span className="time-chip est">≈ {fmtTime(remaining)} restante</span>
          )}
          {finished && (
            <button onClick={() => onDismiss(task.taskId)} className="btn-dismiss">✕</button>
          )}
        </div>
      </div>

      {/* Barra de progreso */}
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${pct}%`, background: isError ? '#ef4444' : undefined }} />
      </div>

      {/* Etapas */}
      <div className="stages-row">
        {STAGE_ORDER.slice(0, -1).map((s, i) => (
          <div key={s} className={`stage-dot ${i < stageIdx ? 'done' : i === stageIdx ? 'active' : 'pending'}`}
            title={s} />
        ))}
      </div>

      <p className="stage-label-text">{stageLabel}</p>

      <div className="task-footer">
        <code className="task-id">{task.taskId}</code>
        {finished && !isError && (
          <a href={`${API_BASE}/api/v1/simplified/${task.taskId}`}
            target="_blank" rel="noopener noreferrer" className="btn-result">
            Ver resultado →
          </a>
        )}
      </div>
    </div>
  )
}

// ── Componente de Historial ──────────────────────────────────────────────────
function HistoryView({ onNavigateBack, onShowProgress }) {
  const [extractions, setExtractions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [reprocessing, setReprocessing] = useState({})
  const [deleting, setDeleting] = useState({})
  const [reusing, setReusing] = useState(null)
  const [sortConfig, setSortConfig] = useState({ key: 'created_at', direction: 'desc' })

  const sortedExtractions = useMemo(() => {
    let sortableItems = [...extractions];
    if (sortConfig.key !== null) {
      sortableItems.sort((a, b) => {
        let aVal = a[sortConfig.key] || "";
        let bVal = b[sortConfig.key] || "";

        if (sortConfig.key === 'pdf_storage_path') {
          aVal = aVal?.split('/').pop() || '';
          bVal = bVal?.split('/').pop() || '';
        }

        if (aVal < bVal) return sortConfig.direction === 'asc' ? -1 : 1;
        if (aVal > bVal) return sortConfig.direction === 'asc' ? 1 : -1;
        return 0;
      });
    }
    return sortableItems;
  }, [extractions, sortConfig]);

  const requestSort = (key) => {
    let direction = 'asc';
    if (sortConfig.key === key && sortConfig.direction === 'asc') {
      direction = 'desc';
    }
    setSortConfig({ key, direction });
  }

  const getSortIcon = (key) => {
    if (sortConfig.key !== key) return ' ↕';
    return sortConfig.direction === 'asc' ? ' ↑' : ' ↓';
  }

  const fetchHistory = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)
      const { data } = await axios.get(`${API_BASE}/api/v1/extractions`)
      setExtractions(data.extractions || [])
    } catch (err) {
      console.error('History Error:', err)
      setError(`Error: ${err.message || 'No se pudo conectar con la API'}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchHistory()
  }, [fetchHistory])

  const handleReprocess = async (ex, skipVision = false) => {
    try {
      setReprocessing(prev => ({ ...prev, [ex.task_id]: true }))
      await axios.post(`${API_BASE}/api/v1/reprocess/${ex.task_id}?skip_vision=${skipVision}`)
      onShowProgress(ex.task_id, ex.act_type) // NEW: Add it back to progress view automatically!
      setTimeout(fetchHistory, 1500)
    } catch (err) {
      alert(`Error al solicitar re-proceso: ${err.message}`)
    } finally {
      setReprocessing(prev => ({ ...prev, [ex.task_id]: false }))
    }
  }

  const handleDelete = async (taskId) => {
    if (!window.confirm('¿Estás seguro de eliminar este registro del historial?')) return
    try {
      setDeleting(prev => ({ ...prev, [taskId]: true }))
      await axios.delete(`${API_BASE}/api/v1/extractions/${taskId}`)
      setExtractions(prev => prev.filter(ex => ex.task_id !== taskId))
    } catch (err) {
      alert(`Error al eliminar: ${err.message}`)
    } finally {
      setDeleting(prev => ({ ...prev, [taskId]: false }))
    }
  }

  const fileInputRef = useRef(null)
  const [taskForAddenda, setTaskForAddenda] = useState(null)

  const handleReprocessWithAddendaClick = (ex) => {
    setTaskForAddenda(ex)
    fileInputRef.current?.click()
  }

  const handleAddendaSelected = async (e) => {
    const files = e.target.files
    if (!taskForAddenda || !files || files.length === 0) return

    const fd = new FormData()
    for (let i = 0; i < files.length; i++) {
      fd.append('additional_documents', files[i])
    }

    try {
      setReprocessing(prev => ({ ...prev, [taskForAddenda.task_id]: true }))
      await axios.post(`${API_BASE}/api/v1/reprocess/${taskForAddenda.task_id}?skip_vision=false`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
      onShowProgress(taskForAddenda.task_id, taskForAddenda.act_type)
      setTimeout(fetchHistory, 1500)
    } catch (err) {
      alert(`Error al reprocesar con adendas: ${err.message}`)
    } finally {
      setReprocessing(prev => ({ ...prev, [taskForAddenda.task_id]: false }))
      setTaskForAddenda(null)
      if (fileInputRef.current) fileInputRef.current.value = null
    }
  }

  const handleReuse = (task) => {
    onNavigateBack() // Go to home
    document.dispatchEvent(new CustomEvent('reuse-document', {
      detail: {
        taskId: task.task_id,
        fileName: task.pdf_storage_path?.split('/').pop() || 'Documento previo',
        actType: task.act_type
      }
    }))
  }

  if (loading) return <div className="loading-state">⏳ Cargando historial de extracciones...</div>

  return (
    <div className="history-container">
      <input
        type="file"
        multiple
        ref={fileInputRef}
        style={{ display: 'none' }}
        onChange={handleAddendaSelected}
      />
      <div className="history-header">
        <button onClick={onNavigateBack} className="btn-back">← Volver al inicio</button>
        <h2 className="history-title">Historial de Procesos</h2>
      </div>

      {error && (
        <div className="error-pill" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>⚠ {error}</span>
          <button onClick={fetchHistory} className="btn-action-small">⟳</button>
        </div>
      )}

      <div className="history-table-wrap">
        <table className="history-table">
          <thead>
            <tr>
              <th onClick={() => requestSort('task_id')} style={{ cursor: 'pointer', userSelect: 'none' }}>
                ID de Tarea{getSortIcon('task_id')}
              </th>
              <th onClick={() => requestSort('act_type')} style={{ cursor: 'pointer', userSelect: 'none' }}>
                Acto{getSortIcon('act_type')}
              </th>
              <th onClick={() => requestSort('pdf_storage_path')} style={{ cursor: 'pointer', userSelect: 'none' }}>
                Documento{getSortIcon('pdf_storage_path')}
              </th>
              <th onClick={() => requestSort('status')} style={{ cursor: 'pointer', userSelect: 'none' }}>
                Estado / Etapa{getSortIcon('status')}
              </th>
              <th onClick={() => requestSort('created_at')} style={{ cursor: 'pointer', userSelect: 'none' }}>
                Fecha{getSortIcon('created_at')}
              </th>
              <th>Motor</th>
              <th>Págs</th>
              <th>Visión</th>
              <th>IA / JSON</th>
              <th>⚡ Eficiencia</th>
              <th>Total</th>
              <th>Acciones</th>
            </tr>
          </thead>
          <tbody>
            {sortedExtractions.map(ex => (
              <tr key={ex.task_id}>
                <td><code className="task-id-small">{ex.task_id?.substring(0, 8)}...</code></td>
                <td><span className="badge-gray">{ex.act_type || '—'}</span></td>
                <td className="file-cell" title={ex.pdf_storage_path}>
                  {ex.expediente_id || ex.pdf_storage_path?.split('/').pop() || '—'}
                </td>
                <td>
                  <span className={`status-chip ${(ex.status || 'unknown').toLowerCase()}`}>
                    {ex.stage_current || ex.status || 'En cola'}
                  </span>
                </td>
                <td>{ex.created_at ? ex.created_at.split('.')[0] : '—'}</td>
                <td>
                  <div className="engine-cell">
                    <span className={`engine-chip ${ex.llm_provider}`}>{ex.llm_provider || '—'}</span>
                    <span className="model-subtext">{ex.llm_model || '—'}</span>
                    {ex.gpu_model && <span className="gpu-subtext">{ex.gpu_model}</span>}
                  </div>
                </td>
                <td><span className="time-metric">{ex.page_count || '—'}</span></td>
                <td><span className="time-metric">{ex.docling_duration_s ? `${ex.docling_duration_s}s` : '—'}</span></td>
                <td><span className="time-metric">{ex.ai_duration_s ? `${ex.ai_duration_s}s` : '—'}</span></td>
                <td>
                  <span className="time-metric effort">
                    {ex.total_duration_s && ex.page_count
                      ? `${(ex.total_duration_s / ex.page_count).toFixed(1)}s/pág`
                      : '—'}
                  </span>
                </td>
                <td><span className="time-metric bold">{ex.total_duration_s ? fmtTime(Math.round(ex.total_duration_s)) : '—'}</span></td>
                <td className="actions-cell">
                  <div className="action-buttons">
                    <button
                      onClick={() => onShowProgress(ex.task_id, ex.act_type)}
                      className="btn-action-small progress"
                      title="Ver detalle del progreso"
                    >
                      📊
                    </button>
                    <a
                      href={`${API_BASE}/api/v1/document/pdf/${ex.task_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="btn-action-small view"
                      title="Ver PDF Original"
                    >
                      📄
                    </a>
                    {(ex.status === 'COMPLETED' || ex.status === 'COMPLETADO' || ex.markdown_minio_path) && (
                      <a
                        href={`${API_BASE}/api/v1/document/markdown/${ex.task_id}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="btn-action-small view md"
                        title="Ver Markdown Extraído"
                      >
                        📝
                      </a>
                    )}
                    <button
                      onClick={() => handleReprocess(ex, true)}
                      disabled={reprocessing[ex.task_id]}
                      className="btn-action-small"
                      title="Re-procesar solo IA"
                    >
                      IA
                    </button>
                    <button
                      onClick={() => handleReprocess(ex, false)}
                      disabled={reprocessing[ex.task_id]}
                      className="btn-action-small"
                      title="Re-procesar todo"
                    >
                      🔄
                    </button>
                    <button
                      onClick={() => handleReprocessWithAddendaClick(ex)}
                      disabled={reprocessing[ex.task_id]}
                      className="btn-action-small"
                      title="Re-procesar todo añadiendo adendas"
                    >
                      +📎
                    </button>
                    <button
                      onClick={() => handleDelete(ex.task_id)}
                      disabled={deleting[ex.task_id]}
                      className="btn-action-small delete"
                      title="Eliminar registro"
                    >
                      🗑️
                    </button>
                    <button
                      onClick={() => handleReuse(ex)}
                      className="btn-action-small reuse"
                      title="Reutilizar documento en nueva forma"
                    >
                      ♻️
                    </button>
                    {(ex.status === 'COMPLETED' || ex.status === 'COMPLETADO') && (
                      <>
                        <a href={`${API_BASE}/api/v1/simplified/${ex.task_id}`} target="_blank" className="btn-action-small view" title="Ver JSON Simplificado">S</a>
                        <a href={`${API_BASE}/api/v1/full/${ex.task_id}`} target="_blank" className="btn-action-small view full" title="Ver JSON Completo (Java)">F</a>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {extractions.length === 0 && (
              <tr><td colSpan="6" className="empty-row">No se encontraron extracciones.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Componente principal ─────────────────────────────────────────────────────
function AppContent() {
  const auth = useAuth()
  const [view, setView] = useState('home') // 'home' | 'history'

  // Monitor Auth state changes for debugging and loop prevention
  useEffect(() => {
    console.log("IDP-Smart Auth State:", {
      isLoading: auth.isLoading,
      isAuthenticated: auth.isAuthenticated,
      error: auth.error?.message,
      pathname: window.location.pathname
    });

    // If we are authenticated but have an error or are missing essential info, 
    // it might be a stale session.
    if (auth.isAuthenticated && !auth.isLoading && !auth.user) {
      console.warn("Authenticated but no user found. Potential stale state.");
    }
  }, [auth.isAuthenticated, auth.isLoading, auth.user, auth.error]);
  const [files, setFiles] = useState([])
  const [actTypes, setActTypes] = useState([])
  const [selectedActs, setSelectedActs] = useState([])
  const [loadingActs, setLoadingActs] = useState(true)
  const [actError, setActError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [tasks, setTasks] = useState([])
  const [reusingDoc, setReusingDoc] = useState(null) // { taskId, fileName }
  const [customExpediente, setCustomExpediente] = useState('')
  const [actSearch, setActSearch] = useState('')
  const [showOptions, setShowOptions] = useState(false)
  const dropdownRef = useRef(null)

  useEffect(() => {
    document.title = 'IDP-Smart | CASMARTS 2026'
  }, [])

  // Axios Interceptor for Authentik
  useEffect(() => {
    if (auth.isAuthenticated && auth.user) {
      const interceptor = axios.interceptors.request.use(config => {
        config.headers.Authorization = `Bearer ${auth.user.access_token}`;
        return config;
      });
      return () => axios.interceptors.request.eject(interceptor);
    }
  }, [auth.isAuthenticated, auth.user]);

  // Ocultar dropdown al hacer click fuera
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setShowOptions(false)
        setActSearch('')
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Filtrado de actos
  const filteredActs = useMemo(() => {
    if (!actSearch) return actTypes
    const s = actSearch.toLowerCase()
    return actTypes.filter(act =>
      act.display_label.toLowerCase().includes(s) ||
      act.dsactocorta.toLowerCase().includes(s) ||
      String(act.form_code).toLowerCase().includes(s)
    )
  }, [actTypes, actSearch])

  useEffect(() => {
    const handler = (e) => {
      setReusingDoc(e.detail)
      setFiles([]) // Clear files if we are reusing
    }
    document.addEventListener('reuse-document', handler)
    return () => document.removeEventListener('reuse-document', handler)
  }, [])

  const showProgress = (taskId, actLabel) => {
    // Si la tarea ya está en la lista de monitoreo, no la duplicamos
    if (!tasks.some(t => t.taskId === taskId)) {
      setTasks(prev => [{ taskId, actLabel: actLabel || 'Extracción manual' }, ...prev])
    }
  }

  const dismissTask = (taskId) => {
    setTasks(prev => prev.filter(t => t.taskId !== taskId))
  }

  useEffect(() => {
    if (auth.isAuthenticated && auth.user) {
      setLoadingActs(true)
      axios.get(`${API_BASE}/api/v1/forms`)
        .then(({ data }) => {
          setActTypes(data.acts || [])
        })
        .catch(() => setActError('No se pudo conectar con la API para obtener los tipos de acto.'))
        .finally(() => setLoadingActs(false))
    }
  }, [auth.isAuthenticated, auth.user])

  // Dropzone
  const onDrop = useCallback((accepted) => {
    setReusingDoc(null) // Clear reuse if new files are dropped
    setFiles(prev => {
      const names = new Set(prev.map(f => f.name))
      return [...prev, ...accepted.filter(f => !names.has(f.name))]
    })
  }, [])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/pdf': ['.pdf'],
      'image/*': ['.png', '.jpg', '.jpeg', '.tif', '.tiff', '.webp'],
      'image/tiff': ['.tif', '.tiff'],
      'application/msword': ['.doc'],
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
    },
    multiple: true,
  })

  const removeFile = (name) => setFiles(prev => prev.filter(f => f.name !== name))

  const [groupAsAddenda, setGroupAsAddenda] = useState(false)

  const handleSubmit = async () => {
    if (selectedActs.length === 0 || (files.length === 0 && !reusingDoc)) return
    setSubmitting(true)

    // Juntar actos y códigos para envío masivo (multi-acto)
    const actsToSend = selectedActs.map(a => a.dsactocorta).join(',')
    const codesToSend = selectedActs.map(a => a.form_code).join(',')

    // El primer acto siempre sirve como ancla si no hay json_form.
    // En multiacto, el backend buscará los demás en el catálogo automáticamente.
    const fd = new FormData()
    fd.append('act_type', actsToSend)
    fd.append('form_code', codesToSend)
    if (customExpediente) fd.append('expediente_id', customExpediente)

    const results = []

    if (reusingDoc) {
      fd.append('reuse_task_id', reusingDoc.taskId)
      try {
        const { data } = await axios.post(`${API_BASE}/api/v1/process`, fd)
        const ids = data.task_ids || [data.task_id]
        ids.forEach((tid, i) => {
          results.push({
            taskId: tid,
            fileName: reusingDoc.fileName,
            actLabel: selectedActs[i]?.display_label || actsToSend,
          })
        })
      } catch (err) {
        alert(`Error al reutilizar: ${err.message}`)
      }
    } else {
      // Caso normal: Un solo documento o con adendas
      if (files.length > 0) {
        fd.append('document', files[0])
        if (groupAsAddenda && files.length > 1) {
          for (let i = 1; i < files.length; i++) {
            fd.append('additional_documents', files[i])
          }
        }

        try {
          const { data } = await axios.post(`${API_BASE}/api/v1/process`, fd, {
            headers: { 'Content-Type': 'multipart/form-data' },
          })
          const ids = data.task_ids || [data.task_id]
          ids.forEach((tid, i) => {
            results.push({
              taskId: tid,
              fileName: groupAsAddenda && files.length > 1 ? `${files[0].name} +${files.length - 1} adendas` : files[0].name,
              actLabel: selectedActs[i]?.display_label || actsToSend,
            })
          })
        } catch (err) {
          console.error(`Error enviando:`, err)
          const msg = err.response?.data?.detail
            ? JSON.stringify(err.response.data.detail)
            : (err.response?.data?.error || err.message)
          alert(`Error al procesar: \n${msg}`)
        }
      }
    }

    // Monitorear TODAS las tareas creadas
    const validResults = (results || []).filter(r => r.taskId && r.taskId !== 'undefined')
    if (validResults.length > 0) {
      setTasks(prev => [...validResults, ...prev])
    }
    setFiles([])
    setReusingDoc(null)
    setCustomExpediente('')
    setSelectedActs([])
    setActSearch('')
    setSubmitting(false)
  }

  const handleLogout = () => {
    // Uso de signoutRedirect para un cierre de sesión OIDC estándar y completo
    auth.signoutRedirect()
  }

  if (auth.isLoading) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-[#0f172a] relative overflow-hidden font-sans">
        {/* Background Blobs for Premium Feel */}
        <div className="fixed top-0 left-0 w-full h-full pointer-events-none z-0">
          <div className="absolute top-[-10%] right-[-10%] w-[50%] h-[50%] bg-blue-500/10 rounded-full blur-[120px]"></div>
          <div className="absolute bottom-[-10%] left-[-10%] w-[40%] h-[40%] bg-emerald-500/5 rounded-full blur-[100px]"></div>
        </div>
        <div className="flex flex-col items-center gap-4 text-white relative z-10">
          <Loader className="animate-spin text-blue-400" size={48} />
          <p className="font-semibold animate-pulse text-blue-400 tracking-wider text-sm">Sincronizando Identidad CASMARTS...</p>
        </div>
      </div>
    )
  }

  if (!auth.isAuthenticated) {
    return (
      <>
        <style>{`
          .login-container {
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            background: #0f172a;
            font-family: 'Outfit', sans-serif;
            position: relative;
            overflow: hidden;
          }
          .login-card {
            background: rgba(15, 23, 42, 0.55);
            backdrop-filter: blur(24px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            padding: 50px;
            border-radius: 32px;
            width: 100%;
            max-width: 450px;
            text-align: center;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5) !important;
            z-index: 10;
          }
          .logo-box {
            display: flex;
            justify-content: center;
            margin-bottom: 30px;
          }
          .logo-img {
            width: 110px;
            height: 110px;
            object-fit: contain;
            filter: brightness(1.1);
          }
          .login-h1 {
            font-size: 36px;
            font-weight: 800;
            color: white;
            margin-bottom: 10px;
            letter-spacing: -1px;
          }
          .login-p {
            font-size: 10px;
            font-weight: 700;
            color: #38bdf8;
            letter-spacing: 3px;
            text-transform: uppercase;
            margin-bottom: 40px;
          }
          .login-btn {
            width: 100%;
            padding: 18px;
            background: #2563eb;
            color: white;
            border: none;
            border-radius: 14px;
            font-weight: 700;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            transition: all 0.2s;
            box-shadow: 0 10px 15px -3px rgba(37, 99, 235, 0.3);
          }
          .login-btn:hover { 
            background: #1d4ed8; 
            box-shadow: 0 10px 20px -3px rgba(37, 99, 235, 0.5);
            transform: translateY(-1px);
          }
          .login-btn:active {
            transform: translateY(1px);
          }
          .login-footer {
            margin-top: 50px;
            padding-top: 30px;
            border-top: 1px solid rgba(255, 255, 255, 0.05);
          }
          .footer-logo { height: 16px; opacity: 0.4; margin-bottom: 10px; filter: grayscale(100%) invert(1); }
          .footer-text { font-size: 9px; color: rgba(255, 255, 255, 0.3); font-weight: 800; letter-spacing: 4px; text-transform: uppercase; }
          .bg-blob-1 {
            position: absolute;
            top: -10%;
            right: -10%;
            width: 50%;
            height: 50%;
            background: rgba(59, 130, 246, 0.1);
            border-radius: 50%;
            filter: blur(120px);
            pointer-events: none;
            z-index: 1;
          }
          .bg-blob-2 {
            position: absolute;
            bottom: -10%;
            left: -10%;
            width: 40%;
            height: 40%;
            background: rgba(16, 185, 129, 0.05);
            border-radius: 50%;
            filter: blur(100px);
            pointer-events: none;
            z-index: 1;
          }
        `}</style>
        <div className="login-container">
          <div className="bg-blob-1"></div>
          <div className="bg-blob-2"></div>
          <div className="login-card">
            <div className="logo-box">
              <img src={appLogo} alt="Logo" className="logo-img" />
            </div>
            <h1 className="login-h1">IDP-Smart</h1>
            <p className="login-p">Intelligent Document Processing</p>

            <button onClick={() => auth.signinRedirect()} disabled={auth.isLoading} className="login-btn">
              {auth.isLoading ? <Loader className="animate-spin" size={20} /> : <ShieldCheck size={20} />}
              <span>{auth.isLoading ? 'CONECTANDO...' : 'INICIAR SESIÓN CON CASMARTS ID'}</span>
            </button>

            <div style={{ marginTop: '25px' }}>
              <a href="https://auth.casmart.internal/if/flow/password-recovery/"
                style={{ fontSize: '12px', color: 'rgba(255, 255, 255, 0.5)', textDecoration: 'none', fontWeight: '600' }}>
                ¿Olvidaste tu contraseña?
              </a>
            </div>

            <div className="login-footer">
              <img src={casmartsLogo} alt="Casmarts" className="footer-logo" />
              <p className="footer-text">Powered by Casmarts AI Core</p>
            </div>
          </div>
        </div>
      </>
    )
  }

  const activeTasks = tasks.filter(t => true)

  return (
    <div className="app-shell">
      {/* Header */}
      <header className="app-header">
        <div className="logo-group">
          <img src={appLogo} alt="IDP Smart" className="logo-app" />
          <div className="logo-separator"></div>
          <img src={casmartsLogo} alt="CASMARTS" className="logo-corp" />
          <div onClick={() => setView('home')} style={{ cursor: 'pointer' }}>
            <div className="logo-name">IDP-Smart</div>
            <div className="logo-sub">Engine de Extracción Soberana</div>
          </div>
        </div>
        <div className="header-right">
          <button onClick={() => setView(view === 'home' ? 'history' : 'home')} className="btn-nav">
            {view === 'home' ? '📜 Historial' : '🏠 Nueva Extracción'}
          </button>
          <button onClick={handleLogout} className="btn-nav" style={{ borderColor: 'var(--error)', color: 'var(--error)' }}>
            Salir
          </button>
          {activeTasks.length > 0 && (
            <span className="tasks-badge">{activeTasks.length}</span>
          )}
          <span className="version-chip">v3.5 RPP</span>
        </div>
      </header>

      <main className="app-main">
        {view === 'history' ? (
          <HistoryView onNavigateBack={() => setView('home')} onShowProgress={showProgress} />
        ) : (
          <>
            {/* Hero */}
            <section className="hero">
              <h1 className="hero-title">
                Procesa expedientes con<br />
                <span className="hero-accent">Inteligencia Artificial</span>
              </h1>
              <p className="hero-sub">
                Sube escrituras, actas o adendas. El motor extrae el contexto semántico y pre-llena automáticamente las formas registrales.
              </p>
            </section>

            {/* Panel de upload */}
            <div className="upload-panel">
              {/* Paso 1 */}
              <div className="step">
                <div className="step-label">PASO 1 — TIPO DE ACTO</div>
                {loadingActs ? (
                  <div className="loading-pill">⏳ Cargando catálogo…</div>
                ) : actError ? (
                  <div className="error-pill">⚠ {actError}</div>
                ) : (
                  <div className="searchable-select-container" ref={dropdownRef}>
                    <div className="search-input-wrapper" onClick={() => setShowOptions(!showOptions)}>
                      <input
                        type="text"
                        placeholder="🔍 Buscar acto (ej. BI34, Compraventa...)"
                        value={actSearch}
                        onChange={(e) => {
                          setActSearch(e.target.value)
                          setShowOptions(true)
                        }}
                        onFocus={() => setShowOptions(true)}
                        className="act-select search-mode"
                        style={{ cursor: 'pointer' }} // se siente como un select
                      />
                      <div className="dropdown-arrow">▼</div>
                      {actSearch && (
                        <button className="clear-search" onClick={(e) => { e.stopPropagation(); setActSearch(''); setShowOptions(true); }}>✕</button>
                      )}
                    </div>

                    {showOptions && (
                      <div className="options-dropdown" style={{
                        position: 'absolute',
                        top: '100%',
                        left: 0,
                        right: 0,
                        backgroundColor: 'white',
                        border: '1px solid #e2e8f0',
                        borderRadius: '12px',
                        marginTop: '4px',
                        boxShadow: '0 10px 15px -3px rgba(0,0,0,0.1)',
                        zIndex: 2000,
                        maxHeight: '320px',
                        overflowY: 'auto'
                      }}>
                        {filteredActs.length > 0 ? (
                          filteredActs.map(act => (
                            <div
                              key={act.form_code}
                              className={`option-item ${selectedActs.some(s => s.form_code === act.form_code) ? 'selected' : ''}`}
                              onClick={() => {
                                if (selectedActs.some(s => s.form_code === act.form_code)) {
                                  setSelectedActs(prev => prev.filter(s => s.form_code !== act.form_code))
                                } else {
                                  setSelectedActs(prev => [...prev, act])
                                }
                                setActSearch('')
                                // setShowOptions(false) // mantenemos abierto para seleccionar más
                              }}
                            >
                              <div className="option-content">
                                <span className="option-code-pill">{act.dsactocorta}</span>
                                <span className="option-text">{act.dsacto}</span>
                                {selectedActs.some(s => s.form_code === act.form_code) && <span style={{ marginLeft: 'auto' }}>✓</span>}
                              </div>
                            </div>
                          ))
                        ) : (
                          <div className="option-item empty">No se encontraron actos</div>
                        )}
                      </div>
                    )}

                    {selectedActs.length > 0 && (
                      <div className="selected-acts-area">
                        <span className="selected-acts-count">Actos seleccionados ({selectedActs.length})</span>
                        <div className="selected-acts-list">
                          {selectedActs.map(act => (
                            <div key={act.form_code} className="selected-act-tag">
                              <span>{act.dsactocorta}</span>
                              <button className="remove-tag" onClick={() => setSelectedActs(prev => prev.filter(s => s.form_code !== act.form_code))}>✕</button>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Paso 2 */}
              <div className="step">
                <div className="step-label">PASO 2 — IDENTIFICACIÓN Y DOCUMENTOS</div>

                <div className="input-group" style={{ marginBottom: '1.5rem' }}>
                  <label htmlFor="expediente" className="field-label">Nombre del proceso / Expediente (Opcional)</label>
                  <input
                    id="expediente"
                    type="text"
                    placeholder="Ej. Escritura 42.105 - Juarez"
                    value={customExpediente}
                    onChange={(e) => setCustomExpediente(e.target.value)}
                    className="act-select" // Reutilizamos estilo
                    style={{ padding: '0.8rem', fontSize: '1rem' }}
                  />
                </div>

                {reusingDoc ? (
                  <div className="reuse-notice">
                    <div className="reuse-info">
                      <span className="reuse-icon">♻️</span>
                      <div>
                        <p className="reuse-title">Reutilizando documento</p>
                        <p className="reuse-file">{reusingDoc.fileName}</p>
                        <code className="reuse-id">ID: {reusingDoc.taskId}</code>
                      </div>
                    </div>
                    <button onClick={() => setReusingDoc(null)} className="btn-cancel-reuse">Cambiar por archivos nuevos</button>
                  </div>
                ) : (
                  <div {...getRootProps()} className={`dropzone ${isDragActive ? 'dz-active' : ''}`} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '3rem 2rem' }}>
                    <input {...getInputProps()} />
                    <svg className="dz-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ width: '64px', height: '64px', color: '#002A4C', marginBottom: '1rem', opacity: 0.8 }}>
                      <polyline points="16 16 12 12 8 16" />
                      <line x1="12" y1="12" x2="12" y2="21" />
                      <path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3" />
                    </svg>
                    <p className="dz-main" style={{ fontSize: '1.1rem', color: '#002A4C', marginBottom: '0.5rem' }}><span className="dz-link" style={{ fontWeight: 700, textDecoration: 'underline' }}>Haz clic</span> o arrastra los archivos aquí</p>
                    <p className="dz-types" style={{ fontSize: '0.75rem', color: '#64748b', fontWeight: 600, textTransform: 'uppercase' }}>PDF · TIFF · PNG · JPG · DOCX</p>
                  </div>
                )}

                {files.length > 0 && (
                  <div className="file-list">
                    <div className="file-list-header">ARCHIVOS SELECCIONADOS ({files.length})</div>
                    {files.map(f => (
                      <div key={f.name} className="file-row">
                        <span className="file-icon">{fileIcon(f.name)}</span>
                        <div className="file-info">
                          <span className="file-name">{f.name}</span>
                          <span className="file-size">{(f.size / 1024 / 1024).toFixed(2)} MB</span>
                        </div>
                        <button onClick={() => removeFile(f.name)} className="file-remove">✕</button>
                      </div>
                    ))}
                    {files.length > 1 && (
                      <div style={{ marginTop: '10px', fontSize: '0.9rem', color: '#4b5563', display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <input
                          type="checkbox"
                          id="groupAsAddenda"
                          checked={groupAsAddenda}
                          onChange={e => setGroupAsAddenda(e.target.checked)}
                        />
                        <label htmlFor="groupAsAddenda" style={{ cursor: 'pointer' }}>
                          Agrupar en una sola extracción (El 1° será principal, los demás Adendas)
                        </label>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Botón */}
              <div className="submit-row">
                <button
                  onClick={handleSubmit}
                  disabled={selectedActs.length === 0 || (files.length === 0 && !reusingDoc) || submitting}
                  className="btn-submit"
                >
                  {submitting ? '⟳ Enviando…' :
                    reusingDoc ? 'Iniciar con Documento Reutilizado' :
                      `Iniciar Extracción${selectedActs.length > 1 ? ` (${selectedActs.length} actos)` : ''}`}
                </button>
              </div>
            </div>

            {/* Panel de tareas activas */}
            {tasks.length > 0 && (
              <div className="tasks-panel">
                <div className="tasks-panel-header">
                  <h2 className="tasks-title">Tareas de Extracción</h2>
                </div>
                <div className="tasks-list">
                  {tasks.map(t => (
                    <TaskCard key={t.taskId} task={t} onDismiss={dismissTask} />
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider {...oidcConfig}>
      <AppContent />
    </AuthProvider>
  )
}
