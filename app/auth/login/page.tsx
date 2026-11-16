'use client'

import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { FormEvent, Suspense, useState } from 'react'
import { createClient } from '@/lib/supabase/client'

function LoginForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const next = searchParams.get('next')
  const destination = (() => {
    if (!next || !next.startsWith('/') || next.startsWith('//') || next.includes('\\')) return '/dashboard'
    const target = new URL(next, 'https://cloudpulse.invalid')
    return target.origin === 'https://cloudpulse.invalid' ? `${target.pathname}${target.search}${target.hash}` : '/dashboard'
  })()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(searchParams.get('error') === 'confirmation' ? 'That confirmation link is invalid or expired. Request a new one.' : '')
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError('')
    const { error: authError } = await createClient().auth.signInWithPassword({ email, password })
    setBusy(false)
    if (authError) {
      setError(authError.code === 'email_not_confirmed'
        ? 'Confirm your email using the link we sent before signing in.'
        : authError.code === 'over_request_rate_limit' || authError.status === 429
          ? 'Too many attempts. Wait a moment and try again.'
          : 'Unable to sign in with those credentials.')
      return
    }
    router.replace(destination)
    router.refresh()
  }

  return <main className="auth-page"><div className="auth-card"><Link href="/" className="auth-brand"><span className="auth-mark">CP</span><span>CloudPulse</span></Link><p className="auth-eyebrow">SECURE WORKSPACE</p><h1>Welcome back</h1><p className="auth-copy">Sign in to access your protected CloudPulse dashboard.</p><form onSubmit={submit} className="auth-form"><label htmlFor="email">Email address</label><input id="email" type="email" autoComplete="email" required value={email} onChange={event => setEmail(event.target.value)} /><label htmlFor="password">Password</label><input id="password" type="password" autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)} />{error && <p className="auth-error" role="alert">{error}</p>}<button disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button></form><p className="auth-switch">New to CloudPulse? <Link href="/auth/sign-up">Create an account</Link></p><p className="auth-footnote">The public dashboard remains in clearly labeled local demo mode.</p></div></main>
}

export default function LoginPage() {
  return <Suspense fallback={<main className="auth-page"><div className="auth-card">Loading sign in…</div></main>}><LoginForm /></Suspense>
}
