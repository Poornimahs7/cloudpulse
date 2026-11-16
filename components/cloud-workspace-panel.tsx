import { Activity, AlertTriangle, Radio } from 'lucide-react'
import type { ReactNode } from 'react'

type CloudDevice = { id: string; device_key: string; enabled: boolean }
type CloudReading = { device_key: string; metric_type: string; value: number; measured_at: string }

export type CloudSnapshot = {
  devices: CloudDevice[]
  readings: CloudReading[]
  error: boolean
}

function Metric({ label, value, icon }: { label: string; value: string | number; icon: ReactNode }) {
  return <div className="cloud-metric"><span>{icon}</span><small>{label}</small><strong>{value}</strong></div>
}

export default function CloudWorkspacePanel({ snapshot }: { snapshot: CloudSnapshot }) {
  const online = snapshot.devices.filter(device => device.enabled).length

  return <section className="panel cloud-summary" aria-label="Supabase cloud workspace data">
    <div className="panel-heading"><div><div className="eyebrow">SUPABASE · USER-SCOPED</div><h2>Cloud workspace</h2></div><span className={`pill ${snapshot.error ? 'pill-amber' : 'pill-green'}`}><i className="pill-dot"/>{snapshot.error ? 'Query issue' : 'Authenticated query'}</span></div>
    {snapshot.error ? <div className="cloud-empty" role="alert"><AlertTriangle size={17}/> Unable to load cloud records. Check the Supabase connection and your account access.</div> : <>
      <p className="cloud-explainer">These records are read from Supabase using your signed-in session and per-user row-level security. This view never mixes them with local demo telemetry.</p>
      <div className="cloud-metrics"><Metric label="Registered devices" value={snapshot.devices.length} icon={<Radio size={15}/>} /><Metric label="Enabled devices" value={online} icon={<Activity size={15}/>} /><Metric label="Recent stored readings" value={snapshot.readings.length} icon={<Activity size={15}/>} /></div>
      {snapshot.readings.length ? <div className="cloud-reading-list"><div className="cloud-reading-head"><span>DEVICE</span><span>METRIC</span><span>VALUE</span><span>MEASURED AT</span></div>{snapshot.readings.map((reading, index) => <div className="cloud-reading-row" key={`${reading.device_key}-${reading.measured_at}-${index}`}><strong>{reading.device_key}</strong><span>{reading.metric_type}</span><span>{reading.value}</span><time dateTime={reading.measured_at}>{new Date(reading.measured_at).toISOString()}</time></div>)}</div> : <div className="cloud-empty"><Activity size={17}/> No sensor readings have been persisted for this account yet.</div>}
    </>}
  </section>
}
