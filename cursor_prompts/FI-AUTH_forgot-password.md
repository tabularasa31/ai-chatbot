# FI-AUTH: Forgot Password / Password Reset Flow

⚠️ **CRITICAL: Follow SETUP exactly. Do NOT skip `git pull origin main`.**

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/forgot-password
```

**MUST DO (in exact order):**
1. `git checkout main`
2. `git pull origin main` — DO NOT SKIP
3. `git checkout -b feature/forgot-password` — NEW branch

**DO NOT reuse old branches.**

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/models.py` — add reset token fields to User
- `backend/auth/routes.py` — add `/forgot-password` and `/reset-password` endpoints
- `backend/auth/schemas.py` — add request/response schemas
- `backend/auth/service.py` — add password reset logic
- `frontend/app/(auth)/forgot-password/page.tsx` — forgot password page (new)
- `frontend/app/(auth)/reset-password/page.tsx` — reset password page (new)
- `frontend/app/(auth)/login/page.tsx` — add "Forgot password?" link

**Do NOT touch:**
- Auth middleware (get_current_user)
- Existing login/register/verify-email endpoints
- Other models, routes, or frontend pages

---

## CONTEXT

**Problem:** User can't log in if they forget their password. No reset flow exists.

**Current state:**
- Email verification already works (Brevo via `send_email()` in `backend/email/service.py`)
- User model has: `verification_token`, `verification_expires_at` (same pattern we'll reuse)
- `settings.FRONTEND_URL` exists (used in verify URL)

**Flow to implement:**

```
1. User clicks "Forgot password?" on login page
2. Enters email → POST /auth/forgot-password
3. Backend: generate reset token, send email with link
4. User clicks link → /reset-password?token=xxx
5. User enters new password → POST /auth/reset-password
6. Backend: validate token, update password, clear token
7. User is redirected to /login
```

---

## WHAT TO DO

### Step 1: Add reset token fields to User model

**File: `backend/models.py`**

Find the User class and add two fields (after `verification_expires_at`):

```python
class User(Base):
    __tablename__ = "users"
    
    # ... existing fields ...
    
    # Password reset (added for FI-AUTH forgot-password)
    reset_password_token = Column(String(128), nullable=True, unique=True)
    reset_password_expires_at = Column(DateTime, nullable=True)
```

---

### Step 2: Add schemas

**File: `backend/auth/schemas.py`**

Add these schemas:

```python
class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ForgotPasswordResponse(BaseModel):
    message: str  # Always "If this email exists, you'll receive a link"

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8, max_length=128)

class ResetPasswordResponse(BaseModel):
    message: str
```

---

### Step 3: Add service functions

**File: `backend/auth/service.py`**

Add two functions:

```python
from datetime import datetime, timedelta, timezone
import uuid
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_reset_token(email: str, db: Session) -> str | None:
    """
    Generate reset token for user. Returns token or None if email not found.
    
    Always returns generic message (don't reveal if email exists).
    """
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return None  # Silently fail (security: don't reveal if email exists)
    
    token = uuid.uuid4().hex
    user.reset_password_token = token
    user.reset_password_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    db.commit()
    return token


def reset_password(token: str, new_password: str, db: Session) -> bool:
    """
    Validate reset token and update password.
    
    Returns True if successful, False if token invalid/expired.
    """
    now = datetime.now(timezone.utc)
    user = (
        db.query(User)
        .filter(
            User.reset_password_token == token,
            User.reset_password_expires_at >= now,
        )
        .first()
    )
    if not user:
        return False
    
    user.password_hash = pwd_context.hash(new_password)
    user.reset_password_token = None
    user.reset_password_expires_at = None
    db.commit()
    return True
```

---

### Step 4: Add endpoints to auth/routes.py

**File: `backend/auth/routes.py`**

Add imports at top:
```python
from backend.auth.schemas import (
    # ... existing imports ...
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
)
from backend.auth.service import (
    # ... existing imports ...
    create_reset_token,
    reset_password,
)
```

Add two new endpoints after the existing ones:

```python
@auth_router.post("/forgot-password", response_model=ForgotPasswordResponse)
@limiter.limit("3/hour")
def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    db: Annotated[Session, Depends(get_db)],
) -> ForgotPasswordResponse:
    """
    Request password reset email.
    
    Always returns same message (security: don't reveal if email exists).
    Rate limited: 3/hour to prevent email spam.
    """
    token = create_reset_token(body.email, db)
    
    if token:
        reset_url = f"{settings.FRONTEND_URL}/reset-password?token={token}"
        subject = "Reset your Chat9 password"
        body_text = (
            "Hi,\n\n"
            "You requested a password reset. Click the link below:\n\n"
            f"{reset_url}\n\n"
            "This link expires in 1 hour.\n\n"
            "If you didn't request this, you can safely ignore this email.\n"
        )
        try:
            send_email(to=body.email, subject=subject, body=body_text)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to send reset email: %s", e)
    
    # Always return same message (don't reveal if email exists)
    return ForgotPasswordResponse(
        message="If this email is registered, you'll receive a password reset link shortly."
    )


@auth_router.post("/reset-password", response_model=ResetPasswordResponse)
@limiter.limit("5/hour")
def reset_password_endpoint(
    request: Request,
    body: ResetPasswordRequest,
    db: Annotated[Session, Depends(get_db)],
) -> ResetPasswordResponse:
    """
    Reset password using token from email.
    
    Errors: 400 (invalid/expired token), 422 (password too short).
    """
    success = reset_password(body.token, body.new_password, db)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired reset token. Please request a new one.",
        )
    return ResetPasswordResponse(message="Password updated successfully. You can now log in.")
```

---

### Step 5: Create Forgot Password page

**File: `frontend/app/(auth)/forgot-password/page.tsx`** (new file)

```typescript
'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      const res = await fetch('/api/auth/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });

      if (!res.ok) {
        const data = await res.json();
        setError(data.detail || 'Something went wrong');
        return;
      }

      setSubmitted(true);
    } catch {
      setError('Network error. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  if (submitted) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="w-full max-w-md space-y-6 p-8 text-center">
          <h1 className="text-2xl font-bold">Check your email</h1>
          <p className="text-muted-foreground">
            If this email is registered, you&apos;ll receive a password reset link shortly.
          </p>
          <Link href="/login" className="text-primary underline">
            Back to login
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="w-full max-w-md space-y-6 p-8">
        <div className="space-y-2 text-center">
          <h1 className="text-2xl font-bold">Forgot password?</h1>
          <p className="text-muted-foreground">
            Enter your email and we&apos;ll send you a reset link.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}

          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? 'Sending...' : 'Send reset link'}
          </Button>
        </form>

        <p className="text-center text-sm">
          <Link href="/login" className="text-primary underline">
            Back to login
          </Link>
        </p>
      </div>
    </div>
  );
}
```

---

### Step 6: Create Reset Password page

**File: `frontend/app/(auth)/reset-password/page.tsx`** (new file)

```typescript
'use client';

import { useState } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

export default function ResetPasswordPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams.get('token');

  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);

  if (!token) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center">
          <p className="text-destructive">Invalid reset link.</p>
          <Link href="/forgot-password" className="text-primary underline">
            Request a new one
          </Link>
        </div>
      </div>
    );
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (password !== confirm) {
      setError('Passwords do not match');
      return;
    }
    if (password.length < 8) {
      setError('Password must be at least 8 characters');
      return;
    }

    setLoading(true);
    try {
      const res = await fetch('/api/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, new_password: password }),
      });

      if (!res.ok) {
        const data = await res.json();
        setError(data.detail || 'Invalid or expired link. Please request a new one.');
        return;
      }

      setSuccess(true);
      setTimeout(() => router.push('/login'), 2000);
    } catch {
      setError('Network error. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  if (success) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-center space-y-2">
          <h1 className="text-2xl font-bold">Password updated!</h1>
          <p className="text-muted-foreground">Redirecting to login...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="w-full max-w-md space-y-6 p-8">
        <div className="space-y-2 text-center">
          <h1 className="text-2xl font-bold">Set new password</h1>
          <p className="text-muted-foreground">Choose a strong password.</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="password">New password</Label>
            <Input
              id="password"
              type="password"
              placeholder="Min. 8 characters"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="confirm">Confirm password</Label>
            <Input
              id="confirm"
              type="password"
              placeholder="Repeat password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
            />
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}

          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? 'Updating...' : 'Update password'}
          </Button>
        </form>
      </div>
    </div>
  );
}
```

---

### Step 7: Add "Forgot password?" link to Login page

**File: `frontend/app/(auth)/login/page.tsx`**

Find the login form and add a link below the password field:

```typescript
{/* Add this after the password Input */}
<div className="text-right">
  <Link href="/forgot-password" className="text-sm text-muted-foreground underline">
    Forgot password?
  </Link>
</div>
```

---

## TESTING

Before pushing:

- [ ] Backend starts without errors
- [ ] `POST /auth/forgot-password` with valid email → sends email + returns generic message
- [ ] `POST /auth/forgot-password` with invalid email → returns same generic message (no leak)
- [ ] `POST /auth/reset-password` with valid token → password updated, returns 200
- [ ] `POST /auth/reset-password` with expired/invalid token → returns 400
- [ ] `POST /auth/reset-password` with password < 8 chars → returns 422
- [ ] Token can only be used once (cleared after use)
- [ ] Frontend: forgot-password page renders, submits, shows success state
- [ ] Frontend: reset-password page validates token, updates password, redirects to login
- [ ] Login page shows "Forgot password?" link
- [ ] All existing tests still pass (pytest)

**Manual flow test:**
```bash
# 1. Request reset
curl -X POST http://localhost:8000/auth/forgot-password \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com"}'

# 2. Check email for token, then:
curl -X POST http://localhost:8000/auth/reset-password \
  -H "Content-Type: application/json" \
  -d '{"token": "TOKEN_FROM_EMAIL", "new_password": "newpassword123"}'

# 3. Login with new password
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "password": "newpassword123"}'
```

---

## GIT PUSH

```bash
git add backend/models.py backend/auth/routes.py backend/auth/schemas.py backend/auth/service.py frontend/app/\(auth\)/forgot-password/page.tsx frontend/app/\(auth\)/reset-password/page.tsx frontend/app/\(auth\)/login/page.tsx

git commit -m "feat: add forgot password / password reset flow (FI-AUTH)

- POST /auth/forgot-password — sends reset email (rate limited 3/hour)
- POST /auth/reset-password — validates token, updates password
- Add reset_password_token + reset_password_expires_at to User model
- Frontend: /forgot-password and /reset-password pages
- Forgot password link on login page
- Generic response (security: no email enumeration)"

git push origin feature/forgot-password
```

---

## NOTES

- **Security:** `/forgot-password` always returns same message — never reveals if email is registered (prevents email enumeration attacks)
- **Token TTL:** 1 hour (balance between convenience and security)
- **Rate limiting:** 3/hour on forgot-password to prevent email spam
- **Single-use tokens:** token is cleared after successful reset
- **Pattern:** Reuses existing Brevo email + verification_token pattern from register flow
- **Migration needed:** Add `reset_password_token` + `reset_password_expires_at` columns to users table

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
[1-2 sentences: what was changed and why]

## Changes
- [file.py] — [what changed]
- [file.py] — [what changed]

## Testing
- [ ] Tests pass (pytest)
- [ ] Manual test: [specific scenario]

## Notes
[Any important context, limitations, or follow-up work]
```
