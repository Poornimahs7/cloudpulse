'use client'

import Link from 'next/link'
import { FormEvent, useState } from 'react'
import { createClient } from '@/lib/supabase/client'

export default function SignUpPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setMessage('')
    setError('')
    const { error: authError } = await createClient().auth.signUp({
      email,
      password,
      options: {
        emailRedirectTo: process.env.NEXT_PUBLIC_DEV_SUPABASE_REDIRECT_URL ?? `${window.location.origin}/auth/callback`,
      },
    })
    setBusy(false)
    if (authError) {
      setError(authError.code === 'weak_password'
        ? 'Choose a stronger password.'
        : authError.code === 'email_address_invalid'
          ? 'Use a valid email address.'
          : authError.code === 'over_email_send_rate_limit' || authError.status === 429
            ? 'Too many attempts. Wait a moment and try again.'
            : 'Unable to complete sign-up. Check your details and try again.')
      return
    }
    setMessage('Check your inbox for the confirmation link. Once confirmed, sign in to open your protected dashboard.')
  }

  return <main className="auth-page"><div className="auth-card"><Link href="/" className="auth-brand"><span className="auth-mark">CP</span><span>CloudPulse</span></Link><p className="auth-eyebrow">CREATE WORKSPACE ACCESS</p><h1>Create your account</h1><p className="auth-copy">Register to access your private, per-user dashboard.</p><form onSubmit={submit} className="auth-form"><label htmlFor="email">Email address</label><input id="email" type="email" autoComplete="email" required value={email} onChange={event => setEmail(event.target.value)} /><label htmlFor="password">Password</label><input id="password" type="password" autoComplete="new-password" minLength={8} required value={password} onChange={event => setPassword(event.target.value)} />{error && <p className="auth-error" role="alert">{error}</p>}{message && <p className="auth-success" role="status">{message}</p>}<button disabled={busy}>{busy ? 'Creating account…' : 'Create account'}</button></form><p className="auth-switch">Already registered? <Link href="/auth/login">Sign in</Link></p></div></main>
}
