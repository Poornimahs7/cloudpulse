import { redirect } from 'next/navigation'
import CloudPulseDashboard from '@/components/cloudpulse-dashboard'
import { createClient } from '@/lib/supabase/server'

export default async function ProtectedDashboardPage() {
  const supabase = await createClient()
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) redirect('/auth/login')

  const [{ data: devices, error: devicesError }, { data: readings, error: readingsError }] = await Promise.all([
    supabase.from('devices').select('id, device_key, enabled').order('created_at', { ascending: false }).limit(100),
    supabase.from('sensor_readings').select('device_id, metric_type, value, measured_at').order('measured_at', { ascending: false }).limit(10),
  ])
  const cloudSnapshot = {
    devices: devices ?? [],
    readings: (readings ?? []).map(reading => ({
      device_key: devices?.find(device => device.id === reading.device_id)?.device_key ?? 'Unknown device',
      metric_type: reading.metric_type,
      value: reading.value,
      measured_at: reading.measured_at,
    })),
    error: Boolean(devicesError || readingsError),
  }

  return <CloudPulseDashboard userEmail={user.email ?? undefined} userId={user.id} cloudSnapshot={cloudSnapshot} />
}
